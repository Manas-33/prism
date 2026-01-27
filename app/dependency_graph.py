from collections import defaultdict
from typing import Dict, List, Set
from app.repo_index import FileIndex
from app.static_analysis import parse_code, walk
import os 

def symbol_id(sym):
    return f"{sym.file}:{sym.kind}:{sym.name}"

def resolve_import_to_file(import_stmt: str) -> str | None:
    if import_stmt.startswith("from "):
        module = import_stmt.split(" ")[1]
    elif import_stmt.startswith("import "):
        module = import_stmt.split(" ")[1].split(".")[0]
    else:
        return None
    return module.replace(".", "/") + ".py"

def build_file_graph(repo_index: Dict[str, FileIndex]):
    graph = defaultdict(set)
    for file_path, file_index in repo_index.items():
        for import_stmt in file_index.imports:
            imported_file = resolve_import_to_file(import_stmt)
            if imported_file and imported_file in repo_index:
                graph[file_path].add(imported_file)
    return graph

def extract_function_calls(tree):
    calls = []

    for node in walk(tree.root_node):
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn:
                calls.append(fn.text.decode())

    return calls

def build_symbol_graph(repo_index,repo_dir):
    symbol_graph = defaultdict(set)

    symbol_lookup = {}
    for fi in repo_index.values():
        for sym in fi.symbols:
            symbol_lookup[sym.name] = symbol_id(sym)

    for fi in repo_index.values():
        
        abs_path = os.path.join(repo_dir, fi.path)
        if not os.path.exists(abs_path):
            continue
        code = open(abs_path).read()
        tree = parse_code(code)
        calls = extract_function_calls(tree)
        print("CALLS IN", fi.path, "→", calls)
        for call in calls:
            name = call.split(".")[-1]
            if name in symbol_lookup:
                symbol_graph[fi.path].add(symbol_lookup[name])

    return symbol_graph

def find_impacted_files(changed_symbols, symbol_graph):
    impacted = set()
    changed_names = {name for _, name in changed_symbols}
    for caller_file, called_symbols in symbol_graph.items():
        for sym in called_symbols:
            _, _, name = sym.split(":")
            if name in changed_names:
                impacted.add(caller_file)
    return impacted


