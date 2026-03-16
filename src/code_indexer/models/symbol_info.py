from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SymbolInfo:
    """Represents a named symbol extracted from a source file."""
    type: str           # class | method | function | interface | enum |
                        # property | field | type_alias | tag |
                        # element_id | script_ref | style_ref | form_field
    name: str           # short name (e.g. "MethodName")
    symbol_id: str      # globally unique: "rel/path.cs::ClassName.MethodName"
    file: str           # absolute file path
    line: int
    end_line: Optional[int] = None
    signature: Optional[str] = None   # full signature / attribute value
    docstring: Optional[str] = None
    parent: Optional[str] = None      # containing class/namespace name
