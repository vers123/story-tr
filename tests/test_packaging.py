"""打包 / 依赖声明一致性：`pyproject.toml` 是唯一事实来源。

`requirements.lock` 只作复现记录，必须覆盖 `pyproject.toml` 中的直接依赖，
避免声明漂移；PEP 561 的 `py.typed` 标记需随包一起分发。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _name(spec: str) -> str:
    """requirements 行 → 规范化包名（去版本 / extras / 环境标记）。"""
    return re.split(r"[<>=!~;\[\s]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _pyproject_arrays() -> tuple[str, list[str], list[str]]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    dev = re.search(r"^dev\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    runtime_deps = re.findall(r'"([^"]+)"', runtime.group(1)) if runtime else []
    dev_deps = re.findall(r'"([^"]+)"', dev.group(1)) if dev else []
    return text, runtime_deps, dev_deps


def test_requirements_lock_covers_all_direct_dependencies():
    _, runtime, dev = _pyproject_arrays()
    expected = {_name(x) for x in runtime + dev}
    locked = {
        _name(line.split("#", 1)[0].strip())
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }
    assert expected <= locked


def test_py_typed_ships_with_package():
    """PEP 561 标记文件随包分发：py.typed 存在，且在 package-data 中声明。"""
    assert (ROOT / "src" / "story_tr" / "py.typed").is_file()

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(
        r"^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "pyproject.toml 缺少 [tool.setuptools.package-data] 段"
    entry = re.search(r"^story_tr\s*=\s*\[(.*?)\]", section.group(1), re.MULTILINE | re.DOTALL)
    assert entry, "package-data 未声明 story_tr 的额外文件"
    assert '"py.typed"' in entry.group(1)
