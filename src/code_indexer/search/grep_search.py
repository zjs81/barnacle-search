import subprocess
import shutil
import logging
import json
from pathlib import Path
from typing import Optional
from ..constants import SUPPORTED_EXTENSIONS, EXCLUDE_DIRS

logger = logging.getLogger(__name__)


def _find_search_tool() -> Optional[str]:
    """Return path to rg, ag, or grep (in preference order)."""
    for tool in ["rg", "ag", "grep"]:
        path = shutil.which(tool)
        if path:
            return tool
    return None


_SEARCH_TOOL: Optional[str] = _find_search_tool()


def _parse_rg_output(output: str, project_path: str) -> list[dict]:
    """Parse ripgrep --json output into result dicts."""
    results: list[dict] = []
    project_root = Path(project_path)

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if obj.get("type") != "match":
            continue

        data = obj.get("data", {})
        file_text = data.get("path", {}).get("text", "")
        line_number = data.get("line_number")
        match_text = data.get("lines", {}).get("text", "").rstrip("\n")

        if not file_text or line_number is None:
            continue

        try:
            rel_path = str(Path(file_text).relative_to(project_root))
        except ValueError:
            rel_path = file_text

        results.append({
            "file": rel_path,
            "line": int(line_number),
            "match": match_text,
        })

    return results


def _parse_grep_output(output: str, project_path: str) -> list[dict]:
    """Parse grep -rn output (file:line:match) into result dicts."""
    results: list[dict] = []
    project_root = Path(project_path)

    for line in output.splitlines():
        # Format: path/to/file.ts:42:matched content
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue

        file_part, line_part, match_part = parts[0], parts[1], parts[2]

        try:
            line_number = int(line_part)
        except ValueError:
            continue

        try:
            rel_path = str(Path(file_part).relative_to(project_root))
        except ValueError:
            rel_path = file_part

        results.append({
            "file": rel_path,
            "line": line_number,
            "match": match_part,
        })

    return results


def search_code(
    project_path: str,
    pattern: str,
    file_pattern: str = "*",
    max_results: int = 50,
    case_sensitive: bool = True,
) -> list[dict]:
    """
    Search for pattern in project_path using ripgrep/grep.

    Returns list of {"file": rel_path, "line": int, "match": str}

    file_pattern: glob pattern to filter files (e.g. "*.cs", "**/*.ts")
    """
    global _SEARCH_TOOL
    if _SEARCH_TOOL is None:
        _SEARCH_TOOL = _find_search_tool()
    if _SEARCH_TOOL is None:
        logger.warning("search_code: no search tool (rg, ag, grep) found on PATH")
        return []

    tool = _SEARCH_TOOL

    if tool == "rg":
        cmd: list[str] = [
            "rg",
            "--json",
            "-n",
            f"--max-count={max_results}",
        ]

        # Exclude dirs
        for excluded in EXCLUDE_DIRS:
            cmd.extend(["--glob", f"!{excluded}/**"])

        # File pattern filter
        if file_pattern and file_pattern != "*":
            cmd.extend(["--glob", file_pattern])

        if not case_sensitive:
            cmd.append("-i")

        cmd.append(pattern)
        cmd.append(project_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # rg exits with 1 when no matches, which is not an error
            if proc.returncode not in (0, 1):
                logger.warning(
                    "rg exited with code %d: %s", proc.returncode, proc.stderr.strip()
                )
            results = _parse_rg_output(proc.stdout, project_path)
            return results[:max_results]
        except subprocess.TimeoutExpired:
            logger.warning("search_code: rg timed out searching '%s'", project_path)
            return []
        except FileNotFoundError:
            logger.warning("search_code: rg not found, falling back")
            _SEARCH_TOOL = _find_search_tool() if shutil.which("ag") or shutil.which("grep") else None
            return search_code(project_path, pattern, file_pattern, max_results, case_sensitive)
        except Exception as exc:
            logger.warning("search_code: rg error: %s", exc)
            return []

    elif tool == "ag":
        cmd = ["ag", "--nocolor", "--numbers"]

        if not case_sensitive:
            cmd.append("-i")

        if file_pattern and file_pattern != "*":
            cmd.extend(["-G", file_pattern])

        cmd.append(pattern)
        cmd.append(project_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode not in (0, 1):
                logger.warning(
                    "ag exited with code %d: %s", proc.returncode, proc.stderr.strip()
                )
            results = _parse_grep_output(proc.stdout, project_path)
            return results[:max_results]
        except subprocess.TimeoutExpired:
            logger.warning("search_code: ag timed out searching '%s'", project_path)
            return []
        except Exception as exc:
            logger.warning("search_code: ag error: %s", exc)
            return []

    else:
        # grep fallback
        cmd = ["grep", "-rn"]

        if not case_sensitive:
            cmd.append("-i")

        if file_pattern and file_pattern != "*":
            cmd.extend([f"--include={file_pattern}"])

        # Exclude dirs
        for excluded in EXCLUDE_DIRS:
            cmd.extend([f"--exclude-dir={excluded}"])

        cmd.append(pattern)
        cmd.append(project_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # grep exits 1 when no matches
            if proc.returncode not in (0, 1):
                logger.warning(
                    "grep exited with code %d: %s", proc.returncode, proc.stderr.strip()
                )
            results = _parse_grep_output(proc.stdout, project_path)
            return results[:max_results]
        except subprocess.TimeoutExpired:
            logger.warning("search_code: grep timed out searching '%s'", project_path)
            return []
        except Exception as exc:
            logger.warning("search_code: grep error: %s", exc)
            return []
