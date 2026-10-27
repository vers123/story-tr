"""评测指标（纯函数，无 IO）。"""
import difflib
import re

_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_NON_WS_RE = re.compile(r"\S")


def zh_ratio(text: str) -> float:
    """汉字数 / 非空白字符数（空返回 0.0）。"""
    text = text or ""
    chars = len(_NON_WS_RE.findall(text))
    if chars == 0:
        return 0.0
    return len(_HAN_RE.findall(text)) / chars


def len_ratio(final: str, source: str) -> float:
    """`len(final) / max(len(source), 1)`。"""
    return len(final) / max(len(source), 1)


def similarity(a: str, b: str) -> float:
    """`difflib.SequenceMatcher(None, a, b).ratio()`。"""
    return difflib.SequenceMatcher(None, a, b).ratio()


def repeat4(text: str) -> float:
    """`1 - 唯一 4-gram 数 / 总 4-gram 数`（长度 <4 记 0.0）。"""
    text = text or ""
    if len(text) < 4:
        return 0.0
    grams = [text[i:i + 4] for i in range(len(text) - 3)]
    total = len(grams)
    if total == 0:
        return 0.0
    return 1.0 - len(set(grams)) / total


def score(metrics: dict[str, float], has_baseline: bool) -> float:
    """按排序评分公式把指标合成 0~1 的得分。

    - 有基线：`0.5*sim + 0.2*min(zh,1) + 0.2*(1-min(abs(len-1),1)) + 0.1*(1-min(rep,1))`
    - 无基线：`0.5*zh + 0.3*(1-min(abs(len-1),1)) + 0.2*(1-min(rep,1))`
    """
    zh = metrics.get("zh", 0.0)
    length_term = 1.0 - min(abs(metrics.get("len", 1.0) - 1.0), 1.0)
    rep_term = 1.0 - min(metrics.get("rep", 0.0), 1.0)
    if has_baseline:
        sim = metrics.get("sim", 0.0)
        return 0.5 * sim + 0.2 * min(zh, 1.0) + 0.2 * length_term + 0.1 * rep_term
    return 0.5 * zh + 0.3 * length_term + 0.2 * rep_term
