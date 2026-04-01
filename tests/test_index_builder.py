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
