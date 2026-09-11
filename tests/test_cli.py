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
