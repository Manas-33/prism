from typing import Dict
from app.static_analysis import parse_code, extract_symbols, extract_imports
from app.models import Symbol, FileIndex
import os 

def index_file(repo_dir: str, file_path: str) -> FileIndex:
    full_path = os.path.join(repo_dir, file_path)
    
    if not full_path.endswith(".py"):
        return None
    
    code = open(full_path).read()
    tree = parse_code(code)
    symbols_raw = extract_symbols(tree)
    imports = extract_imports(tree)
    
    symbols = []
    
    for fn in symbols_raw["functions"]:
        symbols.append(Symbol(
            name=fn["name"],
            kind="function",
            file=file_path,
            start=fn["start_line"],
            end=fn["end_line"]
        ))
        
    for cls in symbols_raw["classes"]:
        symbols.append(Symbol(
            name=cls["name"],
            kind="class",
            file=file_path,
            start=cls["start_line"],
            end=cls["end_line"]
        ))
    
    return FileIndex(
        path=file_path,
        imports=imports,
        symbols=symbols
    )

# Non-library directories: demo/example code reuses common class names
# (Response, Application, ...) and pollutes the symbol graph with collisions.
EXCLUDED_DIRS = {
    "examples", "example", "docs", "doc",
    ".git", "__pycache__", ".venv", "venv", "env", "build", "dist", ".tox",
}

def build_repo_index(repo_dir:str) -> Dict[str, FileIndex]:
    index = {}
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for file in files:
            if file.endswith(".py"):
                rel_path = os.path.relpath(os.path.join(root, file), repo_dir)
                file_index = index_file(repo_dir, rel_path)
                if file_index:
                    index[rel_path] = file_index

    return index