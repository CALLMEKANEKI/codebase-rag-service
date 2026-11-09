import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any


class SourceError(ValueError):
    pass


def count_lines(text: str) -> int:
    if not text:
        return 0
    separators = len(re.findall(r"\r\n|\r|\n", text))
    return separators if text.endswith(("\r", "\n")) else separators + 1


def decode_source(data: bytes, path: str = "<memory>") -> str:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        return data.decode(encoding)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise SourceError(f"{path}: cannot decode Python source: {exc}") from exc


def module_name(file_path: str) -> str:
    module = file_path[:-3].replace("/", ".")
    module = module.removeprefix("src.").removesuffix(".__init__")
    if not module:
        module = "__init__"
    return module


def first_doc_line(node: ast.AST) -> str | None:
    docstring = ast.get_docstring(node, clean=True)
    if docstring is None:
        return None
    for line in docstring.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return None


def symbols_for_source(source: str, repo: str, file_path: str) -> list[dict[str, Any]]:
    line_count = count_lines(source)
    if line_count == 0:
        return []
    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise SourceError(f"{file_path}: cannot parse Python source: {exc}") from exc

    module_qualname = module_name(file_path)
    records: list[dict[str, Any]] = [
        {
            "repo": repo,
            "file_path": file_path,
            "kind": "module",
            "name": module_qualname.rsplit(".", 1)[-1],
            "qualname": module_qualname,
            "parent": None,
            "start_line": 1,
            "end_line": line_count,
            "doc_first_line": first_doc_line(tree),
        }
    ]

    symbol_types = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

    def visit(node: ast.AST, parent_name: str | None, parent_kind: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, symbol_types):
                kind = (
                    "class"
                    if isinstance(child, ast.ClassDef)
                    else "method"
                    if parent_kind == "class"
                    else "function"
                )
                qualname = f"{parent_name}.{child.name}" if parent_name else child.name
                decorators = getattr(child, "decorator_list", [])
                start_line = min(
                    [child.lineno, *(decorator.lineno for decorator in decorators)]
                )
                records.append(
                    {
                        "repo": repo,
                        "file_path": file_path,
                        "kind": kind,
                        "name": child.name,
                        "qualname": qualname,
                        "parent": parent_name,
                        "start_line": start_line,
                        "end_line": child.end_lineno,
                        "doc_first_line": first_doc_line(child),
                    }
                )
                visit(child, qualname, kind)
            else:
                visit(child, parent_name, parent_kind)

    visit(tree, None, None)
    return records


def build_symbols(repo_root: str | Path, repo: str, file_paths: list[str]):
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    empty_files: list[str] = []
    records_by_file: dict[str, list[dict[str, Any]]] = {}
    for file_path in file_paths:
        try:
            source = decode_source(
                (Path(repo_root) / file_path).read_bytes(), file_path
            )
            file_records = symbols_for_source(source, repo, file_path)
        except (OSError, SourceError) as exc:
            errors.append(str(exc))
            continue
        if count_lines(source) == 0:
            empty_files.append(file_path)
        records.extend(file_records)
        records_by_file[file_path] = file_records
    return records, records_by_file, empty_files, errors
