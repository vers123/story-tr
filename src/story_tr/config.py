"""pydantic-settings 配置：从 `.env` 与环境变量读取。

字段名对应 `.env` 键（pydantic-settings 默认大小写不敏感地把字段名映射为
大写环境变量名，因此 `google_api_key` 即读取 `GOOGLE_API_KEY`）。
"""
import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _unescape(value: str) -> str:
    """把字面的 `\\n` / `\\r` / `\\t` 转成真实控制字符（CLI 与环境变量常见写法）。"""
    if not value:
        return value
    return (value.replace("\\r\\n", "\n").replace("\\n", "\n")
            .replace("\\r", "\r").replace("\\t", "\t"))


class Settings(BaseSettings):
    """全局配置（`.env` / 环境变量）。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 工作目录（`.env` 键为 STORY_TR_HOME；字段名统一用 data_dir）
    data_dir: str = Field(default="", alias="STORY_TR_HOME")

    google_api_key: str = ""
    bing_api_key: str = ""
    bing_region: str = ""
    baidu_appid: str = ""
    baidu_appkey: str = ""

    # 代理：优先 HTTPS_PROXY，其次 HTTP_PROXY
    proxy: str = Field(default="", alias="HTTPS_PROXY")
    http_proxy: str = Field(default="", alias="HTTP_PROXY")

    # 本地 RWKV（RWKV-Runner）
    rwkv_api_url: str = "http://127.0.0.1:8000"
    rwkv_prompt_template: str = "User: {text}\\n\\nBot:"
    rwkv_max_tokens: int = 1024
    rwkv_temperature: float = 1.0
    rwkv_top_p: float = 0.3
    rwkv_timeout: float = 300.0
    rwkv_chunk_chars: int = 1200
    rwkv_retries: int = 2
    rwkv_model_path: str = ""

    @model_validator(mode="after")
    def _unescape_rwkv_template(self) -> "Settings":
        """把 `RWKV_PROMPT_TEMPLATE` 里字面的 `\\n` 转成真实换行。"""
        self.rwkv_prompt_template = _unescape(self.rwkv_prompt_template)
        return self

    @property
    def rwkv_template_unescaped(self) -> str:
        """返回真实换行的提示词模板（字段值在构造时已转义）。"""
        return self.rwkv_prompt_template


_settings: Settings | None = None


def load_settings() -> Settings:
    """读取并缓存 `Settings` 单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


_MARKER = os.path.join("data", "character.json")


def resolve_data_dir(cli_value: str | None) -> str:
    """解析工作目录（绝对路径）：CLI 值 → STORY_TR_HOME → 向上查找 data/character.json → CWD。"""
    if cli_value:
        return os.path.abspath(cli_value)
    home = load_settings().data_dir
    if home:
        return os.path.abspath(home)
    cwd = os.path.abspath(os.getcwd())
    cur = cwd
    while True:
        if os.path.exists(os.path.join(cur, _MARKER)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return cwd
        cur = parent
