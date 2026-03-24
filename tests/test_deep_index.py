from code_indexer.indexing.deep_index import DeepIndex
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

    deep = DeepIndex(str(tmp_path), str(tmp_path / "index.db"), StrategyFactory())
    deep.build(force_rebuild=True)

    body = deep.get_symbol_body(str(file_path), "GetOnlyOfficeURL")

    assert body is not None
    assert "GetOnlyOfficeURL" in body
    assert "return email;" in body
