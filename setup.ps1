# Setup barnacle-search and register it as a global MCP server in Claude Code and Codex.
# Run from PowerShell: .\setup.ps1
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EmbedModel = "granite-embedding"
$ClaudeJson = Join-Path $env:USERPROFILE ".claude.json"
$ClaudeMemory = Join-Path (Join-Path $env:USERPROFILE ".claude") "CLAUDE.md"
$CodexDir = Join-Path $env:USERPROFILE ".codex"
$CodexToml = Join-Path $CodexDir "config.toml"
$CodexAgents = Join-Path $CodexDir "AGENTS.md"

function Test-ClaudeInstall {
    if (-not (Test-Path $ClaudeJson)) { return $false }
    try {
        $config = Get-Content $ClaudeJson -Raw | ConvertFrom-Json
        if (-not $config -or -not $config.PSObject.Properties.Name.Contains("mcpServers")) {
            return $false
        }
        return @($config.mcpServers.PSObject.Properties.Name) -contains "barnacle-search"
    } catch {
        return $false
    }
}

function Test-CodexInstall {
    if (-not (Test-Path $CodexToml)) { return $false }
    $codexConfig = Get-Content $CodexToml -Raw
    return $codexConfig -match '(?m)^\[mcp_servers\."barnacle-search"\]$'
}

function Remove-ClaudeInstall {
    if (-not (Test-Path $ClaudeJson)) {
        Write-Host "Claude Code config not found; nothing to remove."
        return
    }

    $config = Get-Content $ClaudeJson -Raw | ConvertFrom-Json
    if (-not $config -or -not $config.PSObject.Properties.Name.Contains("mcpServers")) {
        Write-Host "No Claude Code registration found in $ClaudeJson"
        return
    }

    $serverNames = @($config.mcpServers.PSObject.Properties.Name)
    if ($serverNames -contains "barnacle-search") {
        $config.mcpServers.PSObject.Properties.Remove("barnacle-search")
        if (-not @($config.mcpServers.PSObject.Properties.Name).Count) {
            $config.PSObject.Properties.Remove("mcpServers")
        }
        $config | ConvertTo-Json -Depth 10 | Set-Content $ClaudeJson -Encoding UTF8
        Write-Host "Removed barnacle-search from $ClaudeJson"
    } else {
        Write-Host "No Claude Code registration found in $ClaudeJson"
    }

    if (-not (Test-Path $ClaudeMemory)) {
        Write-Host "Claude memory not found; nothing to remove."
        return
    }

    $memoryContent = Get-Content $ClaudeMemory -Raw
    $memoryPattern = '(?ms)\r?\n?<!-- barnacle-search:claude-guidance:start -->\r?\n.*?<!-- barnacle-search:claude-guidance:end -->\r?\n?'
    if ($memoryContent -match $memoryPattern) {
        $updatedMemory = [regex]::Replace($memoryContent, $memoryPattern, "`r`n").Trim()
        if ($updatedMemory) {
            $updatedMemory += "`r`n"
            $updatedMemory | Set-Content $ClaudeMemory -Encoding UTF8
        } else {
            Remove-Item $ClaudeMemory
        }
        Write-Host "Removed barnacle-search guidance from $ClaudeMemory"
    } else {
        Write-Host "No barnacle-search guidance block found in $ClaudeMemory"
    }
}

function Remove-CodexInstall {
    if (-not (Test-Path $CodexToml)) {
        Write-Host "Codex config not found; nothing to remove."
    } else {
        $codexConfig = Get-Content $CodexToml -Raw
        $pattern = '(?ms)^\[mcp_servers\."barnacle-search"\]\r?\n.*?(?:\r?\n(?=^\[[^\r\n]+\]\r?\n)|\z)'
        if ($codexConfig -match $pattern) {
            $updatedCodexConfig = [regex]::Replace($codexConfig, $pattern, "").TrimEnd()
            if ($updatedCodexConfig) {
                $updatedCodexConfig += "`r`n"
            }
            $updatedCodexConfig | Set-Content $CodexToml -Encoding UTF8
            Write-Host "Removed barnacle-search from $CodexToml"
        } else {
            Write-Host "No Codex registration found in $CodexToml"
        }
    }

    if (-not (Test-Path $CodexAgents)) {
        Write-Host "Codex AGENTS not found; nothing to remove."
        return
    }

    $agentsContent = Get-Content $CodexAgents -Raw
    $agentsPattern = '(?ms)\r?\n?<!-- barnacle-search:codex-guidance:start -->\r?\n.*?<!-- barnacle-search:codex-guidance:end -->\r?\n?'
    if ($agentsContent -match $agentsPattern) {
        $updatedAgents = [regex]::Replace($agentsContent, $agentsPattern, "`r`n").Trim()
        if ($updatedAgents) {
            $updatedAgents += "`r`n"
            $updatedAgents | Set-Content $CodexAgents -Encoding UTF8
        } else {
            Remove-Item $CodexAgents
        }
        Write-Host "Removed barnacle-search guidance from $CodexAgents"
    } else {
        Write-Host "No barnacle-search guidance block found in $CodexAgents"
    }
}

$ClaudeInstalled = Test-ClaudeInstall
$CodexInstalled = Test-CodexInstall

Write-Host "Current MCP registration status:"
if ($ClaudeInstalled) {
    Write-Host "  Claude Code: installed"
} else {
    Write-Host "  Claude Code: not installed"
}
if ($CodexInstalled) {
    Write-Host "  Codex: installed"
} else {
    Write-Host "  Codex: not installed"
}

do {
    Write-Host ""
    Write-Host "What do you want to do?"
    Write-Host "  1) Install or update"
    Write-Host "  2) Uninstall"
    $actionChoice = Read-Host "Choose 1 or 2 [1]"
    if ([string]::IsNullOrWhiteSpace($actionChoice)) { $actionChoice = "1" }
    switch ($actionChoice) {
        "1" { $Action = "install" }
        "2" { $Action = "uninstall" }
        default {
            Write-Warning "Invalid choice: $actionChoice"
            $Action = $null
        }
    }
} while (-not $Action)

if ($Action -eq "uninstall") {
    if (-not $ClaudeInstalled -and -not $CodexInstalled) {
        Write-Host "barnacle-search is not registered in Claude Code or Codex."
        exit 0
    }

    if ($ClaudeInstalled -and -not $CodexInstalled) {
        Write-Host "Detected barnacle-search registration in Claude Code only."
        $UninstallTarget = "claude"
    } elseif (-not $ClaudeInstalled -and $CodexInstalled) {
        Write-Host "Detected barnacle-search registration in Codex only."
        $UninstallTarget = "codex"
    } else {
        do {
            Write-Host ""
            Write-Host "Uninstall barnacle-search from:"
            Write-Host "  1) Claude Code"
            Write-Host "  2) Codex"
            Write-Host "  3) Both"
            $choice = Read-Host "Choose 1, 2, or 3 [3]"
            if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "3" }
            switch ($choice) {
                "1" { $UninstallTarget = "claude" }
                "2" { $UninstallTarget = "codex" }
                "3" { $UninstallTarget = "both" }
                default {
                    Write-Warning "Invalid choice: $choice"
                    $UninstallTarget = $null
                }
            }
        } while (-not $UninstallTarget)
    }

    if ($UninstallTarget -eq "claude" -or $UninstallTarget -eq "both") {
        Remove-ClaudeInstall
    }
    if ($UninstallTarget -eq "codex" -or $UninstallTarget -eq "both") {
        Remove-CodexInstall
    }

    Write-Host ""
    Write-Host "barnacle-search uninstall complete."
    Write-Host "Restart Claude Code and/or Codex if they are currently running."
    exit 0
}

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

# ── 5. Choose MCP target(s) ───────────────────────────────────────────────────

do {
    Write-Host ""
    Write-Host "Register barnacle-search for:"
    Write-Host "  1) Claude Code"
    Write-Host "  2) Codex"
    Write-Host "  3) Both"
    $choice = Read-Host "Choose 1, 2, or 3 [3]"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "3" }
    switch ($choice) {
        "1" { $InstallTarget = "claude" }
        "2" { $InstallTarget = "codex" }
        "3" { $InstallTarget = "both" }
        default {
            Write-Warning "Invalid choice: $choice"
            $InstallTarget = $null
        }
    }
} while (-not $InstallTarget)

# ── 6. Register MCP server in Claude Code ─────────────────────────────────────

$uvPath = (Get-Command uv -ErrorAction SilentlyContinue)?.Source
if (-not $uvPath) { $uvPath = "uv" }

if ($InstallTarget -eq "claude" -or $InstallTarget -eq "both") {
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

    $memoryBlock = @"
<!-- barnacle-search:claude-guidance:start -->
## Barnacle Search

For exploratory codebase questions in a repository, use the `barnacle-search` MCP tools before shell search.

Required workflow:
1. Call `set_project_path("/absolute/path/to/repo")` before any other Barnacle tool.
2. If the deep index has not been built yet, call `build_deep_index()` when semantic or symbol-aware search will help.
3. Start exploration with `semantic_search(query="...")` for feature or behavior questions, or `search_code(pattern="...")` / `find_files(pattern="...")` when you already have strong terms.
4. Narrow with `get_file_summary(path="...")` and then read exact implementations with `get_symbol_body(file="...", symbol="...")`.
5. Use shell search only after Barnacle has narrowed the area, or immediately for exact identifier, exact string, or exact path lookup.

Never call `get_index_status()`, `semantic_search()`, `find_files()`, `search_code()`, `get_file_summary()`, or `get_symbol_body()` before `set_project_path()`.

If Barnacle results are low-signal, the index is not ready, or the task is an exact string/path lookup, fall back to shell search immediately.
<!-- barnacle-search:claude-guidance:end -->
"@

    if (Test-Path $ClaudeMemory) {
        $memoryConfig = Get-Content $ClaudeMemory -Raw
    } else {
        $memoryConfig = ""
    }

    $memoryPattern = '(?ms)<!-- barnacle-search:claude-guidance:start -->\r?\n.*?<!-- barnacle-search:claude-guidance:end -->'
    if ($memoryConfig -match $memoryPattern) {
        $updatedMemoryConfig = [regex]::Replace($memoryConfig, $memoryPattern, $memoryBlock).Trim()
        $updatedMemoryConfig += "`r`n"
    } elseif ([string]::IsNullOrWhiteSpace($memoryConfig)) {
        $updatedMemoryConfig = $memoryBlock + "`r`n"
    } else {
        $updatedMemoryConfig = $memoryConfig.TrimEnd() + "`r`n`r`n" + $memoryBlock + "`r`n"
    }

    $claudeMemoryDir = Split-Path -Parent $ClaudeMemory
    if (-not (Test-Path $claudeMemoryDir)) {
        New-Item -ItemType Directory -Path $claudeMemoryDir | Out-Null
    }

    $updatedMemoryConfig | Set-Content $ClaudeMemory -Encoding UTF8
    Write-Host "Registered barnacle-search guidance in $ClaudeMemory"
}

# ── 7. Register MCP server in Codex ───────────────────────────────────────────

if ($InstallTarget -eq "codex" -or $InstallTarget -eq "both") {
    if (-not (Test-Path $CodexDir)) {
        New-Item -ItemType Directory -Path $CodexDir | Out-Null
    }

    $codexBlock = @"
[mcp_servers."barnacle-search"]
command = "uv"
args = ["--directory", "$RepoDir", "run", "barnacle-search"]
env = { UV_CACHE_DIR = "$env:TEMP\\barnacle-search-uv-cache" }
"@

    if (Test-Path $CodexToml) {
        $codexConfig = Get-Content $CodexToml -Raw
    } else {
        $codexConfig = ""
    }

    $pattern = '(?ms)^\[mcp_servers\."barnacle-search"\]\r?\n.*?(?=^\[[^\r\n]+\]\r?\n|\z)'
    if ($codexConfig -match $pattern) {
        $updatedCodexConfig = [regex]::Replace($codexConfig, $pattern, $codexBlock)
    } elseif ([string]::IsNullOrWhiteSpace($codexConfig)) {
        $updatedCodexConfig = $codexBlock
    } else {
        $updatedCodexConfig = $codexConfig.TrimEnd() + "`r`n`r`n" + $codexBlock
    }

    $updatedCodexConfig | Set-Content $CodexToml -Encoding UTF8
    Write-Host "Registered barnacle-search in $CodexToml"

    $agentsBlock = @"
<!-- barnacle-search:codex-guidance:start -->
## Barnacle Search

For exploratory codebase questions in a repository, use the `barnacle-search` MCP tools before shell search.

Required workflow:
1. Call `set_project_path("/absolute/path/to/repo")` before any other Barnacle tool.
2. If the deep index has not been built yet, call `build_deep_index()` when semantic or symbol-aware search will help.
3. Start exploration with `semantic_search(query="...")` for feature or behavior questions, or `search_code(pattern="...")` / `find_files(pattern="...")` when you already have strong terms.
4. Narrow with `get_file_summary(path="...")` and then read exact implementations with `get_symbol_body(file="...", symbol="...")`.
5. Use `rg` and `rg --files` only after Barnacle has narrowed the area, or immediately for exact identifier, exact string, or exact path lookup.

Never call `get_index_status()`, `semantic_search()`, `find_files()`, `search_code()`, `get_file_summary()`, or `get_symbol_body()` before `set_project_path()`.

If Barnacle results are low-signal, the index is not ready, or the user asks for an exact string/path lookup, fall back to `rg` immediately.
<!-- barnacle-search:codex-guidance:end -->
"@

    if (Test-Path $CodexAgents) {
        $agentsConfig = Get-Content $CodexAgents -Raw
    } else {
        $agentsConfig = ""
    }

    $agentsPattern = '(?ms)<!-- barnacle-search:codex-guidance:start -->\r?\n.*?<!-- barnacle-search:codex-guidance:end -->'
    if ($agentsConfig -match $agentsPattern) {
        $updatedAgentsConfig = [regex]::Replace($agentsConfig, $agentsPattern, $agentsBlock).Trim()
        $updatedAgentsConfig += "`r`n"
    } elseif ([string]::IsNullOrWhiteSpace($agentsConfig)) {
        $updatedAgentsConfig = $agentsBlock + "`r`n"
    } else {
        $updatedAgentsConfig = $agentsConfig.TrimEnd() + "`r`n`r`n" + $agentsBlock + "`r`n"
    }

    $updatedAgentsConfig | Set-Content $CodexAgents -Encoding UTF8
    Write-Host "Registered barnacle-search guidance in $CodexAgents"
}

# ── 8. Pull Ollama embedding model if available ───────────────────────────────

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Pulling Ollama embedding model ($EmbedModel)..."
    ollama pull $EmbedModel
} else {
    Write-Host "Ollama not found; skipping model pull."
}

# ── 9. Done ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "barnacle-search is ready!"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Claude Code and/or Codex to pick up the new MCP server"
Write-Host "  2. In any project, run:"
Write-Host "       set_project_path(`"/path/to/your/project`")"
Write-Host "       build_deep_index()"
Write-Host ""
Write-Host "Requires Ollama for semantic search:"
Write-Host "  winget install Ollama.Ollama"
Write-Host "  ollama pull $EmbedModel"
