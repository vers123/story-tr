"""角色数据模型：索引 `CharacterRef`、档案 `CharacterMeta` / `CharacterText`。"""
from pydantic import BaseModel


class Costume(BaseModel):
    """角色装扮（中/英双语）。"""

    name_en: str
    name_zh: str
    description_en: str
    description_zh: str


class NameCard(BaseModel):
    """角色名片（中/英双语）。"""

    name_en: str
    name_zh: str
    description_en: str
    description_zh: str


class SpecialFood(BaseModel):
    """角色特殊料理（中/英双语名称）。"""

    name_en: str
    name_zh: str


class CharacterRef(BaseModel):
    """角色索引条目（`data/character.json`）。"""

    id: str
    name_en: str
    name_zh: str
    path: str
    stories: list[str] = []


class CharacterMeta(BaseModel):
    """角色语言无关属性（`profile/meta.json`）。"""

    id: str
    rank: int | None = None
    element: str | None = None
    weapon_type: str | None = None
    region: str | None = None
    body_type: str | None = None
    special_prop: str | None = None
    icon: str | None = None
    birthday: list[int] | None = None
    release: int | None = None


class CharacterText(BaseModel):
    """角色文本（`profile/text.json`，中/英叶子级）。"""

    title_en: str
    title_zh: str
    detail_en: str
    detail_zh: str
    constellation_en: str
    constellation_zh: str
    native_en: str
    native_zh: str
    cv: dict[str, str] = {}
    costume: list[Costume] = []
    name_card: NameCard | None = None
    special_food: SpecialFood | None = None
