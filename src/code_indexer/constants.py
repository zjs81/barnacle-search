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
DEEP_INDEX_SNAPSHOT_FILE = "index.bin"
DEEP_INDEX_DB_FILE = DEEP_INDEX_SNAPSHOT_FILE

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "granite-embedding"

EMBED_BATCH_SIZE = max(1, int(os.getenv("BARNACLE_EMBED_BATCH_SIZE", "64")))
EMBED_CONCURRENT_BATCHES = max(1, int(os.getenv("BARNACLE_EMBED_CONCURRENCY", "4")))

DEBOUNCE_SECS = 0.5
INDEX_MAX_WORKERS = max(1, int((os.cpu_count() or 4) * 0.75))
MTIME_PRECISION_DIGITS = 6
