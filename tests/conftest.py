from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:  # 允许不安装直接跑测试
    sys.path.insert(0, str(SRC))

from telemsg.config import Settings  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    configured = Settings(
        bot_token="123456:TEST_TOKEN",
        db_path=tmp_path / "telemsg.sqlite3",
        upload_dir=tmp_path / "uploads",
        # 关键：测试绝不能写到仓库根目录的真实 .env
        env_file=tmp_path / ".env",
        max_retries=1,
        timeout_seconds=5.0,
    )
    configured.ensure_dirs()
    return configured


@pytest.fixture()
def png_bytes() -> bytes:
    """一张 2x2 的合法 PNG。"""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGP8z8DAwMDAxMD"
        "AwMAAAA0AAf8B0m0AAAAASUVORK5CYII="
    )
