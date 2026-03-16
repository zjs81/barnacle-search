import os
import tempfile

SUPPORTED_EXTENSIONS: set[str] = {
    ".cs",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".html", ".htm",
    ".py", ".pyw",
    ".dart",
}

EXCLUDE_DIRS: set[str] = {
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__",
    ".venv", "venv", ".env",
    "dist", "build", "out", "bin", "obj",
    ".vs", ".idea", ".vscode",
    "coverage", ".nyc_output",
    "Migrations",  # EF Core migrations
}

# Cache directory: e.g. /tmp/code_indexer/<hash_of_project_path>/
_CACHE_BASE = os.path.join(tempfile.gettempdir(), "code_indexer")

SHALLOW_INDEX_FILE = "shallow.json"
DEEP_INDEX_DB_FILE = "index.db"

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "qwen3-embedding:0.6b"

# Embedding concurrency: number of parallel requests sent to Ollama.
# Requires OLLAMA_NUM_PARALLEL >= this value on the Ollama server.
# Set OLLAMA_NUM_PARALLEL=4 in your environment before running `ollama serve`.
EMBED_CONCURRENCY = 4
EMBED_BATCH_SIZE = 8  # ≤8 avoids Ollama quality-degradation bug (issue #6262)

DEBOUNCE_SECS = 0.5
INDEX_MAX_WORKERS = max(1, int((os.cpu_count() or 4) * 0.75))
