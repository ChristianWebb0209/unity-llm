# Manual Vast.ai (or any GPU box) — LoRA server

There is **no automation** here. You start the machine, upload files, and run one script.

## 1. Start a container

Use the Vast (or RunPod, etc.) UI: pick a GPU image with **Python 3** and **CUDA** (e.g. a PyTorch CUDA image). **Expose a port** (e.g. `8000`) if you want HTTP from outside.

## 2. Folders on the instance

```bash
mkdir -p /workspace/adapter
```

(Any path is fine; set `ADAPTER_DIR` if you use something else.)

## 3. Upload the LoRA adapter

Copy your adapter directory (must include `adapter_config.json` and `adapter_model.safetensors` or `adapter_model.bin`) into `/workspace/adapter/` (or your `ADAPTER_DIR`).

## 4. Upload `serve_lora.py`

Copy this file from the repo:

`fine_tuning/scripts/vastai/serve_lora.py`

Example on the instance: `/workspace/serve_lora.py`

## 5. Run

Optional env (defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `BASE_MODEL_ID` | `Qwen/Qwen2.5-Coder-7B-Instruct` | Hugging Face repo to download if no local base |
| `BASE_MODEL_LOCAL_DIR` | `/workspace/models/<slug-of-BASE_MODEL_ID>` | Where the full base snapshot is stored |
| `ADAPTER_DIR` | `/workspace/adapter` | Your LoRA files |
| `HF_HOME` | `/workspace/.cache/huggingface` | HF cache |
| `PORT` | `8000` | HTTP port |
| `HOST` | `0.0.0.0` | Bind address |
| `HF_TOKEN` | _(unset)_ | If the base model needs a token |

```bash
export ADAPTER_DIR=/workspace/adapter
# export HF_TOKEN=hf_...   # if required
python /workspace/serve_lora.py
```

On first run the script **installs pip deps**, then **downloads the base model from Hugging Face** if `BASE_MODEL_LOCAL_DIR` has no `config.json`, then loads the adapter and serves **OpenAI-compatible** `POST /v1/chat/completions`. **`GET /docs`** is Swagger.

## 6. Point your app at it

Set your tunnel or public URL as OpenAI base, e.g. `https://your-host/v1`, and use the same model name your client sends (the server accepts any `model` string for routing compatibility).

Repo `.env` often uses `VASTAI_BASE_URL` for the RAG/composer client.
