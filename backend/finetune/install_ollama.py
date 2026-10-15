"""Installs the fine-tuned GGUF as an Ollama model, with the base model's chat template.

The template and stop settings are copied from the base model in your Ollama
(nemotron-3-nano:4b), so tool calling works exactly as before; only the weights change.
export_gguf.py already quantized it to q4_K_M, the same size as the base model.

    python finetune/install_ollama.py                      # -> ollama model "custodian-nemotron"
    python finetune/install_ollama.py --name my-model
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def base_modelfile(host: str, base: str) -> str:
    request = urllib.request.Request(
        f"{host}/api/show", data=json.dumps({"model": base}).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["modelfile"]


def modelfile_for(gguf: Path, base: str) -> str:
    """The base Modelfile with its FROM line pointing at the new weights."""
    lines = [f"FROM {gguf.resolve()}"]
    for line in base.splitlines():
        if line.startswith("FROM ") or line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gguf", type=Path, default=HERE / "out" / "custodian-nemotron-Q4_K_M.gguf")
    parser.add_argument("--base", default="nemotron-3-nano:4b")
    parser.add_argument("--name", default="custodian-nemotron")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    args = parser.parse_args()

    if not args.gguf.exists():
        raise SystemExit(f"{args.gguf} not found - run train_lora.py and export_gguf.py first")
    modelfile = HERE / "out" / "Modelfile"
    modelfile.write_text(modelfile_for(args.gguf, base_modelfile(args.host, args.base)), encoding="utf-8")
    subprocess.run(["ollama", "create", args.name, "-f", str(modelfile)], check=True)
    print(f"\nInstalled. Try it: set NEBIUS_MODEL_ID={args.name} in backend/.env and restart Custodian,")
    print("or compare it with the base model first: python finetune/compare.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
