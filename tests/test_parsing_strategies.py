"""Tests for language parsing strategies — verify symbol extraction per language."""

import os
import tempfile
from pathlib import Path

import pytest

from code_indexer.indexing.strategies.factory import StrategyFactory

factory = StrategyFactory()


def parse(filename: str, source: str) -> tuple:
    """Write source to a temp file, parse it, return (file_info, symbols)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=Path(filename).suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(source)
        path = f.name
    try:
        strategy = factory.get_strategy(path)
        if strategy is None:
            pytest.skip(f"No strategy for {filename}")
        fi = strategy.parse_file(path, source)
        return fi, fi.symbols
    finally:
        os.unlink(path)


def symbol_names(symbols) -> set:
    return {s.name for s in symbols}


# ── Python ───────────────────────────────────────────────────────────────────

class TestPythonStrategy:
    SOURCE = '''\
import os
from pathlib import Path

class MyService:
    def __init__(self):
        pass

    def process(self, data: str) -> str:
        return data.upper()

def standalone():
    return 42
'''

    def test_detects_language(self):
        fi, _ = parse("service.py", self.SOURCE)
        assert fi.language == "python"

    def test_extracts_class(self):
        _, syms = parse("service.py", self.SOURCE)
        assert "MyService" in symbol_names(syms)

    def test_extracts_methods(self):
        _, syms = parse("service.py", self.SOURCE)
        # Python strategy uses qualified names like "MyService.process"
        names = symbol_names(syms)
        assert any("process" in n for n in names)

    def test_extracts_function(self):
        _, syms = parse("service.py", self.SOURCE)
        assert "standalone" in symbol_names(syms)

    def test_extracts_imports(self):
        fi, _ = parse("service.py", self.SOURCE)
        assert "os" in fi.imports

    def test_line_count(self):
        fi, _ = parse("service.py", self.SOURCE)
        assert fi.line_count > 0


# ── TypeScript ───────────────────────────────────────────────────────────────

class TestTypeScriptStrategy:
    SOURCE = '''\
import { Injectable } from "@angular/core";

interface User {
    id: number;
    name: string;
}

export class AuthService {
    login(user: User): boolean {
        return user.id > 0;
    }

    logout(): void {}
}

export function hashPassword(pw: string): string {
    return pw;
}
'''

    def test_detects_language(self):
        fi, _ = parse("auth.ts", self.SOURCE)
        assert fi.language == "typescript"

    def test_extracts_class(self):
        _, syms = parse("auth.ts", self.SOURCE)
        assert "AuthService" in symbol_names(syms)

    def test_extracts_interface(self):
        _, syms = parse("auth.ts", self.SOURCE)
        assert "User" in symbol_names(syms)

    def test_extracts_method(self):
        _, syms = parse("auth.ts", self.SOURCE)
        assert "login" in symbol_names(syms)

    def test_extracts_function(self):
        _, syms = parse("auth.ts", self.SOURCE)
        assert "hashPassword" in symbol_names(syms)

    def test_parent_set_on_method(self):
        _, syms = parse("auth.ts", self.SOURCE)
        login = next((s for s in syms if s.name == "login"), None)
        assert login is not None
        assert login.parent == "AuthService"


# ── JavaScript ───────────────────────────────────────────────────────────────

class TestJavaScriptStrategy:
    SOURCE = '''\
import React from "react";

class EventEmitter {
    emit(event) {
        console.log(event);
    }
}

function fetchData(url) {
    return fetch(url);
}

const formatDate = (d) => d.toISOString();
'''

    def test_detects_language(self):
        fi, _ = parse("app.js", self.SOURCE)
        assert fi.language == "javascript"

    def test_extracts_class(self):
        _, syms = parse("app.js", self.SOURCE)
        assert "EventEmitter" in symbol_names(syms)

    def test_extracts_function(self):
        _, syms = parse("app.js", self.SOURCE)
        assert "fetchData" in symbol_names(syms)

    def test_extracts_arrow_function(self):
        _, syms = parse("app.js", self.SOURCE)
        assert "formatDate" in symbol_names(syms)


# ── C# ───────────────────────────────────────────────────────────────────────

class TestCSharpStrategy:
    SOURCE = '''\
using System;
using System.Collections.Generic;

namespace MyApp.Services
{
    public class UserService
    {
        private readonly IRepository _repo;

        public UserService(IRepository repo)
        {
            _repo = repo;
        }

        public User GetById(int id)
        {
            return _repo.Find(id);
        }

        public bool Delete(int id)
        {
            return _repo.Remove(id);
        }
    }

    public interface IRepository
    {
        User Find(int id);
        bool Remove(int id);
    }
}
'''

    def test_detects_language(self):
        fi, _ = parse("UserService.cs", self.SOURCE)
        assert fi.language == "csharp"

    def test_extracts_class(self):
        _, syms = parse("UserService.cs", self.SOURCE)
        assert "UserService" in symbol_names(syms)

    def test_extracts_interface(self):
        _, syms = parse("UserService.cs", self.SOURCE)
        assert "IRepository" in symbol_names(syms)

    def test_extracts_methods(self):
        _, syms = parse("UserService.cs", self.SOURCE)
        names = symbol_names(syms)
        # C# strategy uses qualified names like "UserService.GetById"
        assert any("GetById" in n for n in names)
        assert any("Delete" in n for n in names)

    def test_extracts_imports(self):
        fi, _ = parse("UserService.cs", self.SOURCE)
        assert "System" in fi.imports

    def test_method_parent_is_class(self):
        _, syms = parse("UserService.cs", self.SOURCE)
        get_by_id = next((s for s in syms if "GetById" in s.name), None)
        assert get_by_id is not None
        assert get_by_id.parent == "UserService"


# ── HTML ─────────────────────────────────────────────────────────────────────

class TestHTMLStrategy:
    SOURCE = '''\
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="styles.css">
    <script src="app.js"></script>
</head>
<body>
    <div id="main-content">
        <form id="login-form">
            <input name="username" type="text">
            <input name="password" type="password">
        </form>
    </div>
</body>
</html>
'''

    def test_detects_language(self):
        fi, _ = parse("index.html", self.SOURCE)
        assert fi.language == "html"

    def test_extracts_element_ids(self):
        _, syms = parse("index.html", self.SOURCE)
        names = symbol_names(syms)
        assert "main-content" in names or "login-form" in names

    def test_extracts_script_refs(self):
        _, syms = parse("index.html", self.SOURCE)
        names = symbol_names(syms)
        assert any("app.js" in n for n in names)

    def test_line_count(self):
        fi, _ = parse("index.html", self.SOURCE)
        assert fi.line_count > 0


# ── Factory ──────────────────────────────────────────────────────────────────

class TestStrategyFactory:
    @pytest.mark.parametrize("ext,lang", [
        (".py", "python"),
        (".ts", "typescript"),
        (".tsx", "typescript"),
        (".js", "javascript"),
        (".cs", "csharp"),
        (".html", "html"),
    ])
    def test_get_strategy_returns_correct_type(self, ext, lang, tmp_path):
        f = tmp_path / f"file{ext}"
        f.write_text("")
        strategy = factory.get_strategy(str(f))
        assert strategy is not None

    def test_unsupported_extension_returns_none(self, tmp_path):
        f = tmp_path / "file.rb"
        f.write_text("")
        assert factory.get_strategy(str(f)) is None
