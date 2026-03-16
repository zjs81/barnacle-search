#!/usr/bin/env bash
# Setup barnacle-search and register it as a global MCP server in Claude Code.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Check dependencies ─────────────────────────────────────────────────────

if ! command -v git &>/dev/null; then
    echo "Error: git is required. Install it and re-run." >&2
    exit 1
fi

if ! command -v gcc &>/dev/null && ! command -v clang &>/dev/null && ! command -v cc &>/dev/null; then
    echo "Error: A C compiler (gcc or clang) is required to build the Dart grammar." >&2
    echo "  macOS:  xcode-select --install" >&2
    echo "  Ubuntu: sudo apt install gcc" >&2
    exit 1
fi

# ── 2. Install uv if missing ──────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── 3. Install Python dependencies ───────────────────────────────────────────

echo "Installing Python dependencies..."
uv --directory "$REPO_DIR" sync

# ── 4. Pre-build Dart grammar ─────────────────────────────────────────────────

echo "Building Dart grammar..."
uv --directory "$REPO_DIR" run python \
    "$REPO_DIR/src/code_indexer/indexing/strategies/build_dart_grammar.py"

# ── 5. Register MCP server in Claude Code ─────────────────────────────────────

CLAUDE_JSON="$HOME/.claude.json"

if ! command -v python3 &>/dev/null; then
    echo "Warning: python3 not found — skipping Claude Code MCP registration." >&2
    echo "Manually add barnacle-search to your Claude Code MCP config." >&2
else
    python3 - <<PYEOF
import json, os, sys

claude_json = os.path.expanduser("~/.claude.json")

# Load existing config or start fresh
if os.path.exists(claude_json):
    with open(claude_json, "r") as f:
        config = json.load(f)
else:
    config = {}

config.setdefault("mcpServers", {})

# Find uv on PATH
import shutil
uv_path = shutil.which("uv") or "uv"

config["mcpServers"]["barnacle-search"] = {
    "type": "stdio",
    "command": uv_path,
    "args": ["--directory", "$REPO_DIR", "run", "barnacle-search"],
    "env": {}
}

with open(claude_json, "w") as f:
    json.dump(config, f, indent=2)

print("Registered barnacle-search in", claude_json)
PYEOF
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────

echo ""
echo "barnacle-search is ready!"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code to pick up the new MCP server"
echo "  2. In any project, run:"
echo "       set_project_path(\"/path/to/your/project\")"
echo "       build_deep_index()"
echo ""
echo "Requires Ollama for semantic search:"
echo "  brew install ollama && ollama pull qwen3-embedding:0.6b"
