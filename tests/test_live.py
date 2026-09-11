"""对真实 Telegram API 的联调测试。

默认**全部跳过**，因为单元测试不该依赖网络，也不该真的往频道发东西。

想跑它们::

    TELEMSG_LIVE_TEST=1 .venv/bin/python -m pytest tests/test_live.py -v

如果还想验证「真的能发出去」，再给一个测试频道（建议先建个私密频道）::

    TELEMSG_LIVE_TEST=1 TELEMSG_TEST_CHAT=@my_test_channel \\
        .venv/bin/python -m pytest tests/test_live.py -v
"""

from __future__ import annotations

import os

import pytest

from telemsg.client import TelegramClient, mask_token
from telemsg.config import get_settings
from telemsg.models import Draft, Keyboard

live = os.environ.get("TELEMSG_LIVE_TEST") == "1"
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not live, reason="需要 TELEMSG_LIVE_TEST=1 才跑真实 API 测试"),
]


@pytest.fixture(scope="module")
def settings():
    cfg = get_settings()
    if not cfg.resolved_token:
        pytest.skip("没有配置 Bot Token（TELEMSG_BOT_TOKEN）")
    return cfg


@pytest.mark.asyncio
async def test_token_is_alive(settings) -> None:
    """能拿到 getMe 就说明 token 有效。"""
    async with TelegramClient(settings=settings, max_retries=0) as client:
        info = await client.get_me()
    assert info["is_bot"] is True
    assert info["username"], "机器人没有设置 username"
    # 只打印脱敏后的信息，测试日志里也不能出现明文 token
    print(f"\n  bot=@{info['username']} id={info['id']} token={mask_token(settings.resolved_token)}")


@pytest.mark.asyncio
async def test_target_chat_is_reachable(settings) -> None:
    """若配置了 TELEMSG_TEST_CHAT，验证机器人确实能访问该会话。"""
    target = os.environ.get("TELEMSG_TEST_CHAT") or settings.default_chat_id
    if not target:
        pytest.skip("未设置 TELEMSG_TEST_CHAT / TELEMSG_DEFAULT_CHAT_ID")
    async with TelegramClient(settings=settings, max_retries=0) as client:
        chat = await client.get_chat(target)
    assert chat.get("id") is not None
    print(f"\n  chat={chat.get('title') or chat.get('username')} type={chat.get('type')}")


@pytest.mark.asyncio
async def test_can_send_text_with_buttons(settings) -> None:
    """真实发送一条带按钮的消息，发送后立刻撤回。

    需要显式提供 TELEMSG_TEST_CHAT，避免误发到生产频道。
    """
    target = os.environ.get("TELEMSG_TEST_CHAT")
    if not target:
        pytest.skip("未设置 TELEMSG_TEST_CHAT，跳过真实发送")

    draft = Draft(
        chat_id=target,
        text="🧪 TeleMsgCreator 自检消息（会自动撤回）",
        parse_mode="HTML",
        keyboard=Keyboard.from_spec("👉 项目文档|https://core.telegram.org/bots/api"),
    )
    async with TelegramClient(settings=settings, max_retries=0) as client:
        result = await client.send_draft(draft)
        assert result.ok and result.message_id
        await client.delete_message(target, result.message_id)
