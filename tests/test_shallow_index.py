"""Tests for ShallowIndex — file discovery, glob matching, and persistence."""

import json
import os
from pathlib import Path

import pytest

from code_indexer.indexing.shallow_index import ShallowIndex


@pytest.fixture
def project(tmp_path):
    """Create a small fake project tree."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "src" / "utils.ts").write_text("export function foo() {}")
    (tmp_path / "src" / "style.html").write_text("<html/>")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lodash.js").write_text("// excluded")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00\x01")
    return tmp_path


class TestShallowIndexBuild:
    def test_finds_supported_files(self, project):
        idx = ShallowIndex().build(str(project))
        paths = idx.get_all_paths()
        names = {Path(p).name for p in paths}
        assert "main.py" in names
        assert "utils.ts" in names
        assert "style.html" in names

    def test_excludes_node_modules(self, project):
        idx = ShallowIndex().build(str(project))
        paths = idx.get_all_paths()
        # Check for the directory component, not a substring of the path
        assert not any(
            "node_modules" in Path(p).parts for p in paths
        )

    def test_excludes_pycache(self, project):
        idx = ShallowIndex().build(str(project))
        paths = idx.get_all_paths()
        assert not any("__pycache__" in p for p in paths)

    def test_excludes_unsupported_extensions(self, project):
        (project / "notes.txt").write_text("notes")
        idx = ShallowIndex().build(str(project))
        paths = idx.get_all_paths()
        assert not any(p.endswith(".txt") for p in paths)

    def test_get_stats(self, project):
        idx = ShallowIndex().build(str(project))
        stats = idx.get_stats()
        assert stats["total"] == 3
        assert stats["by_language"]["python"] == 1
        assert stats["by_language"]["typescript"] == 1
        assert stats["by_language"]["html"] == 1


class TestShallowIndexGlob:
    def test_glob_by_extension(self, project):
        idx = ShallowIndex().build(str(project))
        results = idx.find_files("**/*.py")
        assert len(results) == 1
        assert results[0].endswith("main.py")

    def test_glob_by_name_pattern(self, project):
        idx = ShallowIndex().build(str(project))
        results = idx.find_files("**/utils.*")
        assert len(results) == 1

    def test_glob_no_match_returns_empty(self, project):
        idx = ShallowIndex().build(str(project))
        assert idx.find_files("**/*.cs") == []

    def test_glob_all_files(self, project):
        idx = ShallowIndex().build(str(project))
        assert len(idx.find_files("**/*")) == 3


class TestShallowIndexPersistence:
    def test_save_and_load_roundtrip(self, project, tmp_path):
        idx = ShallowIndex().build(str(project))
        cache_path = str(tmp_path / "shallow.json")
        idx.save(cache_path)

        idx2 = ShallowIndex().load(cache_path)
        assert set(idx2.get_all_paths()) == set(idx.get_all_paths())

    def test_save_creates_valid_json(self, project, tmp_path):
        idx = ShallowIndex().build(str(project))
        cache_path = str(tmp_path / "shallow.json")
        idx.save(cache_path)
        with open(cache_path) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_loaded_stats_match(self, project, tmp_path):
        idx = ShallowIndex().build(str(project))
        cache_path = str(tmp_path / "shallow.json")
        idx.save(cache_path)

        idx2 = ShallowIndex().load(cache_path)
        assert idx2.get_stats() == idx.get_stats()


class TestShallowIndexNeedsRebuild:
    def test_new_file_needs_rebuild(self, project):
        idx = ShallowIndex().build(str(project))
        new_file = str(project / "new.py")
        assert idx.needs_rebuild(new_file) is True

    def test_unchanged_file_does_not_need_rebuild(self, project):
        idx = ShallowIndex().build(str(project))
        py_file = str(project / "src" / "main.py")
        assert idx.needs_rebuild(py_file) is False
