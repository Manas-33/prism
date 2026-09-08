from tree_sitter_languages import get_language, get_parser
import os
import ast
import textwrap

PY_LANGUAGE = get_language("python")
parser = get_parser("python")
def parse_code(code: str):
    tree = parser.parse(code.encode("utf-8"))
    return tree

def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)

def extract_symbols(tree):
    functions = []
    classes = []

    for node in walk(tree.root_node):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            functions.append({
                "name": name_node.text.decode(),
                "start_line": node.start_point[0]+1,
                "end_line": node.end_point[0]+1,
            })

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            classes.append({
                "name": name_node.text.decode(),
                "start_line": node.start_point[0]+1,
                "end_line": node.end_point[0]+1,
            })

    return {
        "functions": functions,
        "classes": classes,
    }

# def extract_imports(tree):
#     imports = []

#     for node in walk(tree.root_node):
#         if node.type == "import_statement":
#             imports.append(node.text.decode())

#         elif node.type == "import_from_statement":
#             imports.append(node.text.decode())

#     return imports

def extract_imports(tree):
    imports = []

    for node in walk(tree.root_node):
        # 1. Handle "import foo, bar" and "import foo as f"
        if node.type == "import_statement":
            skip_next = False
            for child in node.children:
                # If we see "as", we must skip the NEXT child (the alias name)
                if child.text.decode() == "as":
                    skip_next = True
                    continue
                
                if skip_next:
                    skip_next = False
                    continue

                if child.type == "dotted_name":
                    imports.append({
                        "type": "import",
                        "module": child.text.decode(),
                        "level": 0,
                    })

        # 2. Handle "from ..." statements
        elif node.type == "import_from_statement":
            level = 0
            module_from_clause = None
            imported_names = [] 
            seen_import_keyword = False

            for child in node.children:
                if child.type == ".":
                    if not seen_import_keyword:
                        level += 1
                
                elif child.type == "dotted_name":
                    if not seen_import_keyword:
                        # "from foo.bar ..."
                        module_from_clause = child.text.decode()
                    else:
                        # "... import x, y"
                        imported_names.append(child.text.decode())
                
                elif child.text.decode() == "import":
                    seen_import_keyword = True

            # Resolution Logic
            if module_from_clause:
                # Case: "from foo import x" -> resolve "foo"
                imports.append({
                    "type": "from",
                    "module": module_from_clause,
                    "level": level,
                })
            else:
                # Case: "from .. import deep" -> resolve "deep"
                for name in imported_names:
                    imports.append({
                        "type": "from",
                        "module": name,
                        "level": level,
                    })

    return imports

def resolve_absolute_import(module, module_root):
    if not module:
        return None

    parts = module.split(".")

    candidates = [
        os.path.join(module_root, *parts) + ".py",
        os.path.join(module_root, *parts, "__init__.py"),
        os.path.join(module_root, parts[0], "__init__.py"),
    ]

    for path in candidates:
        if os.path.exists(path):
            return os.path.relpath(path, module_root)

    return None

def resolve_relative_import(current_file, module, level, module_root):
    # current_file: demo_app/services.py
    current_parts = current_file.replace(".py", "").split(os.sep)

    # Walk up `level` times
    base = current_parts[:-level]

    if module:
        base += module.split(".")

    candidate = os.path.join(module_root, *base)

    if os.path.exists(candidate + ".py"):
        return os.path.relpath(candidate + ".py", module_root)

    if os.path.exists(os.path.join(candidate, "__init__.py")):
        return os.path.relpath(
            os.path.join(candidate, "__init__.py"),
            module_root,
        )

    return None

def resolve_import(import_stmt, current_file, module_root):
    if import_stmt["type"] == "import":
        return resolve_absolute_import(
            import_stmt["module"],
            module_root,
        )

    if import_stmt["type"] == "from":
        if import_stmt["level"] > 0:
            return resolve_relative_import(
                current_file=current_file,
                module=import_stmt["module"],
                level=import_stmt["level"],
                module_root=module_root,
            )
        else:
            return resolve_absolute_import(
                import_stmt["module"],
                module_root,
            )

    return None
def changed_files_from_diff(diff: str):
    files = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
    return list(files)

def changed_lines_from_diff(diff: str):
    changed = set()
    current = None

    for line in diff.splitlines():
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            hunk = line.split("+")[1].split(" ")[0]
            current = int(hunk.split(",")[0])

        elif line.startswith("+") and not line.startswith("+++"):
            if current is not None:
                changed.add(current)
                current += 1

        elif line.startswith(" ") and current is not None:
            current += 1

    return changed

def changed_lines_by_file(diff: str) -> dict:
    """Map each changed file -> set of its changed (new-file) line numbers.

    Unlike changed_lines_from_diff, this keeps line numbers scoped to the file
    they belong to. Conflating them across files causes false "changed symbols"
    (a hunk in file A marking a same-line-range symbol in file B).
    """
    result: dict = {}
    current_file = None
    lineno = None

    for line in diff.splitlines():
        if line.startswith("diff --git"):
            current_file, lineno = None, None
        elif line.startswith("+++ b/"):
            current_file = line[6:]
            result.setdefault(current_file, set())
            lineno = None
        elif line.startswith("+++ "):        # e.g. "+++ /dev/null" (deleted file)
            current_file, lineno = None, None
        elif line.startswith("@@"):
            if current_file is None:
                continue
            try:
                # @@ -a,b +c,d @@  -> new-file hunk starts at c
                lineno = int(line.split("+", 1)[1].split()[0].split(",")[0])
            except (IndexError, ValueError):
                lineno = None
        elif current_file is not None and lineno is not None:
            if line.startswith("+"):
                result[current_file].add(lineno)
                lineno += 1
            elif line.startswith("-") or line.startswith("\\"):
                pass                          # removed line / "no newline" marker
            else:
                lineno += 1                   # context line

    return result

def find_changed_symbols(symbols, changed_lines):
    changed = []

    for fn in symbols["functions"]:
        if any(fn["start_line"] <= l <= fn["end_line"] for l in changed_lines):
            changed.append(("function", fn["name"]))

    for cls in symbols["classes"]:
        if any(cls["start_line"] <= l <= cls["end_line"] for l in changed_lines):
            changed.append(("class", cls["name"]))

    return changed

def _docstring_lines(tree):
    """Line numbers occupied by docstrings (bare string expression statements)."""
    lines = set()
    for node in walk(tree.root_node):
        if node.type == "expression_statement":
            for child in node.children:
                if child.type == "string":
                    for ln in range(child.start_point[0] + 1, child.end_point[0] + 2):
                        lines.add(ln)
    return lines

def filter_code_lines(tree, source: str, changed_lines):
    """Keep only changed lines that carry real code.

    Drops blank lines, full-line comments, and docstring lines so a change that
    only touches documentation/comments/formatting doesn't mark a symbol as
    changed (and spuriously flag all its callers).
    """
    src_lines = source.splitlines()
    docstrings = _docstring_lines(tree)
    code = set()
    for ln in changed_lines:
        if ln < 1 or ln > len(src_lines):
            continue
        stripped = src_lines[ln - 1].strip()
        if stripped == "" or stripped.startswith("#") or ln in docstrings:
            continue
        code.add(ln)
    return code


def _symbol_source(source: str, symbols, kind: str, name: str):
    key = "functions" if kind == "function" else "classes"
    lines = source.splitlines()
    for s in symbols[key]:
        if s["name"] == name:
            return "\n".join(lines[s["start_line"] - 1:s["end_line"]])
    return None


class _StripNormalizer(ast.NodeTransformer):
    """Erase docstrings. Comments are already absent from the AST (so a change to
    a trailing `# type: ignore` is invisible here). Type annotations are kept on
    purpose: dropping them would hide dataclass field additions, which DO change
    a constructor's signature."""

    def _strip_doc(self, node):
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:]

    def visit_FunctionDef(self, node):
        self._strip_doc(node)
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._strip_doc(node)
        self.generic_visit(node)
        return node

    def visit_Module(self, node):
        self._strip_doc(node)
        self.generic_visit(node)
        return node


def _semantic_signature(src: str):
    try:
        tree = ast.parse(textwrap.dedent(src))
    except (SyntaxError, ValueError, IndentationError, TypeError):
        return None
    try:
        tree = _StripNormalizer().visit(tree)
        ast.fix_missing_locations(tree)
        return ast.dump(tree, annotate_fields=False)
    except Exception:
        return None


def is_substantive_change(base_src, base_symbols, head_src, head_symbols, kind, name) -> bool:
    """False when a symbol's base and head bodies are identical modulo comments,
    docstrings, and type annotations — a cosmetic change that can't break callers.
    Conservative: returns True (substantive) whenever it can't be sure."""
    head_frag = _symbol_source(head_src, head_symbols, kind, name)
    base_frag = _symbol_source(base_src, base_symbols, kind, name) if base_src else None
    if base_frag is None:
        return True  # new or moved symbol
    head_sig = _semantic_signature(head_frag)
    base_sig = _semantic_signature(base_frag)
    if head_sig is None or base_sig is None:
        return True  # couldn't normalize -> don't over-filter
    return head_sig != base_sig
