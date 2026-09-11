from __future__ import annotations

import json

from typer.testing import CliRunner

from telemsg.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "TeleMsgCreator" in result.stdout


def test_example_is_valid_json() -> None:
    result = runner.invoke(app, ["example"])
    assert result.exit_code == 0
    assert '"chat_id"' in result.stdout


def test_validate_ok(tmp_path) -> None:
    draft = {"chat_id": "@c", "text": "hi", "keyboard": "A|https://a.com"}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    result = runner.invoke(app, ["validate", "--json", str(path)])
    assert result.exit_code == 0


def test_validate_fails_on_too_long_text(tmp_path) -> None:
    draft = {"chat_id": "@c", "text": "a" * 5000}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    result = runner.invoke(app, ["validate", "--json", str(path)])
    assert result.exit_code == 2


def test_preview_writes_file(tmp_path) -> None:
    draft = {"chat_id": "@c", "text": "**粗体**", "keyboard": "A|https://a.com"}
    src = tmp_path / "d.json"
    src.write_text(json.dumps(draft), encoding="utf-8")
    out = tmp_path / "p.html"
    result = runner.invoke(app, ["preview", "--json", str(src), "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists() and "粗体" in out.read_text(encoding="utf-8")


def test_send_dry_run(tmp_path) -> None:
    draft = {"chat_id": "@c", "text": "hi", "keyboard": "A|https://a.com"}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    result = runner.invoke(
        app,
        ["send", "--json", str(path), "--dry-run", "--token", "123:FAKE"],
    )
    assert result.exit_code == 0
    assert "dry-run" in result.stdout


def test_schema_command() -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert "properties" in result.stdout


def test_photo_url_is_not_mangled_by_pathlib() -> None:
    """回归：--photo 曾用 Path 接收，会把 https:// 折叠成 https:/ 而误判成本地文件。"""
    from telemsg.cli import _draft_from_options

    draft = _draft_from_options(
        chat="@c",
        text="看图",
        parse_mode="HTML",
        photo="https://example.com/a.jpg",
        video=None,
        document=None,
        caption=None,
        buttons=[],
        per_row=1,
        spoil=False,
        silent=False,
        protect=False,
        thread_id=None,
        name=None,
    )
    assert draft.media is not None
    assert draft.media.source == "https://example.com/a.jpg"
    assert draft.media.is_remote_url
    assert draft.media.caption == "看图"


def test_validate_prints_hints(tmp_path) -> None:
    draft = {"chat_id": "@c", "text": "a" * 5000}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    # 加宽终端，避免 Rich 把表格内容截断导致断言失效
    result = runner.invoke(app, ["validate", "--json", str(path)], env={"COLUMNS": "200"})
    assert result.exit_code == 2
    assert "4096" in result.stdout
    assert "拆分为多条消息" in result.stdout
