from tree_sitter_languages import get_language, get_parser

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
                "start_line": node.start_point[0],
                "end_line": node.end_point[0],
            })

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            classes.append({
                "name": name_node.text.decode(),
                "start_line": node.start_point[0],
                "end_line": node.end_point[0],
            })

    return {
        "functions": functions,
        "classes": classes,
    }

def extract_imports(tree):
    imports = []

    for node in walk(tree.root_node):
        if node.type == "import_statement":
            imports.append(node.text.decode())

        elif node.type == "import_from_statement":
            imports.append(node.text.decode())

    return imports

def changed_files_from_diff(diff: str):
    files = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
    return list(files)

def changed_lines_from_diff(diff: str):
    changed_lines = set()
    current_line = None

    for line in diff.splitlines():
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            plus = line.split("+")[1].split(" ")[0]
            start = int(plus.split(",")[0])
            current_line = start

        elif line.startswith("+") and not line.startswith("+++"):
            if current_line is not None:
                changed_lines.add(current_line)
                current_line += 1

        elif not line.startswith("-"):
            if current_line is not None:
                current_line += 1

    return changed_lines

def find_changed_symbols(symbols, changed_lines):
    changed = []

    for fn in symbols["functions"]:
        if any(fn["start_line"] <= l <= fn["end_line"] for l in changed_lines):
            changed.append(("function", fn["name"]))

    for cls in symbols["classes"]:
        if any(cls["start_line"] <= l <= cls["end_line"] for l in changed_lines):
            changed.append(("class", cls["name"]))

    return changed
