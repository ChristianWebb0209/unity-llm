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
    Note: we must enforce exact versions (not just "module is importable"),
    otherwise Colab's preinstalled packages can cause API mismatches like:
    `Trainer.__init__() got an unexpected keyword argument 'tokenizer'`.
    """
    required = {
        "peft": "peft==0.12.0",
        "accelerate": "accelerate==0.34.2",
        "datasets": "datasets==2.21.0",
        "transformers": "transformers==4.44.2",
        # 0.43.x depends on `triton.ops.matmul_perf_model` which newer Triton versions removed.
        # 0.45.1 includes the fix (removes the dependency on `triton.ops`).
        "bitsandbytes": "bitsandbytes==0.45.1",
        "sympy": "sympy==1.13.1",
    }
    missing_or_mismatched: List[str] = []
    for mod, pkg in required.items():
        # Avoid importing packages here: importing can cache incompatible versions
        # in `sys.modules` and keep using them even after `pip install`.
        desired_version = pkg.split("==", 1)[1] if "==" in pkg else None
        if desired_version is None:
            missing_or_mismatched.append(pkg)
            continue

        try:
            from importlib.metadata import PackageNotFoundError, version as installed_version  # type: ignore

            current_version = installed_version(mod)
        except PackageNotFoundError:
            missing_or_mismatched.append(pkg)
            continue
        except Exception:  # pragma: no cover
            # If metadata is unavailable, fall back to installing the pinned version.
            missing_or_mismatched.append(pkg)
            continue

        if current_version != desired_version:
            missing_or_mismatched.append(pkg)

    if not missing_or_mismatched:
        return
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--upgrade",
            *missing_or_mismatched,
        ],
        check=True,
    )

    # If these modules were already imported in the current runtime, they will
    # remain cached in sys.modules. Clear them so subsequent imports use the
    # pinned versions we just installed.
    for mod in required.keys():
        for name in list(sys.modules.keys()):
            if name == mod or name.startswith(mod + "."):
                sys.modules.pop(name, None)


_ensure_runtime_dependencies()

if "CUDA_VISIBLE_DEVICES" not in os.environ:
    # Ensure a stable single-GPU indexing for transformers/accelerate when using device_map.
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"

# Force single-process semantics so Accelerate always uses GPU index 0.
os.environ.setdefault("RANK", "0")
os.environ.setdefault("LOCAL_RANK", "0")
os.environ.setdefault("WORLD_SIZE", "1")
os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
os.environ.setdefault("MASTER_PORT", "29500")

def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd is not None else None, check=True)

import torch

if torch.cuda.is_available() and torch.cuda.device_count() > 0:
    # Accelerate's device checks assume training happens on GPU index 0.
    torch.cuda.set_device(0)

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

from datasets import Dataset, DatasetDict
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from huggingface_hub import snapshot_download


REPO_ROOT = Path(".").resolve()
DATA_ROOT = REPO_ROOT / "fine_tuning" / "data"
DATA_DIR = DATA_ROOT

COMPOSER_TRAIN = DATA_DIR / "train.jsonl"
COMPOSER_VAL = DATA_DIR / "val.jsonl"

BASE_MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"


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
    - clone/update repo into a fixed directory
    - chdir into the repo
    - mount Drive and set default output directories
    """
    if not Path("/content").exists():
        return

    repo_dir = Path("/content/unity-llm").resolve()

    if not repo_dir.exists():
        _run(["git", "clone", "https://github.com/ChristianWebb0209/unity-llm", str(repo_dir)])

    # If repo exists and has a remote, try to update; otherwise continue.
    try:
        _run(["git", "fetch", "origin"], cwd=repo_dir)
        _run(["git", "reset", "--hard", "origin/master"], cwd=repo_dir)
    except Exception:
        # Repo might have no origin configured or branch might differ.
        # Best-effort: attempt pull, then continue.
        try:
            _run(["git", "pull"], cwd=repo_dir)
        except Exception:
            pass

    os.chdir(repo_dir)
    _refresh_paths_from_cwd()

    try:
        from google.colab import drive  # type: ignore

        drive.mount("/content/drive", force_remount=False)
        drive_root = Path("/content/drive/MyDrive")
        base_dir = drive_root / "unity-composer-v3-runs"
        checkpoint_dir = base_dir / "checkpoints"
        output_adapter_dir = base_dir / "adapter"
        # Your drive's `models/` folder is one directory back in the root.
        model_cache_dir = drive_root / "models"
        base_model_local_dir = model_cache_dir / BASE_MODEL_ID.replace("/", "--")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        output_adapter_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        base_model_local_dir.mkdir(parents=True, exist_ok=True)

        # Hardcode Colab paths (no env lookups for directories).
        os.environ["CHECKPOINT_DIR"] = str(checkpoint_dir)
        os.environ["OUTPUT_ADAPTER_DIR"] = str(output_adapter_dir)
        os.environ["HF_HOME"] = str(model_cache_dir)
        os.environ["BASE_MODEL_LOCAL_DIR"] = str(base_model_local_dir)
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
    # Hardcoded Colab paths (avoid env lookups for directory selection).
    # Prefer the Drive-root `models/` folder first.
    model_cache_dir = Path("/content/drive/MyDrive/models")
    base_model_local_dir = model_cache_dir / BASE_MODEL_ID.replace("/", "--")
    cache_dir = str(model_cache_dir)

    local_ready = base_model_local_dir.is_dir() and (base_model_local_dir / "config.json").exists()
    if not local_ready:
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

    cuda_available = torch.cuda.is_available() and torch.cuda.device_count() > 0
    print(f"CUDA available: {cuda_available} (device_count={torch.cuda.device_count()})")
    if not cuda_available:
        raise SystemExit(
            "No CUDA GPU detected in this Colab runtime. QLoRA (4-bit bitsandbytes) requires a GPU. "
            "Switch Colab Runtime to 'GPU' and restart, then re-run this script."
        )

    tokenizer = AutoTokenizer.from_pretrained(
        model_source,
        trust_remote_code=True,
        cache_dir=cache_dir,
        local_files_only=local_ready,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # `flash_attention_2` is Triton-backed and can fail with driver issues.
    # Use `sdpa` to avoid Triton initialization issues across Colab runtimes.
    attn_impl = "sdpa"

    lora_config = LoraConfig(
        r=4,
        lora_alpha=8,
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
        if cuda_available:
            # QLoRA 4-bit config for Colab (requires CUDA).
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
            model_kwargs: Dict[str, Any] = dict(
                quantization_config=bnb_config,
                trust_remote_code=True,
                attn_implementation=attn_impl,
                low_cpu_mem_usage=True,
            )
            if have_accelerate:
                # Explicitly place the quantized model on the active CUDA device
                # (Accelerate's guard expects the quantized model and the training
                # device to line up).
                current_device = torch.cuda.current_device()
                device_str = f"cuda:{current_device}"
                model_kwargs["device_map"] = {"": current_device}
                # Avoid CPU/disk offload for quantized weights (RAM spikes).
                model_kwargs["max_memory"] = {
                    device_str: "13GiB",
                    "cpu": "0GiB",
                }

        model = AutoModelForCausalLM.from_pretrained(
            model_source,
            **model_kwargs,
            cache_dir=cache_dir,
            local_files_only=local_ready,
        )
    except (RuntimeError, ModuleNotFoundError, OSError, ValueError, ImportError):
        if not cuda_available:
            raise
        # GPU fallback: load bf16 without 4-bit if bitsandbytes/4bit fails.
        fallback_kwargs: Dict[str, Any] = dict(
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            trust_remote_code=True,
            attn_implementation=attn_impl,
        )
        if have_accelerate and cuda_available:
            fallback_kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(
            model_source,
            **fallback_kwargs,
            cache_dir=cache_dir,
            local_files_only=local_ready,
        )

    if cuda_available:
        # PEFT's prepare step can spike memory (casts some params to fp32).
        # For this environment, skip by default to prevent OOM/RAM crashes.
        # Set PREPARE_KBIT_TRAINING=1 to opt back in.
        if os.environ.get("PREPARE_KBIT_TRAINING", "0").strip() in {"1", "true", "yes", "y"}:
            torch.cuda.empty_cache()
            model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, lora_config)

    # Required for gradient checkpointing + LoRA
    model.config.use_cache = False
    # Avoid DDP issues with re-entrant checkpointing (PyTorch>=2.5 warns/behaves differently).
    # HF supports passing `gradient_checkpointing_kwargs`.
    try:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    except TypeError:
        # Fallback for older HF versions.
        model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    return tokenizer, model


def build_trainer(tokenizer: AutoTokenizer, model: AutoModelForCausalLM, dataset: DatasetDict) -> Trainer:
    output_dir = "./unity-composer-v1-lora"

    using_cuda = torch.cuda.is_available() and torch.cuda.device_count() > 0
    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=4,
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
        fp16=using_cuda,
        gradient_checkpointing=False,
        # Lower VRAM usage for optimizer states with QLoRA.
        optim="paged_adamw_8bit",
        report_to="none",
    )

    # Tokenize ahead of time so we don't rely on TRL's SFTTrainer wrapper.
    # Lower sequence length to reduce activation memory.
    max_seq_length = 256

    def _tokenize_batch(batch: Dict[str, Any]) -> Dict[str, Any]:
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=max_seq_length,
            padding=False,
            return_attention_mask=True,
        )

    tokenized_train = dataset["train"].map(
        _tokenize_batch,
        batched=True,
        remove_columns=dataset["train"].column_names,
    )
    tokenized_val = dataset["val"].map(
        _tokenize_batch,
        batched=True,
        remove_columns=dataset["val"].column_names,
    )

    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    return Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_train,
        eval_dataset=tokenized_val,
        data_collator=data_collator,
    )


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
    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
        # Make sure the active CUDA device matches what the quantized model expects.
        torch.cuda.set_device(torch.cuda.current_device())
    trainer = build_trainer(tokenizer, model, dataset)

    trainer.train()

    # Save adapter into a fixed folder by default.
    save_dir = "unity-composer-v1-adapter"
    trainer.model.save_pretrained(save_dir)
    tokenizer.save_pretrained(save_dir)
    print(f"Saved adapter to: {save_dir}")


if __name__ == "__main__":
    main()

