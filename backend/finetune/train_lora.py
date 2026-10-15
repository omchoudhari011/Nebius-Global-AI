"""LoRA fine-tuning of Nemotron 3 Nano 4B on the dataset from make_dataset.py.

The loss covers only the assistant turns - the tool calls the model makes and its replies -
never the system prompt, the user's words or the tool results, so it learns Custodian's
behaviour without memorising the prompts around it.

Needs a Linux CUDA GPU: the model is a hybrid Mamba-Transformer whose kernels (mamba-ssm,
causal-conv1d) have no Windows builds. On Windows, run it through Docker with
finetune/run_finetune.ps1. With 8 GB of VRAM use --qlora (4-bit base model).

    python finetune/train_lora.py --check                    # render and mask the data only (no GPU)
    python finetune/train_lora.py --qlora --epochs 2          # train -> finetune/out/adapter
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
IGNORE = -100


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _for_template(messages: list[dict]) -> list[dict]:
    """Hugging Face chat templates expect tool-call arguments as objects, not JSON text."""
    out = []
    for message in messages:
        message = dict(message)
        if message.get("tool_calls"):
            message["tool_calls"] = [
                {**call, "function": {**call["function"], "arguments": json.loads(call["function"]["arguments"])}}
                for call in message["tool_calls"]
            ]
            message["content"] = message.get("content") or ""
        out.append(message)
    return out


def encode(tokenizer, row: dict, max_len: int) -> dict | None:  # noqa: ANN001 - a transformers tokenizer
    """input_ids with labels set on the assistant turns only (IGNORE elsewhere). Each
    assistant turn's tokens are what the template adds between the prompt up to that turn
    (with the generation prompt) and the conversation including it."""
    messages, tools = _for_template(row["messages"]), row.get("tools") or None

    def render(upto: int, generation_prompt: bool) -> list[int]:
        return tokenizer.apply_chat_template(
            messages[:upto], tools=tools, tokenize=True, add_generation_prompt=generation_prompt
        )

    full = render(len(messages), False)
    labels = [IGNORE] * len(full)
    for i, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        start, end = len(render(i, True)), len(render(i + 1, False))
        if full[:end] != render(i + 1, False):  # the template must render prefixes consistently
            return None
        labels[start:end] = full[start:end]
    if len(full) > max_len or all(label == IGNORE for label in labels):
        return None
    return {"input_ids": full, "labels": labels}


def use_triton_scan(model) -> int:  # noqa: ANN001 - a transformers model
    """Train the Mamba layers with mamba-ssm's Triton scan instead of the pure-PyTorch one.

    The model's fast path also needs causal-conv1d's CUDA kernels, which have no build here
    (none for Windows, and none prebuilt for every GPU), so without them it falls back to a
    PyTorch scan that builds a (chunks x chunk x chunk x heads x state) tensor - over 20 GB
    for one conversation. This is the model's own kernel branch, with the short causal
    convolution done by torch's conv1d. Used whenever there's no generation cache (training and
    the validation pass); generation keeps the original code. Returns how many layers it covers."""
    import sys

    import torch

    mixers = [m for m in model.modules() if type(m).__name__ == "NemotronHMamba2Mixer"]
    if not mixers:
        return 0
    cls = type(mixers[0])
    code = sys.modules[cls.__module__]
    if getattr(code, "mamba_chunk_scan_combined", None) is None:
        raise SystemExit("mamba-ssm's Triton kernels aren't importable; see finetune/Dockerfile")
    original = cls.forward

    def forward(self, hidden_states, cache_params=None, cache_position=None, attention_mask=None):  # noqa: ANN001, ANN202
        if cache_params is not None:  # generating: the original code handles the cache
            return original(self, hidden_states, cache_params, cache_position, attention_mask)
        dtype = hidden_states.dtype
        hidden_states = code.apply_mask_to_padding_states(hidden_states, attention_mask)
        projected = self.in_proj(hidden_states)
        batch, length, _ = hidden_states.shape
        group_state = self.n_groups * self.ssm_state_size
        d_mlp = (projected.shape[-1] - 2 * self.intermediate_size - 2 * group_state - self.num_heads) // 2
        _, _, gate, xbc, dt = projected.split(
            [d_mlp, d_mlp, self.intermediate_size, self.conv_dim, self.num_heads], dim=-1
        )
        conv = self.conv1d(xbc.transpose(1, 2).to(self.conv1d.weight.dtype))[..., :length]
        xbc = code.apply_mask_to_padding_states(self.act(conv).transpose(1, 2).to(dtype), attention_mask)
        x, b, c = torch.split(xbc, [self.intermediate_size, group_state, group_state], dim=-1)
        limits = {} if self.time_step_limit == (0.0, float("inf")) else {"dt_limit": self.time_step_limit}
        y = code.mamba_chunk_scan_combined(
            x.view(batch, length, -1, self.head_dim), dt, -torch.exp(self.A_log.float()),
            b.view(batch, length, self.n_groups, -1), c.view(batch, length, self.n_groups, -1),
            chunk_size=self.chunk_size, D=self.D, z=None, seq_idx=None, return_final_states=False,
            dt_bias=self.dt_bias, dt_softplus=True, **limits,
        )
        y = self.norm(y.view(batch, length, -1), gate)
        return self.out_proj(y.to(dtype))

    cls.forward = forward
    return len(mixers)


def check(args: argparse.Namespace) -> int:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    for split in ("train", "valid"):
        rows = load_rows(args.data / f"{split}.jsonl")
        encoded = [encode(tokenizer, row, args.max_len) for row in rows]
        kept = [e for e in encoded if e]
        lengths = sorted(len(e["input_ids"]) for e in kept)
        trained = sum(sum(label != IGNORE for label in e["labels"]) for e in kept)
        print(
            f"{split}: {len(kept)}/{len(rows)} conversations usable; tokens per conversation median "
            f"{lengths[len(lengths) // 2] if lengths else 0}, max {lengths[-1] if lengths else 0}; "
            f"{trained:,} of {sum(lengths):,} tokens trained on"
        )
    sample = next(e for e in (encode(tokenizer, r, args.max_len) for r in load_rows(args.data / "train.jsonl")) if e)
    shown = [t if l != IGNORE else None for t, l in zip(sample["input_ids"], sample["labels"])]
    spans, current = [], []
    for token in shown + [None]:
        if token is None and current:
            spans.append(tokenizer.decode(current))
            current = []
        elif token is not None:
            current.append(token)
    print("\nWhat the model is trained to produce in one example:")
    for span in spans:
        print("  >>>", span.replace("\n", "\\n")[:300])
    return 0


def train(args: argparse.Namespace) -> int:
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    def dataset(split: str) -> list[dict]:
        return [e for e in (encode(tokenizer, r, args.max_len) for r in load_rows(args.data / f"{split}.jsonl")) if e]

    train_set, valid_set = dataset("train"), dataset("valid")
    print(f"{len(train_set)} training and {len(valid_set)} validation conversations")

    quantization = (
        BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                           bnb_4bit_use_double_quant=True)
        if args.qlora
        else None
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, torch_dtype=torch.bfloat16, trust_remote_code=True, quantization_config=quantization,
        device_map={"": 0},
    )
    if args.qlora:
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False}
        )
    else:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.config.use_cache = False
    print(f"Triton scan for {use_triton_scan(model)} Mamba layers")
    model = get_peft_model(
        model,
        LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, target_modules=args.targets.split(","),
                   task_type="CAUSAL_LM"),
    )
    model.print_trainable_parameters()

    def collate(batch: list[dict]) -> dict:
        width = max(len(e["input_ids"]) for e in batch)
        pad = tokenizer.pad_token_id
        return {
            "input_ids": torch.tensor([e["input_ids"] + [pad] * (width - len(e["input_ids"])) for e in batch]),
            "labels": torch.tensor([e["labels"] + [IGNORE] * (width - len(e["labels"])) for e in batch]),
            "attention_mask": torch.tensor([[1] * len(e["input_ids"]) + [0] * (width - len(e["input_ids"])) for e in batch]),
        }

    class SparseLossTrainer(Trainer):
        """Applies the output layer only where a label is trained on. The model's own loss
        projects every position onto the 131k-token vocabulary in float32 - about 1 GB per
        conversation, plus its gradient - although only ~3% of positions count."""

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):  # noqa: ANN001, ANN201
            causal_lm = model.get_base_model()
            hidden = causal_lm.backbone(input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"])[0]
            targets = inputs["labels"][:, 1:]
            keep = targets != IGNORE
            logits = causal_lm.lm_head(hidden[:, :-1][keep].to(causal_lm.lm_head.weight.dtype)).float()
            loss = torch.nn.functional.cross_entropy(logits, targets[keep], reduction="sum")
            loss = loss / (num_items_in_batch if num_items_in_batch is not None else keep.sum())
            return (loss, {"logits": logits}) if return_outputs else loss

        def prediction_step(self, model, inputs, prediction_loss_only, ignore_keys=None):  # noqa: ANN001, ANN201
            with torch.no_grad():
                return self.compute_loss(model, inputs).detach(), None, None

    steps_per_epoch = math.ceil(len(train_set) / (args.batch * args.accumulate))
    trainer = SparseLossTrainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(args.out / "checkpoints"),
            per_device_train_batch_size=args.batch,
            per_device_eval_batch_size=args.batch,
            gradient_accumulation_steps=args.accumulate,
            num_train_epochs=args.epochs,
            max_steps=args.max_steps or -1,
            learning_rate=args.lr,
            lr_scheduler_type="cosine",
            warmup_steps=max(1, steps_per_epoch // 10),
            logging_steps=5,
            # Checked every quarter epoch, and the best checkpoint (lowest validation loss) is
            # what's saved: a hybrid Mamba model can turn unstable late in a run, and the last
            # step is then worse than an earlier one.
            eval_strategy="steps",
            save_strategy="steps",
            eval_steps=max(1, steps_per_epoch // 4),
            save_steps=max(1, steps_per_epoch // 4),
            save_total_limit=2,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            bf16=True,
            optim="paged_adamw_8bit" if args.qlora else "adamw_torch",
            max_grad_norm=args.max_grad_norm,
            report_to=[],
            remove_unused_columns=False,
        ),
        train_dataset=train_set,
        eval_dataset=valid_set,
        data_collator=collate,
    )
    trainer.train()
    metrics = trainer.evaluate()
    print(f"validation loss {metrics['eval_loss']:.4f}")
    (args.out / "adapter").mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out / "adapter")
    tokenizer.save_pretrained(args.out / "adapter")
    (args.out / "adapter" / "training.json").write_text(
        json.dumps({"base_model": args.base_model, "eval_loss": metrics["eval_loss"], **{k: str(v) for k, v in vars(args).items()}},
                   indent=2),
        encoding="utf-8",
    )
    print(f"adapter saved to {args.out / 'adapter'}; next: python finetune/export_gguf.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only render and mask the data with the tokenizer")
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--data", type=Path, default=HERE / "data")
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--qlora", action="store_true", help="load the base model in 4 bits (for ~8 GB GPUs)")
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--rank", type=int, default=16)
    # Attention and MLP only. Adapting the Mamba mixers (in_proj, whose output goes through an
    # exponential, and out_proj) made gradients spike past 10^6 and undid the learning (README).
    parser.add_argument("--targets", default="q_proj,k_proj,v_proj,o_proj,up_proj,down_proj",
                        help="comma-separated layer names to adapt")
    parser.add_argument("--max-grad-norm", type=float, default=0.3)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accumulate", type=int, default=8)
    parser.add_argument("--max-len", type=int, default=4096)
    parser.add_argument("--max-steps", type=int, default=0, help="stop after this many steps (a smoke test)")
    args = parser.parse_args()
    return check(args) if args.check else train(args)


if __name__ == "__main__":
    raise SystemExit(main())
