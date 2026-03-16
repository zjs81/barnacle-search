from dataclasses import dataclass, field
from typing import Optional
from .symbol_info import SymbolInfo


@dataclass
class FileInfo:
    """Metadata and symbols extracted from a single source file."""
    path: str           # absolute path
    language: str       # csharp | javascript | typescript | html
    line_count: int
    mtime: float        # os.path.getmtime() at parse time
    symbols: list[SymbolInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    error: Optional[str] = None   # set if parsing failed
