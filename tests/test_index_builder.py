from code_indexer.indexing.index_builder import IndexBuilder
from code_indexer.indexing.snapshot_store import SnapshotStore


class _DummyFactory:
    def get_strategy(self, _file_path: str):
        return None


def test_build_symbol_embed_text_caps_body_to_510_tokens(tmp_path):
    file_path = tmp_path / "sample.py"
    token_count = 700
    content = " ".join(f"tok{i}" for i in range(token_count))
    file_path.write_text(content, encoding="utf-8")

    builder = IndexBuilder(str(tmp_path), SnapshotStore(str(tmp_path / "index.bin")), _DummyFactory())
    text = builder.build_symbol_embed_text(
        {
            "short_name": "f",
            "language": "python",
            "line": 1,
            "end_line": 1,
        },
        str(file_path),
        [content],
    )

    body = text.splitlines()[-1]
    assert len(body.split()) == 510


def test_build_symbol_embed_text_uses_cached_body_text(tmp_path):
    file_path = tmp_path / "missing.py"

    builder = IndexBuilder(str(tmp_path), SnapshotStore(str(tmp_path / "index.bin")), _DummyFactory())
    text = builder.build_symbol_embed_text(
        {
            "short_name": "f",
            "language": "python",
            "line": 1,
            "end_line": 3,
            "body_text": "cached body text",
        },
        str(file_path),
    )

    assert text.splitlines()[-1] == "cached body text"


def test_partition_files_evenly_balances_work(tmp_path):
    builder = IndexBuilder(str(tmp_path), SnapshotStore(str(tmp_path / "index.bin")), _DummyFactory())

    partitions = builder._partition_files_evenly(
        [f"file_{index}.py" for index in range(10)],
        4,
    )

    lengths = sorted(len(partition) for partition in partitions)
    assert lengths == [2, 2, 3, 3]


def test_sort_files_by_size_desc_balances_large_files_first(tmp_path):
    builder = IndexBuilder(str(tmp_path), SnapshotStore(str(tmp_path / "index.bin")), _DummyFactory())
    small = tmp_path / "small.py"
    medium = tmp_path / "medium.py"
    large = tmp_path / "large.py"
    small.write_text("x\n", encoding="utf-8")
    medium.write_text("x\n" * 5, encoding="utf-8")
    large.write_text("x\n" * 20, encoding="utf-8")

    sorted_files = builder._sort_files_by_size_desc([str(small), str(large), str(medium)])

    assert sorted_files == [str(large), str(medium), str(small)]


def test_build_files_merges_after_parallel_parse(monkeypatch, tmp_path):
    builder = IndexBuilder(str(tmp_path), SnapshotStore(str(tmp_path / "index.bin")), _DummyFactory())
    files = [str(tmp_path / f"file_{index}.py") for index in range(4)]
    for file_path in files:
        tmp_path.joinpath(file_path.split("/")[-1]).write_text("x = 1\n", encoding="utf-8")

    persisted: list[str] = []
    parse_order: list[str] = []

    def fake_process_file(path: str):
        parse_order.append(path)
        from code_indexer.models.file_info import FileInfo
        file_info = FileInfo(
            path=path,
            language="python",
            line_count=1,
            mtime=1.0,
        )
        return file_info, []

    def fake_persist(file_info, symbols, replace_existing=False, commit=False):
        persisted.append(file_info.path)

    monkeypatch.setattr(builder, "_process_file", fake_process_file)
    monkeypatch.setattr(builder.store, "persist_file_and_symbols", fake_persist)

    stats = builder.build_files(files)

    assert stats == {"files": 4, "symbols": 0, "errors": 0}
    assert sorted(parse_order) == sorted(files)
    assert sorted(persisted) == sorted(files)
