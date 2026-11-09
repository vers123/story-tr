"""结构守护：函数长度、分层依赖方向与私有符号跨模块引用。

这些约束用于防止重构后结构重新腐化：
- 生产代码单函数不超过 `MAX_FUNCTION_LINES` 行
- 分层单向依赖：models → core → transport → services → cli
- 生产模块不得跨模块引用 `_` 开头的私有名字
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "story_tr"
MAX_FUNCTION_LINES = 60

LAYERS = ("models", "core", "transport", "services", "cli")


def _modules() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _layer(path: pathlib.Path) -> str | None:
    """`story_tr/<layer>/...` → 层名；根目录模块为 None。"""
    parts = path.relative_to(SRC).parts
    return parts[0] if len(parts) > 1 and parts[0] in LAYERS else None


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    """收集 `from .. import x` / `from story_tr.a import x` 的模块路径。"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level
            for alias in node.names:
                out.append((node.lineno, ("." * level) + module + ("." if module else "") + alias.name))
                out.append((node.lineno, ("." * level) + module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
    return out


def _parsed():
    for path in _modules():
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_functions_stay_small():
    too_long = []
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length > MAX_FUNCTION_LINES:
                    rel = path.relative_to(SRC.parents[1])
                    too_long.append(f"{rel}:{node.lineno} {node.name} ({length} 行)")
    assert not too_long, "函数过长，需要拆分：\n" + "\n".join(too_long)


def _target_layer(path: pathlib.Path, module: str) -> str | None:
    """把 import 路径解析为目标层（仅关注 story_tr 内部的绝对路径）。"""
    if not module.startswith("story_tr"):
        return None
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else None


def test_layering_is_one_directional():
    violations = []
    for path, tree in _parsed():
        layer = _layer(path)
        if layer is None:
            continue
        allowed = set(LAYERS[: LAYERS.index(layer) + 1])
        for lineno, module in _imports(tree):
            target = _target_layer(path, module)
            if target is not None and target not in allowed:
                rel = path.relative_to(SRC.parents[1])
                violations.append(f"{rel}:{lineno} {layer} → {target}（{module}）")
    assert not violations, "存在反向依赖：\n" + "\n".join(sorted(set(violations)))


def _private_imports(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    out.append((node.lineno, alias.name))
    return out


def test_no_cross_module_private_imports():
    violations = []
    for path, tree in _parsed():
        for lineno, name in _private_imports(tree):
            rel = path.relative_to(SRC.parents[1])
            violations.append(f"{rel}:{lineno} import {name}")
    assert not violations, "生产代码不应跨模块引用私有名字：\n" + "\n".join(violations)
