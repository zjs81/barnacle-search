# 🪸 barnacle-search

A local MCP server that attaches to your codebase and gives Claude semantic search, symbol extraction, and auto-reindexing — no cloud, no API keys.

## What it does

- **Symbol extraction** — parses every file with tree-sitter and indexes classes, methods, functions, imports
- **Semantic search** — embeds your codebase with a local Ollama model so you can search by meaning, not just text
- **Regex search** — fast ripgrep/grep fallback for exact pattern matching
- **Auto-reindex** — watches for file changes and updates the index automatically
- **Works offline** — everything runs locally via Ollama

## Supported languages

| Language | Extensions |
|----------|-----------|
| C# | `.cs` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| TypeScript | `.ts`, `.tsx` |
| HTML | `.html`, `.htm` |
| Python | `.py`, `.pyw` |
| Dart | `.dart` |

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (installed automatically by setup scripts)
- [Ollama](https://ollama.com) for semantic search
- git + a C compiler (`gcc` or `clang`) for the Dart grammar

## Setup

### macOS / Linux

```bash
git clone https://github.com/zjs81/barnacle-search.git
cd barnacle-search
./setup.sh
```

### Windows

```powershell
git clone https://github.com/zjs81/barnacle-search.git
cd barnacle-search
.\setup.ps1
```

The setup script will:
1. Install `uv` if not already present
2. Install all Python dependencies
3. Compile the Dart tree-sitter grammar from source
4. Register `barnacle-search` as a global MCP server in Claude Code (`~/.claude.json`)

### Ollama (for semantic search)

Semantic search requires a running Ollama instance with the embedding model pulled:

```bash
# macOS
brew install ollama
ollama pull qwen3-embedding:0.6b

# Windows
winget install Ollama.Ollama
ollama pull qwen3-embedding:0.6b
```

Barnacle will auto-pull the model if Ollama is running but the model isn't downloaded yet. Structural search (symbols, regex) works fine without Ollama.

## Usage in Claude Code

After setup, restart Claude Code. Then in any session:

```
set_project_path("/path/to/your/project")
build_deep_index()
```

`build_deep_index()` only needs to run once — the index updates automatically when files change.

## Available tools

| Tool | Description |
|------|-------------|
| `set_project_path(path)` | Point barnacle at a project directory |
| `build_deep_index()` | Parse all files and generate embeddings |
| `semantic_search(query)` | Natural language search over your codebase |
| `find_files(pattern)` | Glob matching e.g. `**/*Service*.cs` |
| `search_code(pattern)` | Regex search across files |
| `get_file_summary(path)` | Symbols, imports, line count for a file |
| `get_symbol_body(file, symbol)` | Read source of a specific method or class |
| `get_index_status()` | File count, language breakdown, embedding count |

## Adding to a project's CLAUDE.md

To make Claude automatically use barnacle-search in a specific project, add this to your `CLAUDE.md`:

```markdown
## Code Navigation

Use the `barnacle-search` MCP tools to explore this codebase.

### Setup (first time per session)
set_project_path("/absolute/path/to/project")
build_deep_index()

### Key tools
- `semantic_search(query="...")` — find by meaning
- `find_files(pattern="**/*.cs")` — find by name
- `search_code(pattern="...")` — find by regex
- `get_file_summary(path="...")` — symbols in a file
- `get_symbol_body(file="...", symbol="MethodName")` — read a method
```

## How it works

Barnacle uses a two-tier index:

1. **Shallow index** — a lightweight JSON file list with mtimes for fast file lookup without touching the database
2. **Deep index** — a SQLite database with three tables:
   - `files` — path, language, line count, imports, exports
   - `symbols` — extracted classes/methods/functions with line ranges
   - `symbol_embeddings` — one vector per symbol (packed float32 BLOB, no numpy/chromadb needed)

### Symbol-level embeddings

Embeddings are generated per symbol (class, method, function), not per file. Each symbol is embedded with its full context:

```
path/to/File.cs [csharp] > ClassName > MethodName
signature: ClassName.MethodName(int userId, string name)
<up to 40 lines of body>
```

This means `semantic_search("password hashing")` returns `PasswordHasher.Hash()` directly instead of a file that happens to contain it somewhere. `semantic_search` results include a `matched_symbols` list showing which specific symbols scored highest and their individual scores.

A large repo with 1,000 files and 30 symbols per file produces ~30,000 embeddings — all stored in SQLite, searched with pure Python cosine similarity. No vector database needed at this scale.

### File watcher

Uses FSEventsObserver on macOS and inotify on Linux — directory-level watching with a 500ms debounce, so large repos with `node_modules` work fine. Changed files are re-parsed and their symbol embeddings regenerated incrementally.
