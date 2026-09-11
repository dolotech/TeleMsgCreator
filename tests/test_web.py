from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from telemsg.web.app import create_app

DRAFT = {
    "chat_id": "@my_channel",
    "parse_mode": "HTML",
    "text": "<b>标题</b> 正文",
    "keyboard": "🛒 购买|https://shop.example.com|📋 复制|copy:CODE",
}


@pytest.fixture()
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_and_index(client) -> None:
    assert client.get("/api/health").json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "TeleMsgCreator" in page.text


def test_preview_endpoint_sanitizes(client) -> None:
    payload = dict(DRAFT)
    payload["text"] = '<b>ok</b><script>alert(1)</script>'
    response = client.post("/api/preview", json={"draft": payload})
    assert response.status_code == 200
    body = response.json()
    assert "<script>" not in body["html"]
    assert body["report"]["ok"] is True


def test_preview_reports_errors(client) -> None:
    payload = dict(DRAFT)
    payload["text"] = "a" * 5000
    response = client.post("/api/preview", json={"draft": payload})
    assert response.status_code == 200
    assert response.json()["report"]["ok"] is False


def test_empty_draft_previews_gracefully(client) -> None:
    """空编辑器是正常初始状态，不该返回 422 刷错误。"""
    response = client.post("/api/preview", json={"draft": {"chat_id": "", "text": ""}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "tg-empty" in body["html"]
    assert body["report"]["ok"] is True


def test_broken_button_returns_readable_reason(client) -> None:
    """按钮写错时要给人话，而不是 pydantic 的错误数组。"""
    payload = {
        "chat_id": "@c",
        "text": "hi",
        "keyboard": {"rows": [[{"text": "坏的", "url": "https://a.com", "callback_data": "x"}]]},
    }
    response = client.post("/api/preview", json={"draft": payload})
    assert response.status_code == 200
    errors = response.json()["report"]["errors"]
    assert errors and isinstance(errors[0]["message"], str)
    assert "动作字段" in errors[0]["message"]


def test_send_with_broken_draft_returns_400_not_422(client) -> None:
    response = client.post("/api/send", json={"draft": {"chat_id": "@c"}})
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_compile_matches_expected_method(client) -> None:
    body = client.post("/api/compile", json={"draft": DRAFT}).json()
    assert body["request"]["method"] == "sendMessage"
    assert body["request"]["params"]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://shop.example.com"


def test_send_reports_api_error(client, settings, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})

    monkeypatch.setattr(httpx, "AsyncClient", _make_client_class(handler))
    response = client.post("/api/send", json={"draft": DRAFT})
    assert response.status_code == 400
    body = response.json()
    # 面向使用者的中文诊断 + 可执行建议，而不是 Telegram 的英文原文
    assert body["error"] == "找不到目标会话"
    assert body["hint"] and "频道" in body["hint"]
    assert "chat not found" in (body["raw"] or "")


def test_send_success(client, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

    monkeypatch.setattr(httpx, "AsyncClient", _make_client_class(handler))
    body = client.post("/api/send", json={"draft": DRAFT}).json()
    assert body["ok"] is True
    assert body["result"]["message_id"] == 88
    assert client.get("/api/history").json()["items"]


def _make_client_class(handler):
    """把 httpx.AsyncClient 替换成始终注入 MockTransport 的版本。"""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    return factory


def test_upload_and_assets(client, png_bytes) -> None:
    response = client.post("/api/upload", files={"file": ("a.png", png_bytes, "image/png")})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "photo"
    assert body["size"] == len(png_bytes)


def test_upload_rejects_unknown_type(client) -> None:
    response = client.post("/api/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert response.status_code == 400


def test_templates_api(client) -> None:
    assert client.post("/api/templates", json={"name": "t", "draft": DRAFT}).json()["ok"]
    assert client.get("/api/templates/t").json()["draft"]["text"] == DRAFT["text"]
    assert client.delete("/api/templates/t").json()["ok"]
    assert client.get("/api/templates/t").status_code == 404


def test_schedule_api(client) -> None:
    created = client.post("/api/schedule", json={"draft": DRAFT, "when": "2030-05-01T09:00:00Z"})
    assert created.status_code == 200
    sid = created.json()["id"]
    assert any(row["id"] == sid for row in client.get("/api/schedules").json()["items"])
    assert client.delete(f"/api/schedules/{sid}").json()["ok"]
    assert client.delete(f"/api/schedules/{sid}").status_code == 404


def test_basic_auth_enforced(settings) -> None:
    settings.ui_password = "s3cret"
    guarded = TestClient(create_app(settings))
    assert guarded.get("/").status_code == 401
    assert guarded.get("/", auth=("telemsg", "wrong")).status_code == 401
    assert guarded.get("/", auth=("telemsg", "s3cret")).status_code == 200


def test_static_assets_served(client) -> None:
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


# ------------------------------------------------------------------ 设置/引导
NEW_TOKEN = "999888777:AAHfakeNewTokenForTestsOnly_12345678"


def test_read_settings_never_leaks_token(client) -> None:
    body = client.get("/api/settings").json()
    assert body["ok"] is True
    assert body["settings"]["has_token"] is True
    assert body["settings"]["token_hint"] == "123456...OKEN"
    assert "TEST_TOKEN" not in json.dumps(body, ensure_ascii=False)


def test_save_token_rejects_invalid_token(client, settings, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    monkeypatch.setattr(httpx, "AsyncClient", _make_client_class(handler))
    response = client.post("/api/settings", json={"bot_token": NEW_TOKEN})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert "Token" in body["error"]
    assert body["hint"]
    # 验证失败就不该落盘
    assert not Path(settings.env_file).exists()


def test_save_token_writes_env_and_applies_immediately(client, settings, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 42, "is_bot": True, "username": "MyBot", "first_name": "My"}},
        )

    monkeypatch.setattr(httpx, "AsyncClient", _make_client_class(handler))
    response = client.post("/api/settings", json={"bot_token": NEW_TOKEN, "default_chat_id": "@chan"})
    assert response.status_code == 200
    body = response.json()
    assert body["bot"]["username"] == "MyBot"
    assert body["settings"]["saved_to"] == str(settings.env_file)

    env_text = Path(settings.env_file).read_text(encoding="utf-8")
    assert f"TELEMSG_BOT_TOKEN={NEW_TOKEN}" in env_text
    assert "TELEMSG_DEFAULT_CHAT_ID=@chan" in env_text
    # 立即生效，不需要重启
    assert client.get("/api/settings").json()["settings"]["token_hint"] == "999888...5678"


def test_save_without_persist_keeps_disk_untouched(client, settings, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True, "username": "B"}})

    monkeypatch.setattr(httpx, "AsyncClient", _make_client_class(handler))
    body = client.post("/api/settings", json={"bot_token": NEW_TOKEN, "persist": False}).json()
    assert body["ok"] is True
    assert body["settings"]["saved_to"] is None
    assert not Path(settings.env_file).exists()


def test_me_returns_readable_diagnosis_when_token_missing(settings) -> None:
    settings = settings.model_copy(update={"bot_token": None})
    bare = TestClient(create_app(settings))
    response = bare.get("/api/me")
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["action"] == "open_settings"


def test_index_exposes_bootstrap_flags(settings) -> None:
    page = TestClient(create_app(settings)).get("/")
    assert "TELEMSG_BOOT" in page.text
    assert "hasToken: true" in page.text
