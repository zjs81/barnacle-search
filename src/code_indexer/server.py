"""
MCP server entry point.

Exposes 8 tools over stdio:
  set_project_path, get_index_status, build_deep_index,
  find_files, get_file_summary, get_symbol_body,
  search_code, semantic_search
"""

import asyncio
import collections
import hashlib
import logging
import os
import time
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .constants import (
    DEEP_INDEX_SNAPSHOT_FILE,
    EMBED_BATCH_SIZE,
    EMBED_CONCURRENT_BATCHES,
    EMBED_MODEL,
    OLLAMA_BASE_URL,
    SHALLOW_INDEX_FILE,
    _CACHE_BASE,
)
from .embeddings.ollama_client import OllamaClient, ModelNotFoundError
from .embeddings.vector_store import VectorStore
from .indexing.deep_index import DeepIndex
from .indexing.shallow_index import ShallowIndex
from .indexing.strategies.factory import StrategyFactory
from .search.grep_search import search_code as _grep_search_code
from .watcher.file_watcher import FileWatcherService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────

_factory = StrategyFactory()
_ollama = OllamaClient(base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)
_watcher = FileWatcherService()
_index_lock = asyncio.Lock()
_embed_lock = asyncio.Lock()

# Mutable state — replaced each time set_project_path is called
_state: dict = {
    "project_path": None,
    "cache_dir": None,
    "shallow": None,   # ShallowIndex
    "deep": None,      # DeepIndex
    "vector": None,    # VectorStore
}
_build_state: dict = {
    "task": None,
    "status": "idle",
    "project_path": None,
    "force_rebuild": False,
    "started_at": None,
    "finished_at": None,
    "phase": None,
    "phase_started_at": None,
    "completed": 0,
    "total": 0,
    "percent_done": 0.0,
    "eta_seconds": None,
    "message": None,
    "result": None,
    "error": None,
}


def _cache_dir_for(project_path: str) -> str:
    """Deterministic cache directory based on the project path."""
    h = hashlib.md5(project_path.encode()).hexdigest()[:12]
    d = os.path.join(_CACHE_BASE, h)
    os.makedirs(d, exist_ok=True)
    return d


def _require_project() -> dict:
    if _state["project_path"] is None:
        raise ValueError("No project set. Call set_project_path first.")
    return _state


def _reset_build_state(project_path: Optional[str] = None) -> None:
    _build_state.update(
        {
            "task": None,
            "status": "idle",
            "project_path": project_path,
            "force_rebuild": False,
            "started_at": None,
            "finished_at": None,
            "phase": None,
            "phase_started_at": None,
            "completed": 0,
            "total": 0,
            "percent_done": 0.0,
            "eta_seconds": None,
            "message": None,
            "result": None,
            "error": None,
        }
    )


def _recompute_eta() -> None:
    phase_started_at = _build_state["phase_started_at"]
    completed = _build_state["completed"]
    total = _build_state["total"]
    if not phase_started_at or total <= 0 or completed <= 0 or completed >= total:
        _build_state["eta_seconds"] = 0 if total > 0 and completed >= total else None
        return

    elapsed = max(time.time() - phase_started_at, 0.001)
    rate = completed / elapsed
    if rate <= 0:
        _build_state["eta_seconds"] = None
        return
    _build_state["eta_seconds"] = max(int(round((total - completed) / rate)), 0)


def _set_build_progress(phase: str, completed: int, total: int, *, message: Optional[str] = None) -> None:
    if _build_state["phase"] != phase:
        _build_state["phase"] = phase
        _build_state["phase_started_at"] = time.time()

    _build_state["completed"] = max(completed, 0)
    _build_state["total"] = max(total, 0)
    _build_state["percent_done"] = (
        round((_build_state["completed"] / _build_state["total"]) * 100, 1)
        if _build_state["total"] > 0
        else 100.0
    )
    if message is not None:
        _build_state["message"] = message
    _recompute_eta()


def _get_indexing_status() -> dict:
    task = _build_state["task"]
    in_progress = bool(task and not task.done())
    _recompute_eta()
    return {
        "status": _build_state["status"],
        "in_progress": in_progress,
        "project_path": _build_state["project_path"],
        "force_rebuild": _build_state["force_rebuild"],
        "started_at": _build_state["started_at"],
        "finished_at": _build_state["finished_at"],
        "phase": _build_state["phase"],
        "completed": _build_state["completed"],
        "total": _build_state["total"],
        "percent_done": _build_state["percent_done"],
        "eta_seconds": _build_state["eta_seconds"],
        "message": _build_state["message"],
        "result": _build_state["result"],
        "error": _build_state["error"],
    }


def _start_build_job(force_rebuild: bool) -> dict:
    state = _require_project()
    task = _build_state["task"]
    if task and not task.done():
        _build_state["message"] = (
            "Deep index build already in progress. Call get_index_status() to track progress."
        )
        return {
            "status": "already_in_progress",
            "message": _build_state["message"],
            "indexing": _get_indexing_status(),
        }

    _build_state.update(
        {
            "task": None,
            "status": "queued",
            "project_path": state["project_path"],
            "force_rebuild": force_rebuild,
            "started_at": time.time(),
            "finished_at": None,
            "phase": "queued",
            "phase_started_at": time.time(),
            "completed": 0,
            "total": 0,
            "percent_done": 0.0,
            "eta_seconds": None,
            "message": "Deep index build queued. Call get_index_status() to track progress.",
            "result": None,
            "error": None,
        }
    )
    task = asyncio.create_task(
        _run_deep_index_build(
            project_path=state["project_path"],
            deep=state["deep"],
            vector=state["vector"],
            force_rebuild=force_rebuild,
        )
    )
    _build_state["task"] = task
    return {
        "status": "started",
        "message": "Deep index build started. Call get_index_status() to track progress.",
        "indexing": _get_indexing_status(),
    }


def _rebuild_callback(file_path: str):
    """Called by the file watcher when a file changes."""
    state = _state
    if state["deep"] is None:
        return
    logger.info("Auto-reindexing: %s", file_path)
    state["deep"].rebuild_file(file_path)
    # Schedule embedding for new symbols in the background
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_embed_pending())
    except RuntimeError:
        pass


def _repo_change_callback():
    """Called by the file watcher when the Git HEAD commit changes."""
    logger.info("Detected Git HEAD change for %s; scheduling incremental rebuild", _state["project_path"])
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(build_deep_index(force_rebuild=False))
    except RuntimeError:
        pass


async def _sync_stale_files():
    """Re-parse files that changed while the server was offline, then embed pending symbols."""
    async with _index_lock:
        state = _state
        if state["deep"] is None:
            return
        await asyncio.to_thread(state["deep"].sync_stale_files)

    await _embed_pending()


async def _embed_pending():
    """Embed any symbols that don't have vectors yet."""
    async with _embed_lock:
        while True:
            state = _state
            if state["deep"] is None or state["vector"] is None:
                return

            ollama_ok = await _ollama.is_available()
            if not ollama_ok:
                return

            # Collect pending symbols in a thread to avoid blocking the event loop
            def _collect():
                with state["deep"].mutation_lock():
                    symbols_needing_embed = state["deep"].store_ref.get_symbols_needing_embedding()
                    if not symbols_needing_embed:
                        return None

                    builder = state["deep"].builder
                    pending: list[tuple[str, str]] = []
                    file_lines_cache: dict[str, list[str]] = {}
                    pending_by_type: collections.Counter[str] = collections.Counter()
                    skipped_by_reason: collections.Counter[str] = collections.Counter()
                    for sym in symbols_needing_embed:
                        skip_reason = _skip_embedding_reason(sym)
                        if skip_reason:
                            skipped_by_reason[skip_reason] += 1
                            continue
                        fp = sym["path"]
                        if fp not in file_lines_cache:
                            try:
                                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                                    file_lines_cache[fp] = fh.readlines()
                            except OSError:
                                file_lines_cache[fp] = []
                        text = builder.build_symbol_embed_text(sym, fp, file_lines_cache[fp])
                        pending.append((sym["symbol_id"], text))
                        pending_by_type[sym.get("type", "unknown")] += 1

                    logger.info("Pending auto-embed symbols by type: %s", _format_counter(pending_by_type))
                    if skipped_by_reason:
                        logger.info("Auto-embed skipped symbols by reason: %s", _format_counter(skipped_by_reason))
                    return pending

            pending = await asyncio.to_thread(_collect)
            if pending is None:
                return

            all_batches = [
                pending[i : i + EMBED_BATCH_SIZE]
                for i in range(0, len(pending), EMBED_BATCH_SIZE)
            ]
            for wave_start in range(0, len(all_batches), EMBED_CONCURRENT_BATCHES):
                wave = all_batches[wave_start : wave_start + EMBED_CONCURRENT_BATCHES]
                coros = [
                    _ollama.embed_batch([t for _, t in batch])
                    for batch in wave
                ]
                try:
                    results = await asyncio.gather(*coros)
                except ModelNotFoundError:
                    return
                for batch, vectors in zip(wave, results):
                    if vectors:
                        symbol_ids = [sid for sid, _ in batch]
                        state["vector"].bulk_upsert_symbols(symbol_ids, EMBED_MODEL, vectors)
            logger.info("Auto-embedded %d new symbols", len(pending))
            return


async def _run_deep_index_build(
    *,
    project_path: str,
    deep: DeepIndex,
    vector: VectorStore,
    force_rebuild: bool,
) -> None:
    build_stats: dict = {"files": 0, "symbols": 0, "errors": 0}
    try:
        async with _index_lock:
            _build_state["status"] = "running"
            _build_state["message"] = "Parsing files for the deep index."

            # Stop file watcher during build to prevent concurrent DB writes
            _watcher.stop()
            try:
                # Phase 1: Parse files in a thread to avoid blocking the event loop
                def _parse_phase():
                    with deep.mutation_lock():
                        return deep.build_locked(
                            force_rebuild=force_rebuild,
                            progress_callback=lambda completed, total: _set_build_progress(
                                "parsing",
                                completed,
                                total,
                                message="Parsing files for the deep index.",
                            ),
                        )

                build_stats = await asyncio.to_thread(_parse_phase)

                embed_count = 0
                embed_skipped = False

                ollama_ok = await _ollama.is_available()
                if not ollama_ok:
                    logger.warning(
                        "Ollama not reachable at %s — skipping embeddings. "
                        "Run `ollama serve` and pull %s to enable semantic search.",
                        OLLAMA_BASE_URL,
                        EMBED_MODEL,
                    )
                    embed_skipped = True
                else:
                    # Phase 2: Collect pending symbols in a thread
                    def _collect_pending():
                        with deep.mutation_lock():
                            builder = deep.builder

                            if force_rebuild:
                                deep.store_ref.clear_symbol_embeddings()
                                symbols_needing_embed = deep.store_ref.get_all_symbols_with_file_info()
                            else:
                                symbols_needing_embed = deep.store_ref.get_symbols_needing_embedding()

                            symbol_counts = collections.Counter(deep.store_ref.get_symbol_type_counts())
                            logger.info("Indexed symbols by type: %s", _format_counter(symbol_counts))

                            pending: list[tuple[str, str]] = []
                            pending_by_type: collections.Counter[str] = collections.Counter()
                            skipped_by_reason: collections.Counter[str] = collections.Counter()
                            for sym in symbols_needing_embed:
                                skip_reason = _skip_embedding_reason(sym)
                                if skip_reason:
                                    skipped_by_reason[skip_reason] += 1
                                    continue
                                fp = sym["path"]
                                text = builder.build_symbol_embed_text(sym, fp)
                                pending.append((sym["symbol_id"], text))
                                pending_by_type[sym.get("type", "unknown")] += 1

                            logger.info("Embeddable symbols by type: %s", _format_counter(pending_by_type))
                            if skipped_by_reason:
                                logger.info(
                                    "Skipped embedding by reason: %s",
                                    _format_counter(skipped_by_reason),
                                )
                            return pending

                    pending = await asyncio.to_thread(_collect_pending)

                    # Phase 3: Embed batches concurrently
                    _set_build_progress(
                        "embedding",
                        0,
                        len(pending),
                        message="Generating embeddings for semantic search.",
                    )
                    all_batches = [
                        pending[i : i + EMBED_BATCH_SIZE]
                        for i in range(0, len(pending), EMBED_BATCH_SIZE)
                    ]
                    completed_symbols = 0
                    for wave_start in range(0, len(all_batches), EMBED_CONCURRENT_BATCHES):
                        wave = all_batches[wave_start : wave_start + EMBED_CONCURRENT_BATCHES]
                        coros = [
                            _ollama.embed_batch([t for _, t in batch])
                            for batch in wave
                        ]
                        try:
                            results = await asyncio.gather(*coros)
                        except ModelNotFoundError as exc:
                            _build_state["status"] = "failed"
                            _build_state["error"] = str(exc)
                            _build_state["finished_at"] = time.time()
                            _build_state["message"] = (
                                "Deep index build failed. Call get_index_status() for details."
                            )
                            _build_state["result"] = {
                                "files_parsed": build_stats.get("files", 0),
                                "symbols": build_stats.get("symbols", 0),
                                "embeddings": embed_count,
                            }
                            return
                        for batch, vectors in zip(wave, results):
                            if vectors:
                                symbol_ids = [sid for sid, _ in batch]
                                vector.bulk_upsert_symbols(symbol_ids, EMBED_MODEL, vectors, commit=False)
                                embed_count += len(vectors)
                            completed_symbols += len(batch)
                        _set_build_progress(
                            "embedding",
                            min(completed_symbols, len(pending)),
                            len(pending),
                            message="Generating embeddings for semantic search.",
                        )
                    if embed_count > 0:
                        deep.store_ref.commit()

                _build_state["status"] = "completed"
                _build_state["finished_at"] = time.time()
                _build_state["message"] = "Deep index build completed."
                _build_state["result"] = {
                    "files_parsed": build_stats.get("files", 0),
                    "symbols": build_stats.get("symbols", 0),
                    "errors": build_stats.get("errors", 0),
                    "embeddings": embed_count,
                    "embeddings_skipped": embed_skipped,
                    "model": EMBED_MODEL if not embed_skipped else None,
                }
                if _build_state["phase"] != "embedding":
                    _set_build_progress(
                        "parsing",
                        _build_state["total"],
                        _build_state["total"],
                        message="Deep index build completed.",
                    )
                else:
                    _set_build_progress(
                        "embedding",
                        _build_state["total"],
                        _build_state["total"],
                        message="Deep index build completed.",
                    )
            finally:
                if _state["project_path"] == project_path and _state["deep"] is deep:
                    _watcher.start(project_path, _rebuild_callback, _repo_change_callback)
    except Exception as exc:
        _build_state["status"] = "failed"
        _build_state["finished_at"] = time.time()
        _build_state["error"] = str(exc)
        _build_state["message"] = "Deep index build failed. Call get_index_status() for details."
        logger.exception("Deep index build failed")


# ── Embedding filter ──────────────────────────────────────────────────────────

def _skip_embedding(sym: dict) -> bool:
    """Return True for symbols not worth embedding."""
    sym_type = sym.get("type", "")
    # Imports carry no semantic search value
    if sym_type == "import":
        return True
    # Trivial methods/functions only (not classes — even empty DTOs/interfaces are searchable)
    if sym_type in ("method", "function"):
        start = sym.get("line") or 0
        end = sym.get("end_line") or 0
        if end - start <= 2:
            return True
    return False


def _skip_embedding_reason(sym: dict) -> Optional[str]:
    sym_type = sym.get("type", "")
    if sym_type == "import":
        return "import"
    if sym_type in ("method", "function"):
        start = sym.get("line") or 0
        end = sym.get("end_line") or 0
        if end - start <= 2:
            return "trivial_method"
    return None


def _format_counter(counter: collections.Counter) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}: {count}" for key, count in sorted(counter.items()))


# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP("barnacle-search")


@mcp.tool()
async def set_project_path(path: str) -> str:
    """
    Set the project root directory to index.

    Builds the shallow (file list) index immediately and starts the file watcher
    for automatic reindexing. Call build_deep_index() afterwards for full symbol
    extraction and semantic search support.

    Args:
        path: Absolute or relative path to the project root directory.

    Returns:
        Status message with file count and detected languages.
    """
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(abs_path):
        raise ValueError(f"Not a directory: {abs_path}")

    cache_dir = _cache_dir_for(abs_path)
    shallow_path = os.path.join(cache_dir, SHALLOW_INDEX_FILE)
    db_path = os.path.join(cache_dir, DEEP_INDEX_SNAPSHOT_FILE)

    # Build shallow index
    shallow = ShallowIndex()
    shallow.build(abs_path)
    shallow.save(shallow_path)

    # Initialize deep index snapshot and in-memory lookup state
    deep = DeepIndex(abs_path, db_path, _factory)
    vector = VectorStore(deep.store_ref)

    # Stop old watcher if any
    _watcher.stop()

    # Update global state
    _state["project_path"] = abs_path
    _state["cache_dir"] = cache_dir
    _state["shallow"] = shallow
    _state["deep"] = deep
    _state["vector"] = vector
    _reset_build_state(project_path=abs_path)

    # Start watcher
    _watcher.start(abs_path, _rebuild_callback, _repo_change_callback)

    # Sync files that changed while the server was offline
    asyncio.ensure_future(_sync_stale_files())

    stats = shallow.get_stats()
    lang_summary = ", ".join(
        f"{lang}: {count}" for lang, count in sorted(stats["by_language"].items())
    )
    return (
        f"Project set to: {abs_path}\n"
        f"Files found: {stats['total']}\n"
        f"Languages: {lang_summary or 'none'}\n"
        f"File watcher started. Run build_deep_index() for full symbol extraction."
    )


@mcp.tool()
def get_index_status() -> dict:
    """
    Return the current index status.

    Returns a dict with:
      - project_path: currently indexed project
      - shallow: file count and language breakdown
      - deep: whether built, file/symbol/embedding counts, built_at timestamp
      - watcher: monitoring status
    """
    state = _require_project()

    shallow_stats = state["shallow"].get_stats() if state["shallow"] else {}
    deep_stats = state["deep"].get_stats() if state["deep"] else {}
    watcher_status = _watcher.get_status()

    return {
        "project_path": state["project_path"],
        "shallow": shallow_stats,
        "deep": {
            "built": state["deep"].is_built() if state["deep"] else False,
            **deep_stats,
        },
        "indexing": _get_indexing_status(),
        "watcher": watcher_status,
    }


@mcp.tool()
async def build_deep_index(force_rebuild: bool = False) -> dict:
    """
    Build the full deep index: parse all files for symbols, then generate
    Ollama embeddings for semantic search.

    Args:
        force_rebuild: If True, re-parse all files even if unchanged.

    Returns:
        Dict with files, symbols, errors, embeddings counts and model used.
    """
    _require_project()
    return _start_build_job(force_rebuild=force_rebuild)


@mcp.tool()
def find_files(pattern: str) -> list[str]:
    """
    Search for files matching a glob pattern against the shallow index.

    Args:
        pattern: Glob pattern (e.g. "**/*.cs", "src/**/*.ts", "*.html").

    Returns:
        List of matching absolute file paths.
    """
    state = _require_project()
    shallow: ShallowIndex = state["shallow"]
    return shallow.find_files(pattern)


@mcp.tool()
def get_file_summary(path: str) -> dict:
    """
    Return the indexed summary for a file: symbols, imports, exports, line count.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        Dict with path, language, line_count, imports, exports, symbols list.
        Returns an error dict if the file is not indexed.
    """
    state = _require_project()
    deep: DeepIndex = state["deep"]

    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        abs_path = expanded
    else:
        abs_path = os.path.join(state["project_path"], expanded)
    abs_path = os.path.normpath(abs_path)
    summary = deep.get_file_summary(abs_path)
    if summary is None:
        return {"error": f"File not in index: {abs_path}. Run build_deep_index() first."}
    return summary


@mcp.tool()
def get_symbol_body(file: str, symbol: str) -> str:
    """
    Retrieve the source code of a specific symbol (function, class, method, etc.).

    IMPORTANT: The required parameters are named exactly `file` and `symbol`.
    Do NOT use `path`, `file_path`, `symbol_name`, or any other names.

    Args:
        file: Absolute path to the source file. Example: "/path/to/MyClass.cs"
        symbol: Short name only — NOT the qualified name. Examples: "HandleWopiRequest",
                "CheckFileInfo", "MyClass". Do NOT include the class prefix.

    Returns:
        Source code of the symbol, or an error message if not found.
    """
    state = _require_project()
    deep: DeepIndex = state["deep"]

    expanded = os.path.expanduser(file)
    if os.path.isabs(expanded):
        abs_path = expanded
    else:
        abs_path = os.path.join(state["project_path"], expanded)
    abs_path = os.path.normpath(abs_path)
    body = deep.get_symbol_body(abs_path, symbol)
    if body is None:
        return f"Symbol '{symbol}' not found in {abs_path}. Ensure build_deep_index() has run."
    return body


@mcp.tool()
def search_code(pattern: str, file_pattern: str = "*", max_results: int = 50) -> list[dict]:
    """
    Regex search across project files using ripgrep (or grep as fallback).

    IMPORTANT: The required parameter is named exactly `pattern`, NOT `query` or `search`.

    Args:
        pattern: Regular expression or literal string to search for. Example: "SystemLevel|IsSystemLevel"
        file_pattern: Glob to filter which files to search (e.g. "*.cs", "*Event*.cs", "**/*.ts").
                      Default searches all supported files.
        max_results: Maximum number of results to return. Default: 50.

    Returns:
        List of {"file": relative_path, "line": int, "match": str}.
    """
    state = _require_project()
    return _grep_search_code(
        state["project_path"],
        pattern,
        file_pattern=file_pattern,
        max_results=max_results,
    )


@mcp.tool()
async def semantic_search(query: str, top_k: int = 10) -> list[dict]:
    """
    Natural language semantic search using Ollama embeddings.

    Embeds the query with the same model used during indexing, then returns
    the most semantically similar files based on cosine similarity.

    Args:
        query: Natural language description (e.g. "authentication middleware",
               "database connection pooling", "error handling for HTTP requests").
        top_k: Number of results to return.

    Returns:
        List of {"file": path, "score": float, "language": str, "symbols": [...]}
        sorted by relevance descending.
    """
    state = _require_project()
    vector: VectorStore = state["vector"]
    deep: DeepIndex = state["deep"]

    if vector.get_count() == 0:
        return [{"error": "No embeddings built. Run build_deep_index() first."}]

    try:
        query_vector = await _ollama.embed(query)
    except ModelNotFoundError as exc:
        return [{"error": str(exc)}]
    if query_vector is None:
        return [{"error": f"Ollama not available at {OLLAMA_BASE_URL}. Is `ollama serve` running?"}]

    matches = vector.search(query_vector, top_k=top_k, query_text=query)

    results = []
    for match in matches:
        file_path = match["file"]
        score = match["score"]
        matched_symbols = match.get("matched_symbols", [])
        summary = deep.get_file_summary(file_path)
        language = summary.get("language", "") if summary else ""
        results.append(
            {
                "file": file_path,
                "score": round(score, 4),
                "language": language,
                "matched_symbols": matched_symbols,
            }
        )

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
