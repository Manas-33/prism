from tree_sitter_languages import get_language, get_parser
import os

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

def find_changed_symbols(symbols, changed_lines):
    changed = []

    for fn in symbols["functions"]:
        if any(fn["start_line"] <= l <= fn["end_line"] for l in changed_lines):
            changed.append(("function", fn["name"]))

    for cls in symbols["classes"]:
        if any(cls["start_line"] <= l <= cls["end_line"] for l in changed_lines):
            changed.append(("class", cls["name"]))

    return changed
