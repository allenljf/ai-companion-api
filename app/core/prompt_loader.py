"""prompt 模板載入（migration-plan 4.5：抽成檔案 + Jinja2）。

- Jinja2 而非 str.format()：prompt 內含大量 {"..."} JSON 範例，format 會衝突
- StrictUndefined：漏傳變數直接炸在開發期，不默默輸出空字串
- 模板快取由 Jinja2 Environment 內建（cache_size 預設 400）
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

_env = Environment(
    loader=FileSystemLoader(PROMPT_DIR),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=False,
)


def render_prompt(template_path: str, /, **variables) -> str:
    """載入 app/prompts/{template_path}.txt 並以 Jinja2 渲染。

    第一個參數限定位置傳遞（/），避免與模板變數（如 name）撞名。
    """
    return _env.get_template(f"{template_path}.txt").render(**variables)
