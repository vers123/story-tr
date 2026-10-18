# -*- coding: utf-8 -*-
"""把翻译结果渲染成「谷歌翻译 N 次」风格的视频（B 站 1080P 规格）。

版式（1920×1080）：
- 背景：`profile/story/<story>/<story>.png`，整体压暗 25%
- 左下：`assets/translate-logo.webp`（120×120）
- 右下：两个文本块（上＝原文，下＝译文），自动换行 + 自动缩放

输出（每段一屏，段间硬切）：
- `result/video/<story>.mp4` —— 每个故事一条
- `result/video/<char_id>.mp4` —— 按剧情顺序把各故事无损拼接

**只读**翻译结果：不会读写 `.translate_stories_state.json`，也不会改动任何
`seg_*.json` / mp3，因此不会影响仍在进行的翻译进度。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from . import paths, report, stories

OUT_W, OUT_H = 1920, 1080
FPS_DEFAULT = 30
DURATION_DEFAULT = 3.0
VIDEO_BITRATE = "10M"
AUDIO_BITRATE = "320k"

MARGIN = 48
ICON = 120
ICON_X = 45
ICON_BOTTOM = 54
PANEL_LEFT = ICON_X + ICON + 42
PANEL_RIGHT = OUT_W - MARGIN
PANEL_BOTTOM = ICON_BOTTOM
PANEL_PAD_X = 28
PANEL_PAD_Y = 22
TEXT_MAX_W = PANEL_RIGHT - PANEL_LEFT - 2 * PANEL_PAD_X
TEXT_MAX_H = int(OUT_H * 0.42) - 2 * PANEL_PAD_Y
FONT_MAX = 54
FONT_MIN = 24

SOURCE_COLOR = (255, 255, 255)
FINAL_COLOR = (255, 214, 107)
PANEL_FILL = (0, 0, 0, 107)          # 约 42% 黑
DIM_ALPHA = 64                       # 约 25% 黑

# 剧情顺序：角色详细 → 角色故事 1~5 → 神之眼 → 其余（角色专属故事）按名排序
BASE_ORDER = ["character_details"] + ["character_story_%d" % i for i in range(1, 6)] + ["vision"]

# (粗体, 常规)；按顺序找第一个存在的
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
     "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
]


class VideoError(Exception):
    """视频生成前置条件不满足（缺依赖 / 字体 / ffmpeg 等）。"""


# --------------------------------------------------------------------------- #
# 路径与完整性
# --------------------------------------------------------------------------- #
def profile_story_dir(char_id: str) -> str:
    return os.path.join(stories.STORY_ROOT, char_id, "profile", "story")


def video_dir(char_id: str) -> str:
    return os.path.join(stories.story_dir(char_id), "video")


def story_sort_key(name: str):
    """剧情顺序优先；未知故事按名称排在最后。"""
    if name in BASE_ORDER:
        return (0, BASE_ORDER.index(name), name)
    return (1, 0, name)


def select_stories(char_id: str, names=None):
    """该角色可处理的故事（按剧情顺序）。names 为空＝全部。"""
    base = profile_story_dir(char_id)
    if not os.path.isdir(base):
        return []
    found = [n for n in os.listdir(base)
             if os.path.isdir(os.path.join(base, n))
             and os.path.isfile(os.path.join(base, n, "zh.md"))]
    if names:
        wanted = set(names)
        found = [n for n in found if n in wanted]
    return sorted(found, key=story_sort_key)


def check_story(char_id: str, story: str, allow_partial: bool = False) -> dict:
    """检查一个故事的翻译结果是否可用于出片。

    返回 `{ok, items, problems, image, paragraphs}`；`items` 为
    `[{index, source, final}]`（按段号升序）。
    """
    pdir = os.path.join(profile_story_dir(char_id), story)
    md = os.path.join(pdir, "zh.md")
    image = os.path.join(pdir, story + ".png")
    rdir = os.path.join(stories.story_dir(char_id), story)

    problems = []
    items = []
    if not os.path.isfile(md):
        return {"ok": False, "items": [], "problems": ["缺少 zh.md"],
                "image": image, "paragraphs": 0}

    paragraphs = stories.read_paragraphs(md)
    if not os.path.isfile(image):
        problems.append("缺少截图 %s.png" % story)

    for i, para in enumerate(paragraphs, 1):
        path = os.path.join(rdir, "seg_%02d.json" % i)
        if not os.path.isfile(path):
            problems.append("缺少 seg_%02d.json" % i)
            continue
        try:
            with open(path, encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError) as exc:
            problems.append("seg_%02d.json 读取失败：%s" % (i, exc))
            continue
        source = (row.get("source") or "").strip()
        final = (row.get("final") or "").strip()
        if source != para.strip():
            problems.append("第 %d 段与当前 zh.md 不一致（结果已过期）" % i)
            continue
        if not final:
            problems.append("第 %d 段译文为空" % i)
            continue
        items.append({"index": i, "source": para, "final": row["final"]})

    ok = bool(items) and not problems
    if allow_partial and items:
        ok = True
    return {"ok": ok, "items": items, "problems": problems,
            "image": image, "paragraphs": len(paragraphs)}


# --------------------------------------------------------------------------- #
# 文本排版
# --------------------------------------------------------------------------- #
def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise VideoError('需要 Pillow：pip install "story-tr[video]"') from exc
    return Image, ImageDraw, ImageFont


def find_fonts():
    """返回 (粗体路径, 常规路径)。"""
    for bold, regular in FONT_CANDIDATES:
        if os.path.isfile(bold) and os.path.isfile(regular):
            return bold, regular
    raise VideoError("找不到中文字体；请安装微软雅黑 / 思源黑体 / 文泉驿正黑")


def _tokens(text: str):
    """把文本拆成「ASCII 单词」与「单个非 ASCII 字符」，避免拆断英文单词。"""
    out, buf = [], ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "'-"):
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
    if buf:
        out.append(buf)
    return out


# 简化的中文避头尾：这些字符不应出现在行首 / 行尾
_NO_START = set("，。、；：？！）】》」』”’…—～％‰℃")
_NO_END = set("（【《「『“‘")


def _close_line(lines, cur: str):
    """收一行；若结尾是开引号/开括号，把它带到下一行。返回需要带走的字符。"""
    if cur and cur[-1] in _NO_END and len(cur) > 1:
        carry = cur[-1]
        lines.append(cur[:-1].rstrip())
        return carry
    lines.append(cur.rstrip())
    return ""


def wrap_text(draw, text: str, font, max_width: int):
    """按像素宽度换行；超宽的单个 token 按字符硬切。

    同时做简化的**避头尾**处理：标点不出现在行首、开引号不出现在行尾。
    """
    lines = []
    for para in (text or "").split("\n"):
        cur = ""
        for tok in _tokens(para):
            if draw.textlength(cur + tok, font=font) <= max_width:
                cur += tok
                continue

            if cur:
                # 下一个 token 是收尾标点 → 把它前面连续的一串标点 + 一个字一起挪到下一行
                if len(tok) == 1 and tok in _NO_START and len(cur) > 1:
                    k = len(cur) - 1
                    while k > 0 and cur[k] in _NO_START:
                        k -= 1
                    head, moved = cur[:k], cur[k:]
                    if head:
                        carry = _close_line(lines, head)
                        cur = carry + moved + tok
                    else:
                        cur = moved + tok
                else:
                    cur = _close_line(lines, cur) + tok.lstrip()
            elif draw.textlength(tok, font=font) <= max_width:
                cur = tok.lstrip()
            else:
                cur = tok  # 单个 token 自身就超宽，交给下面的硬切处理

            # 单个 token 自身就超宽：按字符硬切
            if draw.textlength(cur, font=font) <= max_width:
                continue
            piece = ""
            for ch in cur:
                if draw.textlength(piece + ch, font=font) <= max_width:
                    piece += ch
                else:
                    if piece:
                        lines.append(piece)
                    piece = ch
            cur = piece
        lines.append(cur.rstrip())
    return lines or [""]


def _truncate(draw, lines, limit: int, font, max_width: int):
    """保留前 `limit` 行，末行加 `…`（并保证不超宽）。"""
    if len(lines) <= limit:
        return lines
    out = lines[:max(limit, 1)]
    last = out[-1]
    while last and draw.textlength(last + "…", font=font) > max_width:
        last = last[:-1]
    out[-1] = last + "…"
    return out


def layout_text(draw, source: str, final: str, make_font,
                max_width: int = TEXT_MAX_W, max_height: int = TEXT_MAX_H):
    """自动缩放排版；最小字号仍放不下时截断。

    返回 `{size, src_lines, dst_lines, src_font, dst_font, src_lh, dst_lh, gap, height}`。
    """
    chosen = None
    for size in range(FONT_MAX, FONT_MIN - 1, -2):
        src_font, dst_font = make_font(size - 2, False), make_font(size, True)
        src_lines = wrap_text(draw, source, src_font, max_width)
        dst_lines = wrap_text(draw, final, dst_font, max_width)
        src_lh, dst_lh = int((size - 2) * 1.45), int(size * 1.45)
        gap = max(8, size // 4)
        height = len(src_lines) * src_lh + gap * 2 + 2 + len(dst_lines) * dst_lh
        if height <= max_height:
            chosen = (size, src_font, dst_font, src_lines, dst_lines,
                      src_lh, dst_lh, gap, height)
            break

    if chosen is None:
        size = FONT_MIN
        src_font, dst_font = make_font(size - 2, False), make_font(size, True)
        src_lh, dst_lh = int((size - 2) * 1.45), int(size * 1.45)
        gap = max(8, size // 4)
        src_lines = wrap_text(draw, source, src_font, max_width)
        dst_lines = wrap_text(draw, final, dst_font, max_width)
        avail = max_height - gap * 2 - 2
        max_lines = max(2, avail // max(src_lh, dst_lh))
        src_max = max(1, int(max_lines * src_lh / (src_lh + dst_lh)))
        dst_max = max(1, max_lines - src_max)
        src_lines = _truncate(draw, src_lines, src_max, src_font, max_width)
        dst_lines = _truncate(draw, dst_lines, dst_max, dst_font, max_width)
        height = len(src_lines) * src_lh + gap * 2 + 2 + len(dst_lines) * dst_lh
        chosen = (size, src_font, dst_font, src_lines, dst_lines,
                  src_lh, dst_lh, gap, height)

    (size, src_font, dst_font, src_lines, dst_lines, src_lh, dst_lh, gap, height) = chosen
    return {"size": size, "src_lines": src_lines, "dst_lines": dst_lines,
            "src_font": src_font, "dst_font": dst_font,
            "src_lh": src_lh, "dst_lh": dst_lh, "gap": gap, "height": height}


# --------------------------------------------------------------------------- #
# 画面合成
# --------------------------------------------------------------------------- #
def _cover_resize(img, width: int, height: int):
    """等比缩放后居中裁剪（cover）。"""
    from PIL import Image

    ratio = max(width / img.width, height / img.height)
    size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
    img = img.resize(size, Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def render_frame(bg_path: str, logo_path: str, source: str, final: str, out_path: str) -> None:
    """合成一帧（背景 + 压暗 + 文本块 + logo）并保存为 PNG。"""
    Image, ImageDraw, ImageFont = _pil()
    bold_path, regular_path = find_fonts()

    def make_font(size: int, bold: bool):
        path = bold_path if bold else regular_path
        return ImageFont.truetype(path, size, index=0)

    canvas = _cover_resize(Image.open(bg_path).convert("RGB"), OUT_W, OUT_H).convert("RGBA")
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, DIM_ALPHA)))
    draw = ImageDraw.Draw(canvas)

    box = layout_text(draw, source, final, make_font)
    panel_h = box["height"] + 2 * PANEL_PAD_Y
    panel_top = OUT_H - PANEL_BOTTOM - panel_h
    draw.rounded_rectangle(
        (PANEL_LEFT, panel_top, PANEL_RIGHT, OUT_H - PANEL_BOTTOM),
        radius=18, fill=PANEL_FILL)

    x = PANEL_LEFT + PANEL_PAD_X
    y = panel_top + PANEL_PAD_Y
    for line in box["src_lines"]:
        draw.text((x, y), line, font=box["src_font"], fill=SOURCE_COLOR)
        y += box["src_lh"]
    y += box["gap"]
    draw.line((x, y, PANEL_RIGHT - PANEL_PAD_X, y), fill=(255, 255, 255, 60), width=2)
    y += box["gap"] + 2
    for line in box["dst_lines"]:
        draw.text((x, y), line, font=box["dst_font"], fill=FINAL_COLOR)
        y += box["dst_lh"]

    logo = Image.open(logo_path).convert("RGBA").resize((ICON, ICON), Image.LANCZOS)
    canvas.alpha_composite(logo, (ICON_X, OUT_H - ICON_BOTTOM - ICON))
    canvas.convert("RGB").save(out_path, "PNG")


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise VideoError("找不到 ffmpeg；请安装并确保它在 PATH 中")
    return exe


def _concat_line(path: str) -> str:
    return "file '%s'\n" % path.replace("\\", "/").replace("'", "'\\''")


def encode_slideshow(frames, duration: float, out_path: str,
                     fps: int = FPS_DEFAULT, audio: bool = True) -> None:
    """把若干张 PNG 按每张 `duration` 秒编码为 H.264 MP4。"""
    if not frames:
        raise VideoError("没有可编码的帧")
    ffmpeg = _ffmpeg()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="story-tr-video-") as td:
        listing = os.path.join(td, "frames.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for p in frames:
                f.write(_concat_line(p))
                f.write("duration %.3f\n" % duration)
            f.write(_concat_line(frames[-1]))  # 末尾再列一次，保证最后一屏时长生效

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", listing]
        if audio:
            cmd += ["-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000"]
        cmd += ["-vf", "fps=%d,format=yuv420p" % fps,
                "-c:v", "libx264", "-profile:v", "high",
                "-b:v", VIDEO_BITRATE, "-maxrate", "12M", "-bufsize", "20M",
                "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0"]
        if audio:
            cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000", "-ac", "2",
                    "-shortest"]
        cmd += ["-t", "%.3f" % (len(frames) * duration),
                "-movflags", "+faststart", out_path]
        _run(cmd)


def concat_videos(inputs, out_path: str) -> None:
    """按顺序无损拼接（编码参数一致，`-c copy`）。"""
    if not inputs:
        raise VideoError("没有可拼接的视频")
    ffmpeg = _ffmpeg()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="story-tr-concat-") as td:
        listing = os.path.join(td, "videos.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for p in inputs:
                f.write(_concat_line(p))
        _run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
              "-f", "concat", "-safe", "0", "-i", listing,
              "-c", "copy", "-movflags", "+faststart", out_path])


def _run(cmd) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise VideoError("ffmpeg 失败（退出码 %d）：%s" % (proc.returncode, detail[-1200:]))


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
def render_story(char_id: str, story: str, items, image: str, out_path: str,
                 duration: float, fps: int, logo: str, audio: bool = True) -> None:
    """一个故事 = 每段一帧，按时长编码为一条视频。"""
    with tempfile.TemporaryDirectory(prefix="story-tr-frames-") as td:
        frames = []
        for item in items:
            frame = os.path.join(td, "seg_%02d.png" % item["index"])
            render_frame(image, logo, item["source"], item["final"], frame)
            frames.append(frame)
        encode_slideshow(frames, duration, out_path, fps=fps, audio=audio)


def _human(size: int) -> str:
    if size < 1024:
        return "%d B" % size
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
    return "%.1f GB" % size


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_parser(sub):
    """注册 `story-tr video` 子命令。"""
    p = sub.add_parser(
        "video",
        help="把翻译结果渲染成「谷歌翻译 N 次」风格视频（B 站 1080P）",
        parents=[paths.data_dir_parent()],
        epilog="示例：story-tr video 10000021   |   story-tr video 10000021 --only story   |   "
               "story-tr video 10000021 --stories vision --overwrite",
    )
    p.add_argument("char_ids", nargs="*",
                   help="角色 id / 范围（空格或逗号分隔）："
                        "10000021 / 10000002-10000030 / 10000021,10000025-10000030")

    g = p.add_argument_group("角色选择")
    g.add_argument("--all-characters", action="store_true",
                   help="处理 data/character.json 中的全部角色（与 char_ids 互斥）")

    g = p.add_argument_group("故事选择")
    g.add_argument("--stories", default="",
                   help="只处理指定故事，逗号分隔（不指定 = 该角色全部故事）")

    g = p.add_argument_group("输出")
    g.add_argument("--only", choices=["story", "total", "both"], default="both",
                   help="仅分故事 / 仅总视频 / 两者（默认 both）")
    g.add_argument("--duration", type=float, default=DURATION_DEFAULT,
                   help="每段停留秒数（默认 %.1f）" % DURATION_DEFAULT)
    g.add_argument("--fps", type=int, default=FPS_DEFAULT, help="帧率（默认 30）")
    g.add_argument("--logo", default="",
                   help="logo 路径（默认 assets/translate-logo.webp）")
    g.add_argument("--overwrite", action="store_true", help="已有 mp4 也重新生成")
    g.add_argument("--no-audio", action="store_true", help="不添加静音音轨")

    g = p.add_argument_group("不完整结果")
    g.add_argument("--allow-partial", action="store_true",
                   help="缺少部分段落时，用已有段落生成（默认跳过该故事）")
    g.add_argument("--strict", action="store_true",
                   help="任一故事不完整就整体失败，不生成任何文件")
    report.add_verbosity_args(p)

    p.set_defaults(func=main)
    return p


def main(args) -> int:
    if args.char_ids and args.all_characters:
        print("不能同时指定角色 id 与 --all-characters，请二选一。")
        return 1
    if not args.char_ids and not args.all_characters:
        print("请指定角色 id / 范围（如 10000021 或 10000002-10000030），"
              "或用 --all-characters 处理全部角色。")
        return 1
    if args.duration <= 0:
        print("--duration 必须大于 0。")
        return 1

    all_ids = stories.load_char_ids()
    char_ids = all_ids if args.all_characters else stories.parse_char_ids(args.char_ids, all_ids)
    if not char_ids:
        print("没有匹配到任何角色 id；请对照 data/character.json 检查。")
        return 1

    logo = args.logo or os.path.join(paths.home(), "assets", "translate-logo.webp")
    if not os.path.isfile(logo):
        print("找不到 logo：%s（可用 --logo 指定）" % logo)
        return 1
    _pil()  # 尽早给出依赖错误
    _ffmpeg()

    names = [s.strip() for s in args.stories.split(",") if s.strip()] or None

    # ---- 预检 ----
    plan, skipped = [], []
    for cid in char_ids:
        for story in select_stories(cid, names):
            chk = check_story(cid, story, allow_partial=args.allow_partial)
            if not chk["ok"]:
                skipped.append((cid, story, chk["problems"]))
                continue
            plan.append((cid, story, chk))

    if skipped:
        for cid, story, problems in skipped:
            print("跳过 %s/%s：%s" % (cid, story, "；".join(problems)))
        if args.strict:
            print("--strict：存在不完整的故事，未生成任何文件。")
            return 1
    if not plan:
        print("没有可生成视频的故事（检查翻译结果是否完整）。")
        return 1

    # ---- 分故事 ----
    rendered = {}
    for cid, story, chk in report_progress(plan):
        out = os.path.join(video_dir(cid), story + ".mp4")
        rendered.setdefault(cid, []).append((story, out))
        if os.path.exists(out) and not args.overwrite:
            print("跳过（已存在）%s" % stories.rel(out))
            continue
        render_story(cid, story, chk["items"], chk["image"], out,
                     duration=args.duration, fps=args.fps, logo=logo,
                     audio=not args.no_audio)
        if not args.quiet:
            print("生成 %s  %d 段 / %.1fs / %s"
                  % (stories.rel(out), len(chk["items"]),
                     len(chk["items"]) * args.duration, _human(os.path.getsize(out))))

    # ---- 总视频 ----
    if args.only in ("total", "both"):
        for cid in char_ids:
            entries = rendered.get(cid) or []
            if not entries:
                continue
            available = [p for _, p in entries if os.path.isfile(p)]
            if not available:
                print("跳过 %s 的总视频：没有已生成的单故事视频。" % cid)
                continue
            out = os.path.join(video_dir(cid), cid + ".mp4")
            if os.path.exists(out) and not args.overwrite:
                print("跳过（已存在）%s" % stories.rel(out))
                continue
            concat_videos(available, out)
            if not args.quiet:
                total = os.path.getsize(out)
                print("生成 %s  %d 个故事 / %s"
                      % (stories.rel(out), len(available), _human(total)))

    if not args.quiet:
        print("完成。视频目录：%s" % stories.rel(video_dir(char_ids[0])))
    return 0


def report_progress(plan):
    """按故事顺序迭代，并在交互终端显示进度。"""
    from tqdm import tqdm

    bar = tqdm(plan, desc="视频", unit="条", **report.bar_kwargs())
    with bar:
        for cid, story, chk in bar:
            bar.set_postfix_str("%s/%s" % (cid, story))
            yield cid, story, chk
