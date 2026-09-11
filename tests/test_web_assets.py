from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "telemsg" / "web"
APP_JS = WEB_DIR / "static" / "app.js"
INDEX_HTML = WEB_DIR / "templates" / "index.html"
STYLES = WEB_DIR / "static" / "styles.css"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能做 JS 语法检查")
def test_app_js_parses() -> None:
    """前端的语法错误会让整页 JS 静默失效，必须挡住。"""
    result = subprocess.run(
        ["node", "--check", str(APP_JS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_index_references_static_assets() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "/static/app.js" in html
    assert "/static/styles.css" in html
    assert (WEB_DIR / "static" / "app.js").exists()
    assert STYLES.exists()


def test_ids_used_by_js_exist_in_template_or_are_created_by_js() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
    assert ids, "没有解析到任何 id 选择器，测试本身可能失效了"
    missing = []
    for element_id in sorted(ids):
        if f'id="{element_id}"' in html:
            continue
        # JS 动态生成的节点也算数
        if f'id=\\"{element_id}\\"' in js or f"id='{element_id}'" in js or f'id="{element_id}"' in js:
            continue
        missing.append(element_id)
    assert not missing, f"这些 id 在前端找不到对应节点: {missing}"


def test_stylesheet_defines_telegram_classes() -> None:
    css = STYLES.read_text(encoding="utf-8")
    for class_name in ("tg-bubble", "tg-button", "tg-keyboard", "issues", "dropzone"):
        assert f".{class_name}" in css
