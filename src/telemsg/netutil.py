"""端口可用性探测。

Web 编辑器默认监听 127.0.0.1:8765。端口被占用时有两种场景，必须区分开：

* 之前启动的实例还活着 —— 应该直接复用，而不是再起一个；
* 被别的程序占着 —— 应该换一个端口，而不是丢一串 ``address already in use`` 给用户。

注意：先探测再绑定天然存在竞态（probe 与 bind 之间端口可能被别人抢走）。
这里的策略是「探测选端口 + 绑定失败再退避重试」，调用方需要处理
:class:`PortUnavailable` 或捕获 ``OSError`` 后重试。
"""

from __future__ import annotations

import errno
import os
import socket

#: 换端口的最大尝试次数（8765 → 8774）
DEFAULT_ATTEMPTS = 10


class PortUnavailable(RuntimeError):
    """在允许的范围内找不到可用端口。"""


def apply_server_socket_options(sock: socket.socket) -> None:
    """按 uvicorn 的方式设置套接字选项。

    这一步至关重要，否则检测结果会和「服务能不能真的绑上」不一致：

    * POSIX：设 ``SO_REUSEADDR``。刚重启时端口上常残留 ``TIME_WAIT`` 连接，
      不设这个选项 bind 会报 EADDRINUSE，于是每次重启端口都往前漂一格；
    * Windows：``SO_REUSEADDR`` 的语义是「允许抢占别人的端口」，反而会漏判，
      所以 Windows 上用 ``SO_EXCLUSIVEADDRUSE`` 做独占绑定。
    """
    if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def is_port_free(host: str, port: int) -> bool:
    """端口现在能不能绑定（与真实启动时的行为保持一致）。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        apply_server_socket_options(sock)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, preferred: int, *, attempts: int = DEFAULT_ATTEMPTS) -> tuple[int, bool]:
    """返回 ``(可用端口, 是否换了端口)``。

    先试 ``preferred``，再依次试 ``preferred+1 … preferred+attempts-1``；
    都不可用时交给操作系统分配一个临时端口（bind 0），保证服务总能起来。
    """
    for offset in range(attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if is_port_free(host, candidate):
            return candidate, offset > 0

    ephemeral = _ephemeral_port(host)
    if ephemeral is None:  # pragma: no cover - 正常系统不会走到
        raise PortUnavailable(
            f"{host} 上从 {preferred} 开始连续 {attempts} 个端口都不可用，"
            "而且系统也分配不出临时端口"
        )
    return ephemeral, True


def _ephemeral_port(host: str) -> int | None:
    """让操作系统挑一个空闲端口。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        apply_server_socket_options(sock)
        try:
            sock.bind((host, 0))
        except OSError:  # pragma: no cover - 极端情况
            return None
        return int(sock.getsockname()[1])


def is_address_in_use(exc: BaseException) -> bool:
    """判断异常是不是「端口已被占用」。

    Windows 的 errno 是 10048，POSIX 是 48(macOS)/98(Linux)，而且 uvicorn 有时
    会把它包一层，所以同时看 errno 和错误文本。
    """
    if isinstance(exc, OSError) and exc.errno in {errno.EADDRINUSE, 10048, 48, 98}:
        return True
    text = str(exc).lower()
    return "address already in use" in text or "10048" in text
