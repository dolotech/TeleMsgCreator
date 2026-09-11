from __future__ import annotations

import socket

import pytest

from telemsg import netutil


def _can_bind_local_port() -> bool:
    """某些受限环境（沙箱、部分 CI）不允许 bind，这类用例应跳过而不是失败。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _can_bind_local_port(), reason="当前环境不允许绑定本地端口，跳过端口相关用例"
)


@pytest.fixture()
def held_port():
    """占住一个端口，模拟「被别的程序占用」。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    yield port
    sock.close()


def test_is_port_free_reports_false_for_held_port(held_port: int) -> None:
    assert netutil.is_port_free("127.0.0.1", held_port) is False


def test_pick_port_returns_preferred_when_free() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    port, changed = netutil.pick_port("127.0.0.1", free_port)
    assert port == free_port
    assert changed is False


def test_pick_port_falls_back_to_next_free_port(held_port: int) -> None:
    port, changed = netutil.pick_port("127.0.0.1", held_port)
    assert changed is True
    assert port != held_port
    assert netutil.is_port_free("127.0.0.1", port)


def test_pick_port_skips_consecutive_busy_ports() -> None:
    socks = []
    base = None
    try:
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            socks.append(sock)
            port = sock.getsockname()[1]
            base = port if base is None or port < base else base
        port, changed = netutil.pick_port("127.0.0.1", base, attempts=50)
        assert changed is True
        assert port not in {s.getsockname()[1] for s in socks}
    finally:
        for sock in socks:
            sock.close()


def test_pick_port_falls_back_to_ephemeral_when_all_busy(held_port: int) -> None:
    """attempts=1 且唯一候选被占用时，应该交给系统分配，而不是直接报错。"""
    port, changed = netutil.pick_port("127.0.0.1", held_port, attempts=1)
    assert changed is True
    assert netutil.is_port_free("127.0.0.1", port)


@pytest.mark.parametrize("errno_value", [48, 98, 10048])
def test_is_address_in_use_recognises_errno(errno_value: int) -> None:
    assert netutil.is_address_in_use(OSError(errno_value, "boom")) is True


def test_is_address_in_use_recognises_message() -> None:
    # uvicorn 有时会把 errno 丢掉，只剩文本
    assert netutil.is_address_in_use(RuntimeError("address already in use")) is True
    assert netutil.is_address_in_use(OSError(13, "permission denied")) is False
    assert netutil.is_address_in_use(RuntimeError("something else")) is False


def test_port_with_time_wait_is_reported_free() -> None:
    """回归：重启后端口上常有 TIME_WAIT 残留。

    如果不按 uvicorn 的方式设 SO_REUSEADDR，探测会把这种情况判成「被占用」，
    于是每次重启端口都往前漂一格（8765 → 8766 → 8767 …）。
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    conn, _ = server.accept()
    # 由服务端先关，才会在服务端这一侧留下 TIME_WAIT
    conn.close()
    client.close()
    server.close()

    # TIME_WAIT 可能还没生成、也可能已经消散，两种情况都应该判为「可用」
    assert netutil.is_port_free("127.0.0.1", port) is True


def test_server_socket_options_match_uvicorn_behaviour() -> None:
    import os

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        netutil.apply_server_socket_options(sock)
        if os.name == "nt":
            value = sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE)
        else:
            value = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        # BSD 系读回来的是选项原始值（macOS 上 SO_REUSEADDR 为 4），只断言「已开启」
        assert value != 0
    finally:
        sock.close()
