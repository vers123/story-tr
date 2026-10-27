"""分段 / 分块 / 输出清洗（纯字符串操作，无 IO）。

从旧 `chain._split_text`、`rwkv.clean_output` 与 `stories.read_title` /
`read_paragraphs` 的纯逻辑部分迁移。
"""
import json
import re

# 行首角色前缀（中英文冒号）
_ROLE_RE = re.compile(r"(?m)^(?:User|Bot|Assistant|System)\s*[:：]\s*")
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("'", "'"), ("「", "」"))


def split_chunks(text: str, limit: int) -> list[str]:
    """把超长文本切成 ≤ limit 的片段（优先按句末标点，必要时硬切）。"""
    text = text or ""
    if not text:
        return []
    if limit <= 0 or len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for piece in re.split(r"(?<=[。！？；!?;.\n])", text):
        if not piece:
            continue
        if cur and len(cur) + len(piece) > limit:
            chunks.append(cur)
            cur = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        cur += piece
    if cur:
        chunks.append(cur)
    return chunks


def _strip_quotes(text: str) -> str:
    """去掉成对包裹的引号（可多层包裹）。"""
    while len(text) >= 2:
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left):len(text) - len(right)].strip()
                break
        else:
            return text
    return text


def clean_output(raw: str, prompt: str, source: str) -> str:
    """清洗模型输出：去 prompt 回声、行首角色前缀、包裹引号与行尾空白（保留段落空行）。"""
    out = (raw or "").strip()
    for prefix in (prompt or "", (prompt or "").rstrip()):
        if prefix and out.startswith(prefix):
            out = out[len(prefix):]
            break
    out = out.strip()
    while True:
        stripped = _ROLE_RE.sub("", out)
        if stripped == out:
            break
        out = stripped
    out = _strip_quotes(out.strip())
    out = "\n".join(line.rstrip() for line in out.splitlines())
    return out.strip()


def parse_frontmatter(raw: str) -> tuple[dict[str, str] | None, str]:
    """解析 YAML frontmatter，返回 `(frontmatter 字典或 None, 正文)`。

    frontmatter 内 `key: value` 行做最简解析：value 是 JSON 字符串则 `json.loads`，
    否则去引号；无 `---` 包裹或格式不完整时返回 `(None, 原文)`。
    """
    raw = raw or ""
    if not raw.lstrip().startswith("---"):
        return None, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None, raw
    frontmatter: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if not value:
            frontmatter[key] = ""
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value.strip().strip('"').strip("'")
        frontmatter[key] = parsed if isinstance(parsed, str) else str(parsed)
    return frontmatter, parts[2]


def split_paragraphs(body: str) -> list[str]:
    """按空行切段、去空白、去空段（等价旧 `stories.read_paragraphs` 的正文切分）。"""
    return [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
