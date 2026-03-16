"""StrategyFactory: maps file extensions to the appropriate ParsingStrategy."""

from pathlib import Path
from typing import Optional

from .base import ParsingStrategy
from .csharp import CSharpStrategy
from .javascript import JavaScriptStrategy
from .typescript import TypeScriptStrategy
from .html import HtmlStrategy
from .python import PythonStrategy
from .dart import DartStrategy


class StrategyFactory:
    """
    Central registry of all available language parsing strategies.

    Strategies are instantiated once at construction time and reused across
    all parse calls (each strategy is internally thread-safe via
    threading.local() parsers).
    """

    def __init__(self) -> None:
        # language_name → strategy
        self._strategies: dict[str, ParsingStrategy] = {}
        # file extension (lower-case, with dot) → strategy
        self._ext_map: dict[str, ParsingStrategy] = {}

        self._register(CSharpStrategy())
        self._register(JavaScriptStrategy())
        self._register(TypeScriptStrategy())
        self._register(HtmlStrategy())
        self._register(PythonStrategy())
        self._register(DartStrategy())

    def _register(self, strategy: ParsingStrategy) -> None:
        self._strategies[strategy.get_language_name()] = strategy
        for ext in strategy.get_supported_extensions():
            self._ext_map[ext] = strategy

    def get_strategy(self, file_path: str) -> Optional[ParsingStrategy]:
        """Return the strategy for *file_path* based on its extension, or None."""
        ext = Path(file_path).suffix.lower()
        return self._ext_map.get(ext)

    def get_all_strategies(self) -> list[ParsingStrategy]:
        """Return a list of all registered strategy instances."""
        return list(self._strategies.values())
