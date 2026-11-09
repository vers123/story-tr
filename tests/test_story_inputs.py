"""story_inputs：角色 id 解析与故事读取。"""

import pytest

from story_tr.services import story_inputs
from tests.support import setup_workspace as _setup_workspace


def test_parse_char_ids_range_and_missing():
    assert story_inputs.parse_char_ids(["1-3"], ["1", "2", "3"], warn_missing=True) == ["1", "2", "3"]
    assert story_inputs.parse_char_ids(["3-1"], ["1", "2", "3"], warn_missing=True) == ["1", "2", "3"]
    with pytest.raises(ValueError):
        story_inputs.parse_char_ids(["a-b"], ["1"], warn_missing=True)
    assert story_inputs.parse_char_ids(["999"], ["1"], warn_missing=True) == []


def test_resolve_stories(tmp_path):
    _setup_workspace(tmp_path)
    assert story_inputs.resolve_stories(str(tmp_path), "10000021", ["vision"]) == ["vision"]
    assert story_inputs.resolve_stories(str(tmp_path), "10000021", []) == ["vision"]
    assert story_inputs.resolve_stories(str(tmp_path), "404", []) == []
