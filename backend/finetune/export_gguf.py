"""Merges the trained LoRA adapter into the base model and converts it to a 4-bit GGUF for Ollama.

The merge streams one tensor at a time (W + scale * B @ A for every adapted weight), so it
needs about one shard of memory rather than the whole 8 GB model - it fits in Docker Desktop's
default memory limit. Runs where train_lora.py ran (it needs torch and llama.cpp's converter).

    python finetune/export_gguf.py            # -> finetune/out/custodian-nemotron-Q4_K_M.gguf
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def merge(base_dir: Path, adapter_dir: Path, out_dir: Path) -> None:
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file

    config = json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8"))
    scale = config["lora_alpha"] / config["r"]
    lora: dict[str, dict[str, torch.Tensor]] = {}
    with safe_open(adapter_dir / "adapter_model.safetensors", framework="pt") as f:
        for key in f.keys():
            # base_model.model.<module>.lora_A.weight -> <module>.weight
            module, part = key.removeprefix("base_model.model.").rsplit(".lora_", 1)
            lora.setdefault(f"{module}.weight", {})[part.split(".")[0]] = f.get_tensor(key).float()

    out_dir.mkdir(parents=True, exist_ok=True)
    merged, seen = 0, set()
    part: dict[str, torch.Tensor] = {}
    part_bytes, parts, weight_map = 0, [], {}

    def flush() -> None:
        nonlocal part, part_bytes
        if part:
            name = f"part-{len(parts) + 1:05d}.safetensors"
            save_file(part, out_dir / name, metadata={"format": "pt"})
            parts.append(name)
            weight_map.update({key: name for key in part})
            print(f"  {name}")
        part, part_bytes = {}, 0

    # The base ships as one 8 GB file; reading it tensor by tensor and writing 2 GB parts
    # keeps memory near 2 GB.
    for shard in sorted(base_dir.glob("*.safetensors")):
        with safe_open(shard, framework="pt") as f:
            for key in f.keys():
                tensor = f.get_tensor(key)
                if key in lora:
                    delta = (lora[key]["B"] @ lora[key]["A"]) * scale
                    tensor = (tensor.float() + delta).to(tensor.dtype)
                    merged += 1
                seen.add(key)
                part[key] = tensor
                part_bytes += tensor.numel() * tensor.element_size()
                if part_bytes >= 2 * 1024**3:
                    flush()
    flush()
    missing = set(lora) - seen
    if missing:
        raise SystemExit(f"adapter weights with no base tensor (wrong base model?): {sorted(missing)[:3]}")
    (out_dir / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {}, "weight_map": weight_map}, indent=1), encoding="utf-8"
    )
    for extra in base_dir.iterdir():  # config, tokenizer, custom modelling code
        if extra.suffix != ".safetensors" and extra.name != "model.safetensors.index.json" and extra.is_file():
            shutil.copy2(extra, out_dir / extra.name)
    print(f"merged {merged} adapted weights (scale {scale})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--llama-cpp", type=Path, default=Path("/opt/llama.cpp"))
    parser.add_argument("--quantize", default="Q4_K_M", help="llama.cpp quantization (Q4_K_M is the base model's)")
    args = parser.parse_args()

    adapter = args.out / "adapter"
    training = json.loads((adapter / "training.json").read_text(encoding="utf-8"))
    from huggingface_hub import snapshot_download

    base = Path(snapshot_download(training["base_model"]))
    print(f"merging {adapter} into {training['base_model']}")
    merge(base, adapter, args.out / "merged")

    gguf = args.out / "custodian-nemotron-f16.gguf"
    subprocess.run(
        [sys.executable, str(args.llama_cpp / "convert_hf_to_gguf.py"), str(args.out / "merged"),
         "--outfile", str(gguf), "--outtype", "f16"],
        check=True,
    )
    shutil.rmtree(args.out / "merged")  # 8 GB; the GGUF is what Ollama needs
    # Ollama quantizes only safetensors imports, so shrink the GGUF here (8 GB -> ~2.8 GB).
    small = args.out / f"custodian-nemotron-{args.quantize}.gguf"
    subprocess.run([str(args.llama_cpp / "build" / "bin" / "llama-quantize"), str(gguf), str(small), args.quantize], check=True)
    gguf.unlink()
    print(f"wrote {small}; next, on the host: python finetune/install_ollama.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
