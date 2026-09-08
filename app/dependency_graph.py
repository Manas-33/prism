from collections import defaultdict
from typing import Dict, List, Set
from app.models import FileIndex
from app.static_analysis import parse_code, walk, resolve_import
import os, subprocess, re
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

def _module_stem(path: str) -> str:
    base = os.path.basename(path)
    if base == "__init__.py":
        return os.path.basename(os.path.dirname(path))
    return base[:-3] if base.endswith(".py") else base

def _reachable_files(fi, all_files, defs_by_name):
    """Files a caller can reach for name resolution: itself + files it imports.

    An import is resolved two ways (either is enough), which is robust across
    flat vs src/ layouts and independent of import-statement parsing quirks:
      - by module stem: `import foo` / `from foo import x` -> a file named foo
      - by imported symbol: `from x import Bar` -> the file that defines Bar

    Importing anything from a module makes all of that module's symbols
    reachable (file-level granularity), which is what lets a call resolve to the
    right definition instead of every same-named symbol in the repo.
    """
    tokens = set()
    for imp in fi.imports:
        module = imp.get("module")
        if module:
            tokens.add(module.split(".")[-1])

    reachable = {fi.path}
    for f in all_files:
        if _module_stem(f) in tokens:
            reachable.add(f)
    for tok in tokens:
        reachable.update(defs_by_name.get(tok, {}).keys())
    return reachable

SIMPLE_NAME = re.compile(r"[A-Za-z_]\w*")


def is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _type_tokens(type_node):
    """Bare identifiers in a type annotation (e.g. `Optional[HTTPAdapter]` -> {Optional, HTTPAdapter})."""
    toks = set()
    for n in walk(type_node):
        if n.type in ("identifier", "dotted_name"):
            toks.add(n.text.decode().split(".")[-1])
    return toks


def _collect_var_types(tree):
    """name -> {annotated type identifiers} from params (`x: T`) and annotated assignments."""
    var_types = defaultdict(set)
    for node in walk(tree.root_node):
        if node.type in ("typed_parameter", "typed_default_parameter"):
            type_node = node.child_by_field_name("type")
            name_node = node.child_by_field_name("name")
            if name_node is None:
                name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node is not None and type_node is not None:
                var_types[name_node.text.decode()].update(_type_tokens(type_node))
        elif node.type == "assignment":
            type_node = node.child_by_field_name("type")
            left = node.child_by_field_name("left")
            if type_node is not None and left is not None and left.type == "identifier":
                var_types[left.text.decode()].update(_type_tokens(type_node))
    return var_types


def _resolve_call_targets(text, fi_path, reachable, defs_by_name, var_types, external_names=frozenset()):
    """Resolve a call expression to (symbol_id, precise) pairs, using the call's
    receiver instead of matching the bare method name everywhere.

    `precise` is True when we resolved to a single, determinate target (self, a
    typed/class/module receiver, or a uniquely-named bare call). It is False for
    the untyped import-scoped fallback or any ambiguous multi-owner match — those
    are the "shaky" edges that get their confidence down-weighted so they don't
    surface as High.
    """
    if "." in text:
        receiver_expr, method = text.rsplit(".", 1)
    else:
        receiver_expr, method = None, text

    # Dunders are invoked implicitly (construction, `with`, `len()`); the real
    # dependency is the class, already tracked via the class symbol.
    if is_dunder(method):
        return []

    candidates = defs_by_name.get(method)
    if not candidates:
        return []

    def tag(targets, resolved_precisely):
        # A resolution is only trustworthy when a precise rule pinned a *single*
        # owner; multiple owners means we're guessing among them.
        precise = resolved_precisely and len(targets) == 1
        return [(sid, precise) for sid in targets]

    if receiver_expr is None:
        # A bare name imported from a non-repo module (e.g. stdlib `Path`) shadows
        # any same-named repo symbol — don't attribute it to the repo symbol.
        if method in external_names:
            return []
        # otherwise -> a module-level function/class in a reachable file
        return tag([sid for f, sid in candidates.items() if f in reachable], True)

    if receiver_expr == "self":
        # `self.m()` -> a method defined in the caller's own file
        sid = candidates.get(fi_path)
        return [(sid, True)] if sid else []

    if receiver_expr.startswith("super("):
        return []  # parent-class call; needs inheritance resolution -> skip rather than guess

    if SIMPLE_NAME.fullmatch(receiver_expr):
        target_files = set()
        for typ in var_types.get(receiver_expr, ()):           # `x: SomeType` -> SomeType's file
            target_files.update(defs_by_name.get(typ, {}).keys())
        if receiver_expr in defs_by_name:                      # `ClassName.m()` / `Enum.X`
            target_files.update(defs_by_name[receiver_expr].keys())
        for f in reachable:                                    # `module.m()` for an imported module
            if _module_stem(f) == receiver_expr:
                target_files.add(f)
        if target_files:
            return tag([sid for f, sid in candidates.items() if f in target_files], True)

    # Complex/unresolved receiver (e.g. `a.b().c`, or an untyped local): fall
    # back to the import-scoped name match — the honest residual that would need
    # full type inference to nail down. Marked shaky (not precise).
    return tag([sid for f, sid in candidates.items() if f in reachable], False)


def _import_bindings(tree):
    """(local_name, source_module_stem) for names imported into a file.

    e.g. `from pathlib import Path` -> ("Path", "pathlib");
         `from .adapters import HTTPAdapter` -> ("HTTPAdapter", "adapters").
    """
    out = []
    for node in walk(tree.root_node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    top = child.text.decode().split(".")[0]
                    out.append((top, top))
                elif child.type == "aliased_import":
                    dn = child.child_by_field_name("name")
                    al = child.child_by_field_name("alias")
                    if dn is not None and al is not None:
                        out.append((al.text.decode(), dn.text.decode().split(".")[-1]))
        elif node.type == "import_from_statement":
            mod = node.child_by_field_name("module_name")
            stem = None
            if mod is not None:
                txt = mod.text.decode().lstrip(".")
                if txt:
                    stem = txt.split(".")[-1]
            for n in node.children_by_field_name("name"):
                if n.type == "dotted_name":
                    local = n.text.decode().split(".")[-1]
                    out.append((local, stem or local))
                elif n.type == "aliased_import":
                    dn = n.child_by_field_name("name")
                    al = n.child_by_field_name("alias")
                    if dn is not None and al is not None:
                        out.append((al.text.decode(), stem or dn.text.decode().split(".")[-1]))
    return out


def _external_names(tree, repo_stems):
    """Names a file imports from modules that are NOT part of this repo.

    Such a name (e.g. stdlib `Path`) shadows any same-named repo symbol, so a
    bare call to it must not be attributed to the repo symbol.
    """
    ext = set()
    try:
        for local, stem in _import_bindings(tree):
            if stem not in repo_stems:
                ext.add(local)
    except Exception:
        return set()  # never let import-parsing quirks break the graph
    return ext


def build_symbol_graph(repo_dir, repo_index):
    symbol_graph = defaultdict(lambda: defaultdict(lambda: {
        "count": 0,
        "lines": [],
        "precise": False,
    }))

    # name -> {defining_file: symbol_id}   (keep every definition, don't collapse)
    defs_by_name = defaultdict(dict)
    for fi in repo_index.values():
        for sym in fi.symbols:
            defs_by_name[sym.name][fi.path] = symbol_id(sym)

    all_files = list(repo_index.keys())
    repo_stems = {_module_stem(f) for f in all_files}

    for fi in repo_index.values():
        abs_path = os.path.join(repo_dir, fi.path)
        if not os.path.exists(abs_path):
            continue

        reachable = _reachable_files(fi, all_files, defs_by_name)

        code = open(abs_path, "r", encoding="utf-8").read()
        tree = parse_code(code)
        var_types = _collect_var_types(tree)
        external = _external_names(tree, repo_stems)

        for call in extract_function_calls(tree):
            for sid, precise in _resolve_call_targets(
                call["name"], fi.path, reachable, defs_by_name, var_types, external
            ):
                entry = symbol_graph[fi.path][sid]
                entry["count"] += 1
                entry["lines"].append(call["line"])
                if precise:
                    entry["precise"] = True

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
    changed_ids,
    symbol_graph,
    repo_dir,
    repo_index,
    base_sha,
):
    # changed_ids: set of "file:kind:name" identities of the symbols the PR
    # changed. Matching on identity (not bare name) is what stops a change to
    # one `send` from flagging every caller of any `.send()` in the repo.
    changed_ids = set(changed_ids)
    impacts = []

    for file_path, callees in symbol_graph.items():
        for sid, meta in callees.items():
            if sid not in changed_ids:
                continue

            def_file, kind, name = sid.rsplit(":", 2)

            count = meta["count"]
            lines = meta["lines"]
            precise = meta.get("precise", True)

            score = compute_confidence(file_path, name, count, precise=precise)
            label = confidence_label(score)

            abs_impacted = os.path.join(repo_dir, file_path)

            call_site_line = lines[0]
            call_site_code = extract_call_site_snippet(
                abs_impacted,
                call_site_line,
                CALL_SNIPPET_WINDOW,
            )

            # before/after come from the exact defining file/symbol (via the id).
            after_code = ""
            before_code = ""
            fi = repo_index.get(def_file)
            if fi:
                for sym in fi.symbols:
                    if sym.name == name and sym.kind == kind:
                        abs_def_path = os.path.join(repo_dir, def_file)
                        after_code = extract_code_snippet(abs_def_path, sym.start, sym.end)
                        base_text = git_show_file(repo_dir, base_sha, def_file)
                        before_code = extract_code_snippet_from_text(base_text, sym.start, sym.end)
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
                "precise": precise,
            })

    impacts.sort(key=lambda x: x["score"], reverse=True)
    return impacts

def git_show_file(repo_dir:str, commit_sha:str, file_path:str) -> str:
    # check=False: a symbol's defining file may not exist at the base SHA
    # (e.g. a newly added file). Treat "not in base" as empty before-code
    # rather than crashing the whole analysis.
    result = subprocess.run(
        ["git", "show", f"{commit_sha}:{file_path}"],
        cwd=repo_dir,
        check=False,
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