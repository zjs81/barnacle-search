#!/usr/bin/env bash
# Setup barnacle-search and register it as a global MCP server in Claude Code, Codex, and OpenCode.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMBED_MODEL="granite-embedding"
CLAUDE_JSON="$HOME/.claude.json"
CLAUDE_MEMORY="$HOME/.claude/CLAUDE.md"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CODEX_TOML="$HOME/.codex/config.toml"
CODEX_AGENTS="$HOME/.codex/AGENTS.md"
OPENCODE_DIR="$HOME/.config/opencode"
OPENCODE_CONFIG="$OPENCODE_DIR/opencode.json"
OPENCODE_AGENTS="$OPENCODE_DIR/AGENTS.md"

detect_claude_install() {
    if [[ ! -f "$CLAUDE_JSON" ]] || ! command -v python3 &>/dev/null; then
        return 1
    fi

    python3 - <<'PYEOF' >/dev/null 2>&1
import json
import os

path = os.path.expanduser("~/.claude.json")
with open(path, "r", encoding="utf-8") as f:
    config = json.load(f)

if "barnacle-search" in config.get("mcpServers", {}):
    raise SystemExit(0)
raise SystemExit(1)
PYEOF
}

detect_codex_install() {
    [[ -f "$CODEX_TOML" ]] && rg -q '^\[mcp_servers\."barnacle-search"\]$' "$CODEX_TOML"
}

detect_opencode_install() {
    if [[ ! -f "$OPENCODE_CONFIG" ]] || ! command -v python3 &>/dev/null; then
        return 1
    fi

    python3 - <<'PYEOF' >/dev/null 2>&1
import json
import os
import re

def strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//.*$", r"\1", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text

path = os.path.expanduser("~/.config/opencode/opencode.json")
with open(path, "r", encoding="utf-8") as f:
    config = json.loads(strip_jsonc(f.read()) or "{}")

if "barnacle-search" in config.get("mcp", {}):
    raise SystemExit(0)
raise SystemExit(1)
PYEOF
}

target_has() {
    case ",$1," in
        *",$2,"*) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_install_target() {
    INSTALL_TARGET=""
    while [[ -z "$INSTALL_TARGET" ]]; do
        echo ""
        echo "Register barnacle-search for:"
        echo "  1) Claude Code"
        echo "  2) Codex"
        echo "  3) OpenCode"
        echo "  4) Claude Code + Codex"
        echo "  5) Claude Code + OpenCode"
        echo "  6) Codex + OpenCode"
        echo "  7) All"
        read -r -p "Choose 1-7 [7]: " choice
        choice="${choice:-7}"
        case "$choice" in
            1) INSTALL_TARGET="claude" ;;
            2) INSTALL_TARGET="codex" ;;
            3) INSTALL_TARGET="opencode" ;;
            4) INSTALL_TARGET="claude,codex" ;;
            5) INSTALL_TARGET="claude,opencode" ;;
            6) INSTALL_TARGET="codex,opencode" ;;
            7) INSTALL_TARGET="claude,codex,opencode" ;;
            *)
                echo "Invalid choice: $choice" >&2
                ;;
        esac
    done
}

prompt_uninstall_target() {
    local claude_installed="$1"
    local codex_installed="$2"
    local opencode_installed="$3"
    UNINSTALL_TARGET=""

    if [[ "$claude_installed" == "0" && "$codex_installed" == "0" && "$opencode_installed" == "0" ]]; then
        echo "barnacle-search is not registered in Claude Code, Codex, or OpenCode."
        exit 0
    fi

    if [[ "$claude_installed" == "1" && "$codex_installed" == "0" && "$opencode_installed" == "0" ]]; then
        echo "Detected barnacle-search registration in Claude Code only."
        UNINSTALL_TARGET="claude"
        return
    fi

    if [[ "$claude_installed" == "0" && "$codex_installed" == "1" && "$opencode_installed" == "0" ]]; then
        echo "Detected barnacle-search registration in Codex only."
        UNINSTALL_TARGET="codex"
        return
    fi

    if [[ "$claude_installed" == "0" && "$codex_installed" == "0" && "$opencode_installed" == "1" ]]; then
        echo "Detected barnacle-search registration in OpenCode only."
        UNINSTALL_TARGET="opencode"
        return
    fi

    while [[ -z "$UNINSTALL_TARGET" ]]; do
        echo ""
        echo "Uninstall barnacle-search from:"
        echo "  1) Claude Code"
        echo "  2) Codex"
        echo "  3) OpenCode"
        echo "  4) Claude Code + Codex"
        echo "  5) Claude Code + OpenCode"
        echo "  6) Codex + OpenCode"
        echo "  7) All"
        read -r -p "Choose 1-7 [7]: " choice
        choice="${choice:-7}"
        case "$choice" in
            1) UNINSTALL_TARGET="claude" ;;
            2) UNINSTALL_TARGET="codex" ;;
            3) UNINSTALL_TARGET="opencode" ;;
            4) UNINSTALL_TARGET="claude,codex" ;;
            5) UNINSTALL_TARGET="claude,opencode" ;;
            6) UNINSTALL_TARGET="codex,opencode" ;;
            7) UNINSTALL_TARGET="claude,codex,opencode" ;;
            *)
                echo "Invalid choice: $choice" >&2
                ;;
        esac
    done
}

uninstall_claude() {
    if [[ ! -f "$CLAUDE_JSON" ]]; then
        echo "Claude Code config not found; nothing to remove."
        return
    fi

    if ! command -v python3 &>/dev/null; then
        echo "Warning: python3 not found - skipping Claude Code uninstall." >&2
        return
    fi

    python3 - <<'PYEOF'
import json
import os

claude_json = os.path.expanduser("~/.claude.json")
with open(claude_json, "r", encoding="utf-8") as f:
    config = json.load(f)

servers = config.get("mcpServers", {})
removed = servers.pop("barnacle-search", None) is not None
if not servers and "mcpServers" in config:
    del config["mcpServers"]

with open(claude_json, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)

print("Removed barnacle-search from", claude_json if removed else f"{claude_json} (no existing entry)")
PYEOF

    python3 - <<'PYEOF'
import os
import re

claude_memory = os.path.expanduser("~/.claude/CLAUDE.md")
if not os.path.exists(claude_memory):
    print("Claude memory not found; nothing to remove.")
    raise SystemExit(0)

with open(claude_memory, "r", encoding="utf-8") as f:
    existing = f.read()

pattern = re.compile(
    r'(?ms)\n?<!-- barnacle-search:claude-guidance:start -->\n.*?<!-- barnacle-search:claude-guidance:end -->\n?'
)
updated, count = pattern.subn("\n", existing)
updated = updated.strip()

if updated:
    updated += "\n"
    with open(claude_memory, "w", encoding="utf-8") as f:
        f.write(updated)
else:
    os.remove(claude_memory)

print("Removed barnacle-search guidance from", claude_memory if count else f"{claude_memory} (no existing block)")
PYEOF

    python3 - <<'PYEOF'
import json
import os

claude_settings = os.path.expanduser("~/.claude/settings.json")
if not os.path.exists(claude_settings):
    print("Claude settings not found; nothing to remove.")
    raise SystemExit(0)

with open(claude_settings, "r", encoding="utf-8") as f:
    config = json.load(f)

permissions = config.get("permissions")
if isinstance(permissions, dict):
    allow = permissions.get("allow")
    if isinstance(allow, list):
        allow = [rule for rule in allow if rule not in ("mcp__barnacle-search", "mcp__barnacle-search__*")]
        if allow:
            permissions["allow"] = allow
        else:
            permissions.pop("allow", None)
    if not permissions:
        config.pop("permissions", None)

with open(claude_settings, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print("Removed barnacle-search MCP permission from", claude_settings)
PYEOF
}

uninstall_codex() {
    if [[ ! -f "$CODEX_TOML" ]]; then
        echo "Codex config not found; nothing to remove."
        return
    fi

    if ! command -v python3 &>/dev/null; then
        echo "Warning: python3 not found - skipping Codex uninstall." >&2
        return
    fi

    python3 - <<'PYEOF'
import os
import re

codex_toml = os.path.expanduser("~/.codex/config.toml")
with open(codex_toml, "r", encoding="utf-8") as f:
    existing = f.read()

pattern = re.compile(
    r'(?ms)^\[mcp_servers\."barnacle-search"\]\n.*?(?:\n(?=^\[[^\n]+\]\n)|\Z)'
)
updated, count = pattern.subn("", existing)
updated = updated.rstrip() + ("\n" if updated.strip() else "")

with open(codex_toml, "w", encoding="utf-8") as f:
    f.write(updated)

print("Removed barnacle-search from", codex_toml if count else f"{codex_toml} (no existing entry)")
PYEOF

    if ! command -v python3 &>/dev/null; then
        echo "Warning: python3 not found - skipping Codex AGENTS cleanup." >&2
        return
    fi

    python3 - <<'PYEOF'
import os
import re

codex_agents = os.path.expanduser("~/.codex/AGENTS.md")
if not os.path.exists(codex_agents):
    print("Codex AGENTS not found; nothing to remove.")
    raise SystemExit(0)

with open(codex_agents, "r", encoding="utf-8") as f:
    existing = f.read()

pattern = re.compile(
    r'(?ms)\n?<!-- barnacle-search:codex-guidance:start -->\n.*?<!-- barnacle-search:codex-guidance:end -->\n?'
)
updated, count = pattern.subn("\n", existing)
updated = updated.strip()

if updated:
    updated += "\n"
    with open(codex_agents, "w", encoding="utf-8") as f:
        f.write(updated)
else:
    os.remove(codex_agents)

print("Removed barnacle-search guidance from", codex_agents if count else f"{codex_agents} (no existing block)")
PYEOF
}

uninstall_opencode() {
    if [[ ! -f "$OPENCODE_CONFIG" ]]; then
        echo "OpenCode config not found; nothing to remove."
    elif ! command -v python3 &>/dev/null; then
        echo "Warning: python3 not found - skipping OpenCode uninstall." >&2
    else
        python3 - <<'PYEOF'
import json
import os
import re

def strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//.*$", r"\1", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text

opencode_config = os.path.expanduser("~/.config/opencode/opencode.json")
with open(opencode_config, "r", encoding="utf-8") as f:
    config = json.loads(strip_jsonc(f.read()) or "{}")

mcp = config.get("mcp")
removed = False
if isinstance(mcp, dict) and "barnacle-search" in mcp:
    removed = mcp.pop("barnacle-search", None) is not None
    if not mcp:
        config.pop("mcp", None)

with open(opencode_config, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print("Removed barnacle-search from", opencode_config if removed else f"{opencode_config} (no existing entry)")
PYEOF
    fi

    if [[ ! -f "$OPENCODE_AGENTS" ]]; then
        echo "OpenCode AGENTS not found; nothing to remove."
        return
    fi

    if ! command -v python3 &>/dev/null; then
        echo "Warning: python3 not found - skipping OpenCode AGENTS cleanup." >&2
        return
    fi

    python3 - <<'PYEOF'
import os
import re

opencode_agents = os.path.expanduser("~/.config/opencode/AGENTS.md")
with open(opencode_agents, "r", encoding="utf-8") as f:
    existing = f.read()

pattern = re.compile(
    r'(?ms)\n?<!-- barnacle-search:opencode-guidance:start -->\n.*?<!-- barnacle-search:opencode-guidance:end -->\n?'
)
updated, count = pattern.subn("\n", existing)
updated = updated.strip()

if updated:
    updated += "\n"
    with open(opencode_agents, "w", encoding="utf-8") as f:
        f.write(updated)
else:
    os.remove(opencode_agents)

print("Removed barnacle-search guidance from", opencode_agents if count else f"{opencode_agents} (no existing block)")
PYEOF
}

CLAUDE_INSTALLED=0
CODEX_INSTALLED=0
OPENCODE_INSTALLED=0
if detect_claude_install; then
    CLAUDE_INSTALLED=1
fi
if detect_codex_install; then
    CODEX_INSTALLED=1
fi
if detect_opencode_install; then
    OPENCODE_INSTALLED=1
fi

echo "Current MCP registration status:"
if [[ "$CLAUDE_INSTALLED" == "1" ]]; then
    echo "  Claude Code: installed"
else
    echo "  Claude Code: not installed"
fi
if [[ "$CODEX_INSTALLED" == "1" ]]; then
    echo "  Codex: installed"
else
    echo "  Codex: not installed"
fi
if [[ "$OPENCODE_INSTALLED" == "1" ]]; then
    echo "  OpenCode: installed"
else
    echo "  OpenCode: not installed"
fi

ACTION=""
while [[ -z "$ACTION" ]]; do
    echo ""
    echo "What do you want to do?"
    echo "  1) Install or update"
    echo "  2) Uninstall"
    read -r -p "Choose 1 or 2 [1]: " action_choice
    action_choice="${action_choice:-1}"
    case "$action_choice" in
        1) ACTION="install" ;;
        2) ACTION="uninstall" ;;
        *)
            echo "Invalid choice: $action_choice" >&2
            ;;
    esac
done

if [[ "$ACTION" == "uninstall" ]]; then
    prompt_uninstall_target "$CLAUDE_INSTALLED" "$CODEX_INSTALLED" "$OPENCODE_INSTALLED"

    if target_has "$UNINSTALL_TARGET" "claude"; then
        uninstall_claude
    fi

    if target_has "$UNINSTALL_TARGET" "codex"; then
        uninstall_codex
    fi

    if target_has "$UNINSTALL_TARGET" "opencode"; then
        uninstall_opencode
    fi

    echo ""
    echo "barnacle-search uninstall complete."
    echo "Restart Claude Code, Codex, and/or OpenCode if they are currently running."
    exit 0
fi

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

# ── 5. Choose MCP target(s) ───────────────────────────────────────────────────

prompt_install_target

# ── 6. Register MCP server in Claude Code ─────────────────────────────────────

if target_has "$INSTALL_TARGET" "claude"; then
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

    python3 - <<'PYEOF'
import os
import re

claude_memory = os.path.expanduser("~/.claude/CLAUDE.md")
os.makedirs(os.path.dirname(claude_memory), exist_ok=True)

block = """<!-- barnacle-search:claude-guidance:start -->
## Barnacle Search

For exploratory codebase questions in a repository, use the `barnacle-search` MCP tools before shell search.

Required workflow:
1. Call `set_project_path("/absolute/path/to/repo")` before any other Barnacle tool.
2. If the deep index has not been built yet, call `build_deep_index()` when semantic or symbol-aware search will help.
3. Start exploratory work with `semantic_search(query="...")` by default. Use `search_code(pattern="...")` or `find_files(pattern="...")` first only when you already have a strong exact term, identifier, string, or path pattern.
4. Narrow with `get_file_summary(path="...")` and then read exact implementations with `get_symbol_body(file="...", symbol="...")`.
5. Use shell search only after Barnacle has narrowed the area, or immediately for exact identifier, exact string, or exact path lookup.

Never call `get_index_status()`, `semantic_search()`, `find_files()`, `search_code()`, `get_file_summary()`, or `get_symbol_body()` before `set_project_path()`.

If Barnacle results are low-signal, the index is not ready, or the task is an exact string/path lookup, fall back to shell search immediately.
<!-- barnacle-search:claude-guidance:end -->
"""

existing = ""
if os.path.exists(claude_memory):
    with open(claude_memory, "r", encoding="utf-8") as f:
        existing = f.read()

pattern = re.compile(
    r'(?ms)<!-- barnacle-search:claude-guidance:start -->\n.*?<!-- barnacle-search:claude-guidance:end -->'
)

if pattern.search(existing):
    updated = pattern.sub(block, existing).strip() + "\n"
else:
    prefix = existing.rstrip()
    if prefix:
        updated = prefix + "\n\n" + block + "\n"
    else:
        updated = block + "\n"

with open(claude_memory, "w", encoding="utf-8") as f:
    f.write(updated)

print("Registered barnacle-search guidance in", claude_memory)
PYEOF

    python3 - <<'PYEOF'
import json
import os

claude_settings = os.path.expanduser("~/.claude/settings.json")
os.makedirs(os.path.dirname(claude_settings), exist_ok=True)

if os.path.exists(claude_settings):
    with open(claude_settings, "r", encoding="utf-8") as f:
        config = json.load(f)
else:
    config = {}

permissions = config.setdefault("permissions", {})
allow = permissions.setdefault("allow", [])
if "mcp__barnacle-search" not in allow:
    allow.append("mcp__barnacle-search")

with open(claude_settings, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print("Registered barnacle-search MCP permission in", claude_settings)
PYEOF
fi
fi

# ── 7. Register MCP server in Codex ───────────────────────────────────────────

if target_has "$INSTALL_TARGET" "codex"; then
if ! command -v python3 &>/dev/null; then
    echo "Warning: python3 not found - skipping Codex MCP registration." >&2
    echo "Manually add barnacle-search to your Codex MCP config." >&2
else
    python3 - <<PYEOF
import os, re

codex_toml = os.path.expanduser("~/.codex/config.toml")
os.makedirs(os.path.dirname(codex_toml), exist_ok=True)

block = """[mcp_servers."barnacle-search"]
command = "uv"
args = ["--directory", "$REPO_DIR", "run", "barnacle-search"]
env = { UV_CACHE_DIR = "/tmp/barnacle-search-uv-cache" }
"""

existing = ""
if os.path.exists(codex_toml):
    with open(codex_toml, "r", encoding="utf-8") as f:
        existing = f.read()

pattern = re.compile(
    r'(?ms)^\[mcp_servers\."barnacle-search"\]\n.*?(?=^\[[^\n]+\]\n|\Z)'
)

if pattern.search(existing):
    updated = pattern.sub(block, existing).rstrip() + "\n"
else:
    prefix = existing.rstrip()
    if prefix:
        updated = prefix + "\n\n" + block
    else:
        updated = block

with open(codex_toml, "w", encoding="utf-8") as f:
    f.write(updated)

print("Registered barnacle-search in", codex_toml)
PYEOF

    BARNACLE_REPO_DIR="$REPO_DIR" python3 - <<'PYEOF'
import os
import re

codex_agents = os.path.expanduser("~/.codex/AGENTS.md")
os.makedirs(os.path.dirname(codex_agents), exist_ok=True)

block = """<!-- barnacle-search:codex-guidance:start -->
## Barnacle Search

For exploratory codebase questions in a repository, use the `barnacle-search` MCP tools before shell search.

Required workflow:
1. Call `set_project_path("/absolute/path/to/repo")` before any other Barnacle tool.
2. If the deep index has not been built yet, call `build_deep_index()` when semantic or symbol-aware search will help.
3. Start exploratory work with `semantic_search(query="...")` by default. Use `search_code(pattern="...")` or `find_files(pattern="...")` first only when you already have a strong exact term, identifier, string, or path pattern.
4. Narrow with `get_file_summary(path="...")` and then read exact implementations with `get_symbol_body(file="...", symbol="...")`.
5. Use `rg` and `rg --files` only after Barnacle has narrowed the area, or immediately for exact identifier, exact string, or exact path lookup.

Never call `get_index_status()`, `semantic_search()`, `find_files()`, `search_code()`, `get_file_summary()`, or `get_symbol_body()` before `set_project_path()`.

If Barnacle results are low-signal, the index is not ready, or the user asks for an exact string/path lookup, fall back to `rg` immediately.
<!-- barnacle-search:codex-guidance:end -->
"""

existing = ""
if os.path.exists(codex_agents):
    with open(codex_agents, "r", encoding="utf-8") as f:
        existing = f.read()

pattern = re.compile(
    r'(?ms)<!-- barnacle-search:codex-guidance:start -->\n.*?<!-- barnacle-search:codex-guidance:end -->'
)

if pattern.search(existing):
    updated = pattern.sub(block, existing).strip() + "\n"
else:
    prefix = existing.rstrip()
    if prefix:
        updated = prefix + "\n\n" + block + "\n"
    else:
        updated = block + "\n"

with open(codex_agents, "w", encoding="utf-8") as f:
    f.write(updated)

print("Registered barnacle-search guidance in", codex_agents)
PYEOF
fi
fi

# ── 8. Register MCP server in OpenCode ───────────────────────────────────────

if target_has "$INSTALL_TARGET" "opencode"; then
if ! command -v python3 &>/dev/null; then
    echo "Warning: python3 not found - skipping OpenCode MCP registration." >&2
    echo "Manually add barnacle-search to your OpenCode config." >&2
else
    BARNACLE_REPO_DIR="$REPO_DIR" python3 - <<'PYEOF'
import json
import os
import re
import shutil

def strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//.*$", r"\1", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text

opencode_config = os.path.expanduser("~/.config/opencode/opencode.json")
os.makedirs(os.path.dirname(opencode_config), exist_ok=True)
repo_dir = os.environ["BARNACLE_REPO_DIR"]

if os.path.exists(opencode_config):
    with open(opencode_config, "r", encoding="utf-8") as f:
        config = json.loads(strip_jsonc(f.read()) or "{}")
else:
    config = {}

config.setdefault("$schema", "https://opencode.ai/config.json")
config.setdefault("mcp", {})

uv_path = shutil.which("uv") or "uv"
config["mcp"]["barnacle-search"] = {
    "type": "local",
    "command": [uv_path, "--directory", repo_dir, "run", "barnacle-search"],
    "enabled": True,
    "environment": {
        "UV_CACHE_DIR": "/tmp/barnacle-search-uv-cache"
    }
}

with open(opencode_config, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print("Registered barnacle-search in", opencode_config)
PYEOF

    python3 - <<'PYEOF'
import os
import re

opencode_agents = os.path.expanduser("~/.config/opencode/AGENTS.md")
os.makedirs(os.path.dirname(opencode_agents), exist_ok=True)

block = """<!-- barnacle-search:opencode-guidance:start -->
## Barnacle Search

For exploratory codebase questions in a repository, use the `barnacle-search` MCP tools before shell search.

Required workflow:
1. Call `set_project_path("/absolute/path/to/repo")` before any other Barnacle tool.
2. If the deep index has not been built yet, call `build_deep_index()` when semantic or symbol-aware search will help.
3. Start exploratory work with `semantic_search(query="...")` by default. Use `search_code(pattern="...")` or `find_files(pattern="...")` first only when you already have a strong exact term, identifier, string, or path pattern.
4. Narrow with `get_file_summary(path="...")` and then read exact implementations with `get_symbol_body(file="...", symbol="...")`.
5. Use shell search only after Barnacle has narrowed the area, or immediately for exact identifier, exact string, or exact path lookup.

Never call `get_index_status()`, `semantic_search()`, `find_files()`, `search_code()`, `get_file_summary()`, or `get_symbol_body()` before `set_project_path()`.

If Barnacle results are low-signal, the index is not ready, or the task is an exact string/path lookup, fall back to shell search immediately.
<!-- barnacle-search:opencode-guidance:end -->
"""

existing = ""
if os.path.exists(opencode_agents):
    with open(opencode_agents, "r", encoding="utf-8") as f:
        existing = f.read()

pattern = re.compile(
    r'(?ms)<!-- barnacle-search:opencode-guidance:start -->\n.*?<!-- barnacle-search:opencode-guidance:end -->'
)

if pattern.search(existing):
    updated = pattern.sub(block, existing).strip() + "\n"
else:
    prefix = existing.rstrip()
    if prefix:
        updated = prefix + "\n\n" + block + "\n"
    else:
        updated = block + "\n"

with open(opencode_agents, "w", encoding="utf-8") as f:
    f.write(updated)

print("Registered barnacle-search guidance in", opencode_agents)
PYEOF
fi
fi

# ── 9. Pull Ollama embedding model if available ───────────────────────────────

if command -v ollama &>/dev/null; then
    echo "Pulling Ollama embedding model ($EMBED_MODEL)..."
    ollama pull "$EMBED_MODEL"
else
    echo "Ollama not found; skipping model pull."
fi

# ── 10. Done ──────────────────────────────────────────────────────────────────

echo ""
echo "barnacle-search is ready!"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code, Codex, and/or OpenCode to pick up the new MCP server"
echo "  2. In any project, run:"
echo "       set_project_path(\"/path/to/your/project\")"
echo "       build_deep_index()"
echo ""
echo "Requires Ollama for semantic search:"
echo "  brew install ollama && ollama pull $EMBED_MODEL"
