"""语言判定与后端语言代码映射（纯逻辑，无 IO）。

从旧 `chain.py` 迁移：先按 Unicode 脚本判定（假名 / 谚文 / 汉字），
无文字特征回退 `en`，其余交 langdetect（固定 seed、线程锁内预热一次）。
langdetect 是本模块唯一允许的外部依赖（语言判定所需）。
"""
import re
import threading

from ..errors import UnsupportedLanguage

TARGET_ZH = "zh-CN"

# 「文字」= Unicode 字母（含汉字）。langdetect 的特征只来自字母，
# 纯标点（如 `……`）/ 纯数字 / 空白的文本没有任何特征可判。
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# 文字脚本：来源文本以中文为主，先按脚本判定，避免 langdetect 把短中文误判成韩/日。
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

LANG_NAMES = {
    "en": "英语", "zh-CN": "简体中文", "zh-TW": "繁体中文", "ja": "日语", "ko": "韩语",
    "th": "泰语", "vi": "越南语", "id": "印尼语", "ms": "马来语", "tl": "菲律宾语",
    "hi": "印地语", "bn": "孟加拉语", "ta": "泰米尔语", "te": "泰卢固语", "ur": "乌尔都语",
    "fa": "波斯语", "ar": "阿拉伯语", "tr": "土耳其语", "iw": "希伯来语", "my": "缅甸语",
    "km": "高棉语", "lo": "老挝语", "ne": "尼泊尔语", "de": "德语", "fr": "法语",
    "es": "西班牙语", "pt": "葡萄牙语", "it": "意大利语", "nl": "荷兰语", "sv": "瑞典语",
    "no": "挪威语", "da": "丹麦语", "fi": "芬兰语", "pl": "波兰语", "cs": "捷克语",
    "hu": "匈牙利语", "ro": "罗马尼亚语", "bg": "保加利亚语", "el": "希腊语", "ru": "俄语",
    "uk": "乌克兰语", "cy": "威尔士语", "is": "冰岛语", "la": "拉丁语", "eo": "世界语",
    "ga": "爱尔兰语", "eu": "巴斯克语", "sq": "阿尔巴尼亚语", "mt": "马耳他语",
    "hy": "亚美尼亚语", "ka": "格鲁吉亚语", "sw": "斯瓦希里语", "jw": "爪哇语",
    "su": "巽他语", "mi": "毛利语", "haw": "夏威夷语", "zu": "祖鲁语", "yo": "约鲁巴语",
    "ha": "豪萨语", "am": "阿姆哈拉语",
}

# 各后端语言代码差异
BING_OVERRIDES = {"iw": "he", "jw": "jv", "tl": "fil", "zh-CN": "zh-Hans",
                  "zh-TW": "zh-Hant", "no": "nb"}
BAIDU_NAMES = {
    "en": "english", "zh-CN": "chinese (simplified)", "zh-TW": "chinese (traditional)",
    "ja": "japanese", "ko": "korean", "ar": "arabic", "bg": "bulgarian", "cs": "czech",
    "da": "danish", "nl": "dutch", "et": "estonian", "fi": "finnish", "fr": "french",
    "de": "german", "el": "greek", "hu": "hungarian", "it": "italian", "pl": "polish",
    "pt": "portuguese", "ro": "romanian", "ru": "russian", "sl": "slovenian",
    "es": "spanish", "sv": "swedish", "th": "thai", "vi": "vietnamese",
}
# langdetect 的检出码 -> google 码
DETECT_TO_GOOGLE = {"zh-cn": "zh-CN", "zh-tw": "zh-TW", "he": "iw", "jv": "jw",
                    "fil": "tl", "nb": "no"}


def normalize_detected(code: str) -> str:
    """把 langdetect 的检出码归一化为内部 google 码。"""
    c = (code or "").lower()
    return DETECT_TO_GOOGLE.get(c, c)


_detect_lock = threading.Lock()
_detect_ready = False


def has_features(text: str) -> bool:
    """文本是否含「文字」（字母 / 汉字等）—— langdetect 判语言所需的最小特征。"""
    return bool(_LETTER_RE.search(text or ""))


def has_han(text: str) -> bool:
    """文本是否含汉字（用于 `translate` 的源语言提示）。"""
    return bool(_HAN_RE.search(text or ""))


def detect_language(text: str) -> str:
    """判定源语言（固定 langdetect seed，保证结果可复现）。

    langdetect 的 profile 加载非线程安全，首次调用在锁内预热一次，之后即可并发使用。
    无语言特征的文本（纯标点 / 数字 / 空白）直接返回 `en`。
    先按脚本判定：假名 → 日语、谚文 → 韩语、汉字 → 简体中文；其余交 langdetect。
    """
    global _detect_ready
    text = text or ""
    if not has_features(text):
        return "en"
    if _KANA_RE.search(text):
        return "ja"
    if _HANGUL_RE.search(text):
        return "ko"
    if _HAN_RE.search(text):
        return "zh-CN"

    from langdetect import DetectorFactory, LangDetectException, detect  # type: ignore[import-untyped]

    if not _detect_ready:
        with _detect_lock:
            if not _detect_ready:
                DetectorFactory.seed = 0
                detect("warmup")  # 触发 profile 加载完成
                _detect_ready = True
    try:
        return normalize_detected(detect(text))
    except LangDetectException:  # 极短文本等偶发判定失败，同样退回 en
        return "en"


def to_provider_lang(provider: str, code: str) -> str:
    """内部语言码 → 指定后端的语言码（不支持的组合抛 `UnsupportedLanguage`）。"""
    if provider == "google":
        return code
    if provider == "bing":
        return BING_OVERRIDES.get(code, code)
    if provider == "baidu":
        if code not in BAIDU_NAMES:
            raise UnsupportedLanguage(f"baidu 不支持语言：{code}")
        return BAIDU_NAMES[code]
    raise UnsupportedLanguage(f"未知后端：{provider}")


def lang_label(code: str) -> str:
    """语言码 → 人类可读标签（含原始代码）。"""
    return f"{LANG_NAMES.get(code, '?')}({code})"
