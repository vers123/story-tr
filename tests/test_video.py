# -*- coding: utf-8 -*-
"""视频合成：故事顺序、完整性校验、文本排版与端到端编码。"""
import json
import os
import shutil
import subprocess

import pytest

from story_tr import stories
from story_tr import video as vid

pytest.importorskip("PIL")


# --------------------------------------------------------------------------- #
# 顺序与选择
# --------------------------------------------------------------------------- #
def test_story_sort_key_puts_canonical_order_first():
    names = ["vision", "amber_journal", "character_story_2", "character_details",
             "character_story_10"]
    ordered = sorted(names, key=vid.story_sort_key)
    assert ordered[:4] == ["character_details", "character_story_2", "vision", "amber_journal"]
    assert ordered[-1] == "character_story_10"  # 未知故事排在最后


def _make_profile(root, char_id, story, paragraphs):
    d = os.path.join(str(root), char_id, "profile", "story", story)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "zh.md"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(paragraphs) + "\n")
    with open(os.path.join(d, story + ".png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # 只校验存在性
    return d


def _make_result(root, char_id, story, rows):
    d = os.path.join(str(root), char_id, "result", story)
    os.makedirs(d, exist_ok=True)
    for i, (source, final) in enumerate(rows, 1):
        with open(os.path.join(d, "seg_%02d.json" % i), "w", encoding="utf-8") as f:
            json.dump({"segment_index": i, "source": source, "final": final},
                      f, ensure_ascii=False)
    return d


def test_select_stories_filters_and_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    for name in ("vision", "amber_journal", "character_details"):
        _make_profile(tmp_path, "1", name, ["正文"])
    assert vid.select_stories("1") == ["character_details", "vision", "amber_journal"]
    assert vid.select_stories("1", ["amber_journal"]) == ["amber_journal"]


# --------------------------------------------------------------------------- #
# 完整性
# --------------------------------------------------------------------------- #
def test_check_story_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    _make_profile(tmp_path, "1", "vision", ["第一段", "第二段"])
    _make_result(tmp_path, "1", "vision", [("第一段", "译文一"), ("第二段", "译文二")])
    chk = vid.check_story("1", "vision")
    assert chk["ok"] is True and chk["problems"] == []
    assert [x["index"] for x in chk["items"]] == [1, 2]
    assert chk["items"][1]["final"] == "译文二"


def test_check_story_missing_segment_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    _make_profile(tmp_path, "1", "vision", ["第一段", "第二段"])
    _make_result(tmp_path, "1", "vision", [("第一段", "译文一")])
    chk = vid.check_story("1", "vision")
    assert chk["ok"] is False
    assert any("seg_02.json" in p for p in chk["problems"])


def test_check_story_detects_stale_source(tmp_path, monkeypatch):
    """翻译跑在旧文本上时要报「不一致」，避免字幕与截图对不上。"""
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    _make_profile(tmp_path, "1", "vision", ["新正文"])
    _make_result(tmp_path, "1", "vision", [("旧正文", "旧译文")])
    chk = vid.check_story("1", "vision")
    assert chk["ok"] is False
    assert any("不一致" in p for p in chk["problems"])


def test_check_story_allow_partial_uses_available(tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "STORY_ROOT", str(tmp_path))
    _make_profile(tmp_path, "1", "vision", ["第一段", "第二段"])
    _make_result(tmp_path, "1", "vision", [("第一段", "译文一")])
    chk = vid.check_story("1", "vision", allow_partial=True)
    assert chk["ok"] is True
    assert [x["index"] for x in chk["items"]] == [1]
    assert chk["problems"]  # 仍然如实报告缺段


# --------------------------------------------------------------------------- #
# 排版
# --------------------------------------------------------------------------- #
def test_wrap_text_respects_width_and_keeps_words():
    Image, ImageDraw, ImageFont = vid._pil()
    try:
        bold, regular = vid.find_fonts()
    except vid.VideoError:
        pytest.skip("没有可用中文字体")
    font = ImageFont.truetype(regular, 40, index=0)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = vid.wrap_text(draw, "安柏是西风骑士团的侦察骑士，Google Translate 20 次。", font, 300)
    assert len(lines) >= 2
    assert all(draw.textlength(x, font=font) <= 300 for x in lines)
    assert any("Google" in x for x in lines)  # 英文单词不被拆断


def test_wrap_text_avoids_leading_punctuation():
    """避头尾：收尾标点不应出现在行首。"""
    Image, ImageDraw, ImageFont = vid._pil()
    try:
        _, regular = vid.find_fonts()
    except vid.VideoError:
        pytest.skip("没有可用中文字体")
    font = ImageFont.truetype(regular, 40, index=0)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text = "「我抓到魔鸟了！」它叫拉米什么来着，这三天都在追它，直到今天才有空。"
    for width in (120, 160, 200, 240, 300, 360):
        lines = vid.wrap_text(draw, text, font, width)
        for line in lines:
            assert not line or line[0] not in vid._NO_START, (width, lines)


def test_layout_text_shrinks_then_truncates():
    Image, ImageDraw, ImageFont = vid._pil()
    try:
        bold, regular = vid.find_fonts()
    except vid.VideoError:
        pytest.skip("没有可用中文字体")

    def make_font(size, is_bold):
        return ImageFont.truetype(bold if is_bold else regular, size, index=0)

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    short = vid.layout_text(draw, "短", "短", make_font)
    assert short["size"] >= vid.FONT_MIN

    huge = "字" * 4000
    longest = vid.layout_text(draw, huge, huge, make_font, max_height=200)
    assert longest["size"] == vid.FONT_MIN
    assert longest["height"] <= 200
    assert longest["src_lines"][-1].endswith("…")


# --------------------------------------------------------------------------- #
# 端到端（需要 ffmpeg + 字体）
# --------------------------------------------------------------------------- #
def test_render_story_and_total(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("没有 ffmpeg")
    try:
        vid.find_fonts()
    except vid.VideoError:
        pytest.skip("没有可用中文字体")

    root = tmp_path / "home"
    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    _make_profile(root, "1", "vision", ["第一段", "第二段"])
    _make_result(root, "1", "vision", [("第一段", "译文一"), ("第二段", "译文二")])

    # 用 Pillow 生成一张纯色背景与 logo
    Image, _, _ = vid._pil()
    bg = root / "1" / "profile" / "story" / "vision" / "vision.png"
    Image.new("RGB", (640, 360), (30, 40, 60)).save(bg)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (47, 111, 237, 255)).save(logo)

    out = tmp_path / "vision.mp4"
    vid.render_story("1", "vision", vid.check_story("1", "vision")["items"], str(bg),
                     str(out), duration=0.5, fps=10, logo=str(logo), audio=True)
    assert out.exists() and out.stat().st_size > 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True)
    assert abs(float(probe.stdout.strip()) - 1.0) < 0.2  # 2 段 × 0.5s

    total = tmp_path / "1.mp4"
    vid.concat_videos([str(out), str(out)], str(total))
    assert total.exists() and total.stat().st_size > 0
