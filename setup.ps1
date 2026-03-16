# Setup barnacle-search and register it as a global MCP server in Claude Code.
# Run from PowerShell: .\setup.ps1
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── 1. Check dependencies ─────────────────────────────────────────────────────

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git is required. Install from https://git-scm.com and re-run."
    exit 1
}

$hasCompiler = (Get-Command gcc -ErrorAction SilentlyContinue) -or
               (Get-Command clang -ErrorAction SilentlyContinue) -or
               (Get-Command cl -ErrorAction SilentlyContinue)
if (-not $hasCompiler) {
    Write-Warning "No C compiler found. The Dart grammar will be built on first use."
    Write-Warning "Install MinGW (https://winlibs.com) or Visual Studio Build Tools."
}

# ── 2. Install uv if missing ──────────────────────────────────────────────────

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
}

# ── 3. Install Python dependencies ───────────────────────────────────────────

Write-Host "Installing Python dependencies..."
uv --directory $RepoDir sync

# ── 4. Pre-build Dart grammar ─────────────────────────────────────────────────

if ($hasCompiler) {
    Write-Host "Building Dart grammar..."
    uv --directory $RepoDir run python `
        "$RepoDir\src\code_indexer\indexing\strategies\build_dart_grammar.py"
} else {
    Write-Host "Skipping Dart grammar build (no compiler). Will build on first use if a compiler is added."
}

# ── 5. Register MCP server in Claude Code ─────────────────────────────────────

$ClaudeJson = Join-Path $env:USERPROFILE ".claude.json"

$uvPath = (Get-Command uv -ErrorAction SilentlyContinue)?.Source
if (-not $uvPath) { $uvPath = "uv" }

# Load or create config
if (Test-Path $ClaudeJson) {
    $config = Get-Content $ClaudeJson -Raw | ConvertFrom-Json
} else {
    $config = [PSCustomObject]@{}
}

# Ensure mcpServers exists
if (-not ($config.PSObject.Properties.Name -contains "mcpServers")) {
    $config | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
}

# Add barnacle-search entry
$entry = [PSCustomObject]@{
    type    = "stdio"
    command = $uvPath
    args    = @("--directory", $RepoDir, "run", "barnacle-search")
    env     = [PSCustomObject]@{}
}

$config.mcpServers | Add-Member -MemberType NoteProperty -Name "barnacle-search" -Value $entry -Force

$config | ConvertTo-Json -Depth 10 | Set-Content $ClaudeJson -Encoding UTF8
Write-Host "Registered barnacle-search in $ClaudeJson"

# ── 6. Done ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "barnacle-search is ready!"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Claude Code to pick up the new MCP server"
Write-Host "  2. In any project, run:"
Write-Host "       set_project_path(`"/path/to/your/project`")"
Write-Host "       build_deep_index()"
Write-Host ""
Write-Host "Requires Ollama for semantic search:"
Write-Host "  winget install Ollama.Ollama"
Write-Host "  ollama pull qwen3-embedding:0.6b"
