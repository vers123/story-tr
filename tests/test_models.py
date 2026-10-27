"""models 数据模型的构造 / 序列化 / 校验单测。"""
import pytest
from pydantic import ValidationError

from story_tr.models import (
    BatchState,
    CharacterRef,
    CharacterText,
    Costume,
    TranslationResult,
    TranslationStep,
)


def _ref() -> CharacterRef:
    return CharacterRef(
        id="10000021",
        name_en="Amber",
        name_zh="安柏",
        path="data/story/character/10000021",
        stories=["amber_journal", "vision"],
    )


def test_character_ref_roundtrip():
    ref = _ref()
    assert ref.stories == ["amber_journal", "vision"]
    assert ref.model_dump()["id"] == "10000021"


def test_character_ref_default_stories():
    ref = CharacterRef(id="1", name_en="a", name_zh="b", path="c")
    assert ref.stories == []


def test_character_text():
    text = CharacterText(
        title_en="t",
        title_zh="标",
        detail_en="d",
        detail_zh="详",
        constellation_en="c",
        constellation_zh="座",
        native_en="n",
        native_zh="本",
        cv={"en": "a"},
        costume=[Costume(name_en="a", name_zh="b", description_en="c", description_zh="d")],
    )
    dumped = text.model_dump()
    assert dumped["cv"] == {"en": "a"}
    assert dumped["name_card"] is None
    assert dumped["special_food"] is None
    assert dumped["costume"][0]["name_zh"] == "b"


def test_translation_result_roundtrip():
    result = TranslationResult(
        source="a",
        source_lang="zh",
        target_lang="zh",
        mode="rwkv20",
        providers=["rwkv"],
        languages=["rwkv20"],
        steps=[],
        final="b",
    )
    assert result.final == "b"
    assert result.passthrough is False
    assert result.model_dump()["providers"] == ["rwkv"]


def test_translation_result_requires_final():
    with pytest.raises(ValidationError):
        TranslationResult(
            source="a",
            source_lang="zh",
            target_lang="zh",
            mode="rwkv20",
            providers=["rwkv"],
            languages=["rwkv20"],
            steps=[],
        )


def test_translation_result_with_step():
    step = TranslationStep(index=0, kind="rwkv20", lang="zh-CN", provider="rwkv", text="x")
    result = TranslationResult(
        source="a",
        source_lang="zh",
        target_lang="zh",
        mode="rwkv20",
        providers=["rwkv"],
        languages=["rwkv20"],
        steps=[step],
        final="x",
    )
    assert result.steps[0].kind == "rwkv20"


def test_batch_state():
    state = BatchState(signature={"mode": "fixed"}, completed=["a"], started_at="2026-01-01T00:00:00")
    assert state.model_dump()["completed"] == ["a"]
    assert state.updated_at is None


def test_batch_state_requires_started_at():
    with pytest.raises(ValidationError):
        BatchState(signature={})
