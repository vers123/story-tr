"""语言链与翻译任务构建（纯逻辑，无 IO）。

从旧 `chain.py` 迁移：内置固定语言链、随机语言池、路线解析与任务列表构建。
"""
import random

from .lang import TARGET_ZH

# 内置固定语言链（各 20 个中间语言；首 en、末 zh 不计入）
CHAINS = {
    "asia": ["ja", "ko", "th", "vi", "id", "ms", "tl", "hi", "bn", "ta",
             "te", "ur", "fa", "ar", "tr", "iw", "my", "km", "lo", "ne"],
    "europe": ["de", "fr", "es", "pt", "it", "nl", "sv", "no", "da", "fi",
               "pl", "cs", "hu", "ro", "bg", "el", "ru", "uk", "cy", "is"],
    "exotic": ["la", "eo", "cy", "ga", "is", "eu", "sq", "mt", "hy", "ka",
               "sw", "tl", "jw", "su", "mi", "haw", "zu", "yo", "ha", "am"],
}
# 随机模式的内置趣味语言池（并集去重排序）
RANDOM_POOL = sorted({c for chain in CHAINS.values() for c in chain})


def resolve_languages(mode: str, chain: str = "asia", languages: str = "",
                      steps: int = 20) -> tuple[list[str], str]:
    """按路线模式解析出语言序列，返回 `(语言列表, 路线标签)`。

    - `fixed`：整条内置链
    - `random`：从 `RANDOM_POOL` 随机抽取 `steps` 个
    - `custom`：逗号分隔的自定义语言序列（去空白去空）
    - `rwkv20`：本地一次成型（单元素伪路线）
    参数错误统一抛 `ValueError`（由 services/cli 转退出码 1）。
    """
    if mode == "rwkv20":
        return ["rwkv20"], "rwkv20"
    if mode == "fixed":
        if chain not in CHAINS:
            raise ValueError(f"未知链：{chain}")
        return list(CHAINS[chain]), chain
    if mode == "random":
        if steps > len(RANDOM_POOL):
            raise ValueError(f"随机语言池只有 {len(RANDOM_POOL)} 个，无法抽取 {steps} 个")
        return random.sample(RANDOM_POOL, steps), "random"
    if mode == "custom":
        langs = [x.strip() for x in languages.split(",") if x.strip()]
        if not langs:
            raise ValueError("--mode custom 需要用 --languages 指定语言序列（逗号分隔）")
        return langs, "custom"
    raise ValueError(f"未知模式：{mode}")


def build_tasks(detected: str, languages: list[str]) -> list[dict[str, str | int]]:
    """按「检出语言 + 语言序列」构建翻译任务列表。

    - `["rwkv20"]`：单任务本地一次成型（英文原文 → 中文）
    - 否则：非英文先 `to_en`，再逐语言 `step`，最后 `to_zh`
    """
    if list(languages) == ["rwkv20"]:
        return [{"kind": "rwkv20", "index": 0, "lang": TARGET_ZH,
                 "src": "en", "tgt": TARGET_ZH}]
    tasks: list[dict[str, str | int]] = []
    if detected != "en":
        tasks.append({"kind": "to_en", "lang": "en", "src": detected, "tgt": "en"})
    cur = "en"
    for i, lang in enumerate(languages):
        tasks.append({"kind": "step", "index": i, "lang": lang, "src": cur, "tgt": lang})
        cur = lang
    tasks.append({"kind": "to_zh", "lang": TARGET_ZH, "src": cur, "tgt": TARGET_ZH})
    return tasks
