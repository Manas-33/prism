from dataclasses import dataclass
from typing import List

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
    
def serialize_repo_index(repo_index: FileIndex) -> dict:
    return {
       path:{
           "imports": fi.imports,
           "symbols": [
               {
                    "name": sym.name,
                    "kind": sym.kind,
                    "file": sym.file,
                    "start": sym.start,
                    "end": sym.end
               }
               for sym in fi.symbols
           ]
       }
       for path, fi in repo_index.items()
    }
    
def deserialize_repo_index(data: dict) -> FileIndex:
    index = {}
    for path, fi_data in data.items():
        symbols = [
            Symbol(
                **sym
            )
            for sym in fi_data["symbols"]
        ]
        index[path] = FileIndex(
            path=path,
            imports=fi_data["imports"],
            symbols=symbols
        )
    return index

def serialize_symbol_graph(symbol_graph):
    return {k: list(v) for k, v in symbol_graph.items()}

def deserialize_symbol_graph(data):
    return {k: set(v) for k, v in data.items()}

