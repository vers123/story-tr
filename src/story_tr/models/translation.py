"""翻译结果模型：步骤、本地 RWKV 元数据与最终结果。"""
from pydantic import BaseModel


class RwkvMeta(BaseModel):
    """本地 RWKV 单次生成的元数据。"""

    url: str
    model: str
    template_sha1: str
    chunks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class TranslationStep(BaseModel):
    """翻译链中的一步。"""

    index: int
    kind: str
    lang: str
    provider: str
    text: str
    rwkv: RwkvMeta | None = None


class TranslationResult(BaseModel):
    """一份文本的翻译结果（`result/*.json`）。"""

    source: str
    source_lang: str
    target_lang: str
    mode: str
    providers: list[str]
    languages: list[str]
    steps: list[TranslationStep]
    final: str
    chain: str | None = None
    passthrough: bool = False
    rwkv: RwkvMeta | None = None
    story: str | None = None
    segment_index: int | None = None
    source_file: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
