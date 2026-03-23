from code_indexer.indexing.index_builder import IndexBuilder
from code_indexer.indexing.sqlite_store import SQLiteStore


class _DummyFactory:
    def get_strategy(self, _file_path: str):
        return None


def test_build_symbol_embed_text_caps_body_to_510_tokens(tmp_path):
    file_path = tmp_path / "sample.py"
    token_count = 700
    content = " ".join(f"tok{i}" for i in range(token_count))
    file_path.write_text(content, encoding="utf-8")

    builder = IndexBuilder(str(tmp_path), SQLiteStore(str(tmp_path / "index.db")), _DummyFactory())
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
