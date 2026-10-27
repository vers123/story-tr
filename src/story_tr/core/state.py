"""内存态状态机与签名（纯函数，无 IO）。

签名用于续跑时校验「本次参数与上次是否一致」；不落盘，只做 dict 比较与差异描述。
"""
from typing import Any

_SIG_LABELS = (
    ("mode", "模式"),
    ("chain", "链"),
    ("steps", "随机步数"),
    ("languages", "自定义语言"),
    ("providers", "后端"),
    ("rwkv", "本地 RWKV"),
)


def build_signature(
    *,
    mode: str,
    chain: str,
    languages: str,
    steps: int,
    providers: list[str],
    rwkv_signature: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造稳定的批量签名 dict；相同参数得到相同 dict，不同参数不同 dict。

    仅保留与当前模式相关的字段（fixed→chain、random→steps、custom→languages），
    `rwkv_signature` 非空时加入 `rwkv` 子对象（用于本地后端参数防混跑）。
    """
    sig: dict[str, Any] = {
        "mode": mode,
        "chain": chain if mode == "fixed" else None,
        "steps": steps if mode == "random" else None,
        "languages": languages if mode == "custom" else None,
        "providers": list(providers),
    }
    if rwkv_signature:
        sig["rwkv"] = dict(rwkv_signature)
    return sig


def signatures_match(prev: dict[str, Any] | None, cur: dict[str, Any]) -> bool:
    """上次签名为空（首次运行）返回 True，否则要求与本次完全一致。"""
    if not prev:
        return True
    return prev == cur


def _human_sig(value: Any) -> str:
    """把签名里的单个值渲染成人看的短字符串。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        if len(value) > 6:
            return f"{','.join(str(x) for x in value[:6])}…（共 {len(value)} 项）"
        return ",".join(str(x) for x in value)
    return str(value)


def describe_diff(prev: dict[str, Any] | None, cur: dict[str, Any]) -> str:
    """把两次签名的差异写成「上次 x → 本次 y」的多行中文；无差异返回固定文案。"""
    prev = prev or {}
    lines: list[str] = []
    for key, label in _SIG_LABELS:
        before = prev.get(key, "<无>")
        after = cur.get(key, "<无>")
        if before != after:
            lines.append(f"{label}：上次 {_human_sig(before)} → 本次 {_human_sig(after)}")
    return "\n".join(lines) if lines else "（无可比较的差异）"
