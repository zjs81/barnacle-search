from code_indexer.indexing.deep_index import DeepIndex, _mtime_changed
from code_indexer.indexing.strategies.factory import StrategyFactory


def test_get_symbol_body_matches_unqualified_csharp_method_name(tmp_path):
    source = """\
namespace MyApp.Services
{
    internal static class DocumentManager
    {
        public static string GetOnlyOfficeURL(string email)
        {
            return email;
        }
    }
}
"""
    file_path = tmp_path / "DocumentManager.cs"
    file_path.write_text(source, encoding="utf-8")

    deep = DeepIndex(str(tmp_path), str(tmp_path / "index.bin"), StrategyFactory())
    deep.build(force_rebuild=True)

    body = deep.get_symbol_body(str(file_path), "GetOnlyOfficeURL")

    assert body is not None
    assert "GetOnlyOfficeURL" in body
    assert "return email;" in body


def test_mtime_changed_ignores_float_noise():
    assert _mtime_changed(100.1234564, 100.12345649) is False
    assert _mtime_changed(100.123456, 100.123457) is True


def test_rebuild_file_refreshes_shared_snapshot_before_mutation(tmp_path):
    foo_path = tmp_path / "foo.py"
    foo_path.write_text(
        """\
def foo():
    return 1
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "index.bin"

    deep_a = DeepIndex(str(tmp_path), str(db_path), StrategyFactory())
    deep_b = DeepIndex(str(tmp_path), str(db_path), StrategyFactory())

    deep_a.build(force_rebuild=True)

    bar_path = tmp_path / "bar.py"
    bar_path.write_text(
        """\
def bar():
    return 2
""",
        encoding="utf-8",
    )

    assert deep_b.rebuild_file(str(bar_path)) is True

    reloaded = DeepIndex(str(tmp_path), str(db_path), StrategyFactory())
    assert reloaded.get_file_summary(str(foo_path)) is not None
    assert reloaded.get_file_summary(str(bar_path)) is not None
