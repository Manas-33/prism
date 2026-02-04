from collections import defaultdict
from typing import Dict, List, Set
from app.models import FileIndex
from app.static_analysis import parse_code, walk, resolve_import
import os, subprocess
from app.confidence import compute_confidence, confidence_label
from app.llm_service import explain_impact

CALL_SNIPPET_WINDOW = 4

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

def build_file_graph(repo_dir, repo_index):
    module_root = detect_module_root(repo_dir)
    graph = defaultdict(set)
    for file_path, fi in repo_index.items():
        for imp in fi.imports:
            target = resolve_import(
                import_stmt=imp,
                current_file=file_path,
                module_root=module_root,
            )
            if target:
                graph[file_path].add(target)

    return graph

# def extract_function_calls(tree):
#     calls = []

#     for node in walk(tree.root_node):
#         if node.type == "call":
#             fn = node.child_by_field_name("function")
#             if fn:
#                 calls.append(fn.text.decode())

#     return calls

# def build_symbol_graph(repo_index,repo_dir):
#     symbol_graph = defaultdict(lambda: defaultdict(int))

#     symbol_lookup = {}
#     for fi in repo_index.values():
#         for sym in fi.symbols:
#             symbol_lookup[sym.name] = symbol_id(sym)

#     for fi in repo_index.values():
        
#         abs_path = os.path.join(repo_dir, fi.path)
#         if not os.path.exists(abs_path):
#             continue
#         code = open(abs_path).read()
#         tree = parse_code(code)
#         calls = extract_function_calls(tree)
#         print("CALLS IN", fi.path, "→", calls)
        
#         for call in calls:
#             name = call.split(".")[-1]
#             if name in symbol_lookup:
#                 sid = symbol_lookup[name]
#                 symbol_graph[fi.path][sid] += 1

#     return symbol_graph

def extract_function_calls(tree):
    calls = []

    for node in walk(tree.root_node):
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is None:
                continue

            name = fn.text.decode()
            line = node.start_point[0] + 1 

            calls.append({
                "name": name,
                "line": line,
            })

    return calls

def build_symbol_graph(repo_dir, repo_index):
    symbol_graph = defaultdict(lambda: defaultdict(lambda: {
        "count": 0,
        "lines": [],
    }))

    symbol_lookup = {}
    for fi in repo_index.values():
        for sym in fi.symbols:
            symbol_lookup[sym.name] = symbol_id(sym)

    for fi in repo_index.values():
        abs_path = os.path.join(repo_dir, fi.path)
        if not os.path.exists(abs_path):
            continue

        code = open(abs_path, "r", encoding="utf-8").read()
        tree = parse_code(code)
        calls = extract_function_calls(tree)

        for call in calls:
            name = call["name"].split(".")[-1]
            if name not in symbol_lookup:
                continue

            sid = symbol_lookup[name]
            entry = symbol_graph[fi.path][sid]

            entry["count"] += 1
            entry["lines"].append(call["line"])

    return symbol_graph

# def find_impacted_files(changed_symbols, symbol_graph):
#     impacted = set()
#     changed_names = {name for _, name in changed_symbols}
#     for caller_file, called_symbols in symbol_graph.items():
#         for sym in called_symbols:
#             _, _, name = sym.split(":")
#             if name in changed_names:
#                 impacted.add(caller_file)
#     return impacted

# def find_impacts_with_confidence(changed_symbols, symbol_graph):
    
#     changed_names = {name for _, name in changed_symbols}
#     impacts = []
    
#     for file_path, callees in symbol_graph.items():
#         for sid, count in callees.items():
#             _, _, name = sid.split(":")
#             if name in changed_names:
#                 score = compute_confidence(file_path, name, count)
#                 label = confidence_label(score)
#                 impacts.append({
#                     "file": file_path,
#                     "symbol": name,
#                     "call_count": count,
#                     "score": score,
#                     "label": label,
#                 })
#     return impacts

def detect_module_root(repo_dir):
    src = os.path.join(repo_dir, "src")
    if os.path.exists(src):
        return src
    return repo_dir

def extract_code_snippet(path: str, start_line: int, end_line: int) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    
    start = max(1, start_line)
    end = min(len(lines), end_line)
    return "\n".join(lines[start-1:end]) if start <= end else ""

def extract_call_site_snippet(path, line_no, window=4):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    start = max(1, line_no - window)
    end = min(len(lines), line_no + window)
    return "\n".join(lines[start-1:end])

def find_impacts_with_confidence_and_context(
    changed_symbols,
    symbol_graph,
    repo_dir,
    repo_index,
    base_sha,
):
    changed_names = {name for _, name in changed_symbols}
    impacts = []

    for file_path, callees in symbol_graph.items():
        for sid, meta in callees.items():
            _, _, name = sid.split(":")

            if name not in changed_names:
                continue

            count = meta["count"]
            lines = meta["lines"]

            score = compute_confidence(file_path, name, count)
            label = confidence_label(score)

            abs_impacted = os.path.join(repo_dir, file_path)

            call_site_line = lines[0]
            call_site_code = extract_call_site_snippet(
                abs_impacted,
                call_site_line,
                CALL_SNIPPET_WINDOW,
            )

            after_code = ""
            before_code = ""

            for fi_path, fi in repo_index.items():
                for sym in fi.symbols:
                    if sym.name == name:
                        abs_def_path = os.path.join(repo_dir, fi_path)
                        after_code = extract_code_snippet(
                            abs_def_path,
                            sym.start,
                            sym.end,
                        )
                        base_text = git_show_file(
                            repo_dir,
                            base_sha,
                            fi_path,
                        )
                        before_code = extract_code_snippet_from_text(
                            base_text,
                            sym.start,
                            sym.end,
                        )
                        break
                if after_code:
                    break

            impacts.append({
                "file": file_path,
                "symbol": name,
                "call_count": count,
                "call_site_line": call_site_line,
                "call_site_code": call_site_code,
                "after_code": after_code,
                "before_code": before_code,
                "score": score,
                "label": label,
            })

    impacts.sort(key=lambda x: x["score"], reverse=True)
    return impacts

def git_show_file(repo_dir:str, commit_sha:str, file_path:str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit_sha}:{file_path}"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout
    

def extract_code_snippet_from_text(
    text: str,
    start_line: int,
    end_line: int,
) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)

    if start > end:
        return ""

    return "\n".join(lines[start - 1 : end])