from __future__ import annotations

import stat

from telemsg.config import Settings, env_file_path, update_env_file
from telemsg.logging_setup import mask_token


def test_update_env_file_creates_with_restrictive_mode(tmp_path) -> None:
    target = tmp_path / ".env"
    update_env_file({"TELEMSG_BOT_TOKEN": "123:ABC"}, path=target)
    assert target.read_text(encoding="utf-8").strip() == "TELEMSG_BOT_TOKEN=123:ABC"
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"token 文件权限过宽: {oct(mode)}"


def test_update_env_file_preserves_comments_and_other_keys(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "# 我的配置\nTELEMSG_BOT_TOKEN=old\nTELEMSG_UI_PORT=9000\n\n# 尾部注释\n",
        encoding="utf-8",
    )
    update_env_file({"TELEMSG_BOT_TOKEN": "new"}, path=target)
    text = target.read_text(encoding="utf-8")
    assert "# 我的配置" in text
    assert "# 尾部注释" in text
    assert "TELEMSG_UI_PORT=9000" in text
    assert "TELEMSG_BOT_TOKEN=new" in text
    assert "old" not in text


def test_update_env_file_appends_new_key(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("TELEMSG_BOT_TOKEN=1:2\n", encoding="utf-8")
    update_env_file({"TELEMSG_DEFAULT_CHAT_ID": "@chan"}, path=target)
    text = target.read_text(encoding="utf-8")
    assert "TELEMSG_DEFAULT_CHAT_ID=@chan" in text
    assert text.startswith("TELEMSG_BOT_TOKEN=1:2")


def test_update_env_file_removes_key_when_none(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("TELEMSG_BOT_TOKEN=1:2\nTELEMSG_UI_PORT=9000\n", encoding="utf-8")
    update_env_file({"TELEMSG_BOT_TOKEN": None}, path=target)
    text = target.read_text(encoding="utf-8")
    assert "TELEMSG_BOT_TOKEN" not in text
    assert "TELEMSG_UI_PORT=9000" in text


def test_update_env_file_is_idempotent(tmp_path) -> None:
    target = tmp_path / ".env"
    for _ in range(3):
        update_env_file({"TELEMSG_BOT_TOKEN": "same"}, path=target)
    assert target.read_text(encoding="utf-8").count("TELEMSG_BOT_TOKEN") == 1


def test_env_file_path_follows_cwd() -> None:
    assert env_file_path().name == ".env"
    assert env_file_path().is_absolute()


def test_public_view_never_exposes_token(settings) -> None:
    view = settings.public_view()
    assert "TEST_TOKEN" not in str(view)
    assert view["has_token"] is True
    assert view["token_hint"].startswith("123456")


def test_public_view_reports_missing_token(settings) -> None:
    blank = settings.model_copy(update={"bot_token": None})
    assert blank.public_view()["has_token"] is False


def test_token_from_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("TELEMSG_BOT_TOKEN", "999:FROMENV")
    # _env_file=None 表示忽略仓库里的 .env，避免测试读到真实配置
    assert Settings(_env_file=None).public_view()["token_from_env"] is True
    monkeypatch.delenv("TELEMSG_BOT_TOKEN")
    assert Settings(_env_file=None).public_view()["token_from_env"] is False


def test_mask_token_helper() -> None:
    assert mask_token("123456789:AAHsecret") == "123456...cret"
    assert mask_token(None) == "<未配置>"
    assert mask_token("short") == "***"
