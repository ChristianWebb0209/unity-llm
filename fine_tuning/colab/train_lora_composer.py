"""
Colab training script for Composer LoRA/QLoRA fine-tuning.

Dataset selection:
- Scans fine_tuning/data for folders named composer_vN (N = integer).
- Selects the highest N automatically.
- Loads train.jsonl and val.jsonl from that folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import re
from typing import Any, Dict, List


def _ensure_runtime_dependencies() -> None:
    """
    Ensure required training dependencies exist in Colab/runtime.
    Keeps versions pinned to a known-compatible set.
    """
    required = {
        "peft": "peft==0.12.0",
        "trl": "trl==0.9.6",
        "accelerate": "accelerate==0.34.2",
        "datasets": "datasets==2.21.0",
        "transformers": "transformers==4.44.2",
        "bitsandbytes": "bitsandbytes==0.43.3",
        "sympy": "sympy==1.13.1",
    }
    missing: List[str] = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    if not missing:
        return
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            *missing,
        ],
        check=True,
    )


_ensure_runtime_dependencies()

def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd is not None else None, check=True)

import torch

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

from datasets import Dataset, DatasetDict
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer
from huggingface_hub import snapshot_download


REPO_ROOT = Path(".").resolve()
DATA_ROOT = REPO_ROOT / "fine_tuning" / "data"
DATA_DIR = DATA_ROOT

COMPOSER_TRAIN = DATA_DIR / "train.jsonl"
COMPOSER_VAL = DATA_DIR / "val.jsonl"

BASE_MODEL_ID = os.environ.get("BASE_MODEL_ID", "Qwen/Qwen2.5-Coder-7B-Instruct")


def _refresh_paths_from_cwd() -> None:
    global REPO_ROOT, DATA_ROOT, DATA_DIR, COMPOSER_TRAIN, COMPOSER_VAL
    REPO_ROOT = Path(".").resolve()
    DATA_ROOT = REPO_ROOT / "fine_tuning" / "data"
    DATA_DIR = DATA_ROOT
    COMPOSER_TRAIN = DATA_DIR / "train.jsonl"
    COMPOSER_VAL = DATA_DIR / "val.jsonl"


def _maybe_setup_colab_repo_and_drive() -> None:
    """
    In Colab:
    - optionally clone/update repo
    - chdir into a Colab repo directory
    - mount Drive and set default output directories
    """
    if not Path("/content").exists():
        return

    repo_dir = Path(os.environ.get("COLAB_REPO_DIR", "/content/unity-llm")).resolve()

    if not repo_dir.exists():
        clone_url = os.environ.get("REPO_CLONE_URL", "").strip()
        if not clone_url:
            raise SystemExit(
                f"Colab repo not found at {repo_dir}. Set REPO_CLONE_URL to enable auto-clone, "
                "or ensure the repo is already present in the runtime."
            )
        _run(["git", "clone", clone_url, str(repo_dir)])

    # If repo exists and has a remote, try to update; otherwise continue.
    try:
        _run(["git", "fetch", "origin"], cwd=repo_dir)
        _run(["git", "reset", "--hard", "origin/master"], cwd=repo_dir)
    except Exception:
        pass

    os.chdir(repo_dir)
    _refresh_paths_from_cwd()

    try:
        from google.colab import drive  # type: ignore

        drive.mount("/content/drive", force_remount=False)
        base_dir = Path(os.environ.get("DRIVE_BASE_DIR", "/content/drive/MyDrive/unity-composer-v3-runs"))
        checkpoint_dir = base_dir / "checkpoints"
        output_adapter_dir = base_dir / "adapter"
        model_cache_dir = base_dir / "models"
        base_model_local_dir = model_cache_dir / BASE_MODEL_ID.replace("/", "--")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        output_adapter_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        base_model_local_dir.mkdir(parents=True, exist_ok=True)

        os.environ.setdefault("CHECKPOINT_DIR", str(checkpoint_dir))
        os.environ.setdefault("OUTPUT_ADAPTER_DIR", str(output_adapter_dir))
        os.environ.setdefault("HF_HOME", str(model_cache_dir))
        os.environ.setdefault("BASE_MODEL_LOCAL_DIR", str(base_model_local_dir))
        print("CHECKPOINT_DIR =", os.environ["CHECKPOINT_DIR"])
        print("OUTPUT_ADAPTER_DIR =", os.environ["OUTPUT_ADAPTER_DIR"])
        print("BASE_MODEL_LOCAL_DIR =", os.environ["BASE_MODEL_LOCAL_DIR"])
    except Exception as e:
        print(f"Drive setup skipped: {e}")


def _resolve_latest_composer_dataset_dir() -> Path:
    """
    Return the highest-version composer_vN directory under fine_tuning/data.
    """
    if not DATA_ROOT.exists():
        raise FileNotFoundError(f"Data root not found: {DATA_ROOT}")

    pattern = re.compile(r"^composer_v(\d+)$")
    candidates: List[tuple[int, Path]] = []
    for child in DATA_ROOT.iterdir():
        if not child.is_dir():
            continue
        m = pattern.match(child.name)
        if not m:
            continue
        candidates.append((int(m.group(1)), child))

    if not candidates:
        raise FileNotFoundError(f"No composer_vN dataset directories found under: {DATA_ROOT}")

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def load_jsonl_dataset(path: Path) -> Dataset:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    if not records:
        raise ValueError(f"Dataset is empty: {path}")
    return Dataset.from_list(records)


def format_messages_example(example: Dict[str, Any]) -> str:
    msgs = example.get("messages") or []
    parts: List[str] = []
    for m in msgs:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"<system>{content}</system>")
        elif role == "user":
            parts.append(f"<user>{content}</user>")
        elif role == "assistant":
            parts.append(f"<assistant>{content}</assistant>")
        else:
            parts.append(str(content))
    return "\n".join(parts)


def load_tokenizer_and_model() -> tuple[AutoTokenizer, AutoModelForCausalLM]:
    base_model_local_dir = Path(os.environ.get("BASE_MODEL_LOCAL_DIR", "")).expanduser()
    hf_home = os.environ.get("HF_HOME", "")
    cache_dir = hf_home if hf_home else None

    local_ready = base_model_local_dir.is_dir() and (base_model_local_dir / "config.json").exists()
    if not local_ready:
        if not base_model_local_dir:
            # Fallback local dir in current workspace if BASE_MODEL_LOCAL_DIR is unset.
            base_model_local_dir = REPO_ROOT / ".cache" / "models" / BASE_MODEL_ID.replace("/", "--")
            base_model_local_dir.mkdir(parents=True, exist_ok=True)
        print(f"Base model not found locally, downloading to: {base_model_local_dir}")
        snapshot_download(
            repo_id=BASE_MODEL_ID,
            cache_dir=cache_dir,
            local_dir=str(base_model_local_dir),
            local_dir_use_symlinks=False,
        )
        local_ready = (base_model_local_dir / "config.json").exists()

    model_source = str(base_model_local_dir) if local_ready else BASE_MODEL_ID
    print(f"Using model source: {model_source}")

    tokenizer = AutoTokenizer.from_pretrained(
        model_source,
        trust_remote_code=True,
        cache_dir=cache_dir,
        local_files_only=local_ready,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    attn_impl = "flash_attention_2" if torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8 else "sdpa"

    # QLoRA 4-bit config for Colab
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )

    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    try:
        import accelerate  # noqa: F401
        have_accelerate = True
    except Exception:
        have_accelerate = False

    try:
        model_kwargs: Dict[str, Any] = dict(
            quantization_config=bnb_config,
            trust_remote_code=True,
            attn_implementation=attn_impl,
        )
        if have_accelerate:
            model_kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(
            model_source,
            **model_kwargs,
            cache_dir=cache_dir,
            local_files_only=local_ready,
        )
    except (RuntimeError, ModuleNotFoundError, OSError, ValueError, ImportError):
        # Fallback: load bf16 without 4-bit if bitsandbytes/4bit fails.
        fallback_kwargs: Dict[str, Any] = dict(
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            trust_remote_code=True,
            attn_implementation=attn_impl,
        )
        if have_accelerate:
            fallback_kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(
            model_source,
            **fallback_kwargs,
            cache_dir=cache_dir,
            local_files_only=local_ready,
        )

    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, lora_config)

    # Required for gradient checkpointing + LoRA
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    return tokenizer, model


def build_trainer(tokenizer: AutoTokenizer, model: AutoModelForCausalLM, dataset: DatasetDict) -> SFTTrainer:
    output_dir = os.environ.get("CHECKPOINT_DIR", "./unity-composer-v1-lora")

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=3,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=2,
        num_train_epochs=1,
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=40,
        eval_strategy="no",
        save_strategy="steps",
        save_steps=700,
        save_total_limit=3,
        bf16=False,
        fp16=True,
        gradient_checkpointing=True,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset["train"],
        eval_dataset=dataset["val"],
        dataset_text_field="text",
        max_seq_length=1024,
        args=training_args,
    )
    return trainer


def main() -> None:
    _maybe_setup_colab_repo_and_drive()

    global DATA_DIR, COMPOSER_TRAIN, COMPOSER_VAL
    DATA_DIR = _resolve_latest_composer_dataset_dir()
    COMPOSER_TRAIN = DATA_DIR / "train.jsonl"
    COMPOSER_VAL = DATA_DIR / "val.jsonl"
    print(f"Using dataset directory: {DATA_DIR}")

    if not COMPOSER_TRAIN.exists() or not COMPOSER_VAL.exists():
        raise SystemExit(
            f"Missing dataset files. Expected:\n- {COMPOSER_TRAIN}\n- {COMPOSER_VAL}"
        )

    train_ds = load_jsonl_dataset(COMPOSER_TRAIN)
    val_ds = load_jsonl_dataset(COMPOSER_VAL)

    train_ds = train_ds.map(lambda ex: {"text": format_messages_example(ex)}, remove_columns=train_ds.column_names)
    val_ds = val_ds.map(lambda ex: {"text": format_messages_example(ex)}, remove_columns=val_ds.column_names)

    dataset = DatasetDict({"train": train_ds, "val": val_ds})
    tokenizer, model = load_tokenizer_and_model()
    trainer = build_trainer(tokenizer, model, dataset)

    trainer.train()

    save_dir = os.environ.get("OUTPUT_ADAPTER_DIR", "unity-composer-v1-adapter")
    trainer.model.save_pretrained(save_dir)
    tokenizer.save_pretrained(save_dir)
    print(f"Saved adapter to: {save_dir}")


if __name__ == "__main__":
    main()

