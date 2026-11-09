"""services.folders：故事文件夹重命名。"""

import json

from story_tr.errors import ExitCode
from story_tr.services import folders as folders_mod
from tests.support import folders_home


def test_folders_rename_idempotent(tmp_path):
    data = tmp_path / "data"
    char_dir = data / "story" / "character" / "10000021"
    story_dir = char_dir / "profile" / "story" / "old_name"
    story_dir.mkdir(parents=True)
    (story_dir / "zh.md").write_text("第一段\n", encoding="utf-8")
    (data / "character.json").write_text(
        json.dumps(
            [{"id": "10000021", "name_en": "A", "name_zh": "甲", "path": "p", "stories": ["new_name"]}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.OK
    assert (char_dir / "profile" / "story" / "new_name").exists()
    assert not (char_dir / "profile" / "story" / "old_name").exists()
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.OK


def test_folders_missing_char_file(tmp_path):
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.USAGE


def test_folders_rename_success(tmp_path):
    base = folders_home(tmp_path, [{"id": "1", "stories": ["new"]}])
    sdir = base / "1" / "profile" / "story"
    (sdir / "old").mkdir(parents=True)
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.OK
    assert (sdir / "new").is_dir()


def test_folders_warnings(tmp_path):
    base = folders_home(
        tmp_path,
        [
            {"id": "1", "stories": ["character_details"]},  # 缺少 profile/story 目录
            {"id": "2", "stories": ["b", "c"]},  # 当前多余与目标多余数量不一致
            {"id": "3", "stories": ["character_details"]},  # 目录存在但缺固定文件夹
        ],
    )
    (base / "2" / "profile" / "story" / "a").mkdir(parents=True)
    (base / "3" / "profile" / "story").mkdir(parents=True)
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.USAGE
