## Godot RAG Service (Skeleton)

This folder contains a minimal RAG backend for the Godot AI assistant. For now it
only exposes a stubbed `/health` and `/query` endpoint, but the API surface is
intended to remain stable as you plug in real retrieval and LLM calls.

### Layout

- `app/main.py` – FastAPI entrypoint with:
  - `GET /health` – health check
  - `POST /query` – main RAG endpoint (currently returns a placeholder answer)
- `requirements.txt` – Python dependencies

### Running the service

From the `rag_service/` folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once running:

- `http://127.0.0.1:8000/health` should return `{"status": "ok"}`.
- `POST http://127.0.0.1:8000/query` with a JSON body like:

```json
{
  "question": "How do I move a CharacterBody2D with WASD?",
  "context": {
    "engine_version": "4.2",
    "language": "gdscript",
    "selected_node_type": "CharacterBody2D",
    "current_script": "extends CharacterBody2D\n"
  },
  "top_k": 5
}
```

will return a placeholder `answer` and one stub `snippet`. This is enough for the
Godot plugin to integrate and verify the plumbing.

