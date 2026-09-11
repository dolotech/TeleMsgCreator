from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telemsg.models import Draft, SendResult
from telemsg.store import Store


def test_template_crud(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    store.save_template("a", Draft(chat_id="@c", text="1"))
    store.save_template("a", Draft(chat_id="@c", text="2"))
    assert store.get_template("a").text == "2"
    assert len(store.list_templates()) == 1
    assert store.delete_template("a")
    assert store.get_template("a") is None


def test_schedule_lifecycle(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    sid = store.add_schedule(Draft(chat_id="@c", text="x"), chat_id="@c", run_at=past)
    due = store.due_schedules()
    assert [row["id"] for row in due] == [sid]
    store.mark_schedule(sid, status="done", next_run_at=None, error=None)
    assert store.due_schedules() == []
    assert store.list_schedules()[0]["status"] == "done"


def test_schedule_requires_trigger(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        store.add_schedule(Draft(chat_id="@c", text="x"), chat_id="@c")
    except ValueError as exc:
        assert "run_at" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当抛出 ValueError")


def test_cancel_schedule(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    sid = store.add_schedule(
        Draft(chat_id="@c", text="x"), chat_id="@c", run_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    assert store.cancel_schedule(sid)
    assert not store.cancel_schedule(sid)


def test_send_audit(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    store.record_send(SendResult(ok=True, chat_id="@c", message_id=5, method="sendMessage"))
    store.record_send(SendResult.failure(RuntimeError("boom"), chat_id="@c", method="sendMessage"))
    rows = store.recent_sends()
    assert len(rows) == 2
    assert any(r["ok"] == 0 for r in rows)


def test_assets_and_file_id(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    aid = store.add_asset(filename="a.png", path="/tmp/a.png", size_bytes=10, mime_type="image/png")
    store.remember_file_id(aid, "FILE_ID")
    assert store.list_assets()[0]["file_id"] == "FILE_ID"
