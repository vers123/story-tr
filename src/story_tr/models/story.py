"""故事文档模型：`*.md` 的 frontmatter 与正文段落。"""
from pydantic import BaseModel


class StoryFrontmatter(BaseModel):
    """故事 Markdown 的 frontmatter。"""

    id: str
    story: str
    lang: str
    title: str


class StoryDocument(BaseModel):
    """故事文档（frontmatter + 正文段落列表）。"""

    frontmatter: StoryFrontmatter
    paragraphs: list[str]
