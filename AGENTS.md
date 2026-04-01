# Barnacle Search Guidance

- For exploratory codebase questions, prefer Barnacle tools first.
- Start exploratory Barnacle work with `semantic_search` by default. Use it when the user asks where or how a feature works, names a subsystem or behavior instead of an exact file or symbol, or when concept-based discovery is more useful than exact text matching.
- Use `search_code` or `find_files` first only when you already have a strong exact term, exact identifier, exact string, or exact path pattern.
- Use `rg` and `rg --files` first for exact identifier lookup, exact string search, exact path lookup, and quick verification after Barnacle has narrowed the area.
- If Barnacle results are low-signal or the index is not ready, fall back to `rg` immediately.
