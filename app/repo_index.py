from dataclasses import dataclass
from typing import Dict, List

@dataclass
class Symbol:
    name: str
    kind: str
    file: str
    start: int
    end: int

@dataclass
class FileIndex:
    path: str
    imports: List[str]
    symbols: List[Symbol]
    
