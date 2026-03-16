"""
Build the tree-sitter-dart shared library from source.

Run directly:
    python build_dart_grammar.py

Or called automatically by dart.py on first use if the .so is missing.
Requires: git, a C compiler (gcc or clang).
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile

# Output path — alongside this file
_HERE = os.path.dirname(os.path.abspath(__file__))
_GRAMMAR_REPO = "https://github.com/UserNobody14/tree-sitter-dart.git"

DART_SO_NAME = {
    "Darwin": "tree-sitter-dart.dylib",
    "Linux": "tree-sitter-dart.so",
    "Windows": "tree-sitter-dart.dll",
}.get(platform.system(), "tree-sitter-dart.so")

DART_SO_PATH = os.path.join(_HERE, DART_SO_NAME)


def _find_compiler() -> str:
    for cc in ("gcc", "clang", "cc"):
        if shutil.which(cc):
            return cc
    raise RuntimeError(
        "No C compiler found. Install gcc or clang:\n"
        "  macOS:  xcode-select --install\n"
        "  Ubuntu: sudo apt install gcc\n"
        "  Windows: install MinGW or MSVC"
    )


def build(output_path: str = DART_SO_PATH) -> str:
    """
    Clone tree-sitter-dart and compile it to a shared library.
    Returns the path to the compiled library.
    """
    compiler = _find_compiler()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "tree-sitter-dart")

        print(f"Cloning tree-sitter-dart grammar...", flush=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", _GRAMMAR_REPO, repo_dir],
            check=True,
            capture_output=True,
        )

        src_dir = os.path.join(repo_dir, "src")
        parser_c = os.path.join(src_dir, "parser.c")
        scanner_c = os.path.join(src_dir, "scanner.c")

        sources = [parser_c]
        if os.path.exists(scanner_c):
            sources.append(scanner_c)

        system = platform.system()
        if system == "Windows":
            cmd = [
                compiler, "-O2", "-shared",
                f"-I{src_dir}",
                *sources,
                "-o", output_path,
            ]
        else:
            cmd = [
                compiler, "-O2", "-shared", "-fPIC",
                f"-I{src_dir}",
                *sources,
                "-o", output_path,
            ]

        print(f"Compiling with {compiler}...", flush=True)
        subprocess.run(cmd, check=True, capture_output=True)

    print(f"Built: {output_path}")
    return output_path


if __name__ == "__main__":
    try:
        build()
    except subprocess.CalledProcessError as e:
        print(f"Build failed:\n{e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
