#!/usr/bin/env python3
"""TeleMsgCreator 本地服务管理器。

用法::

    python scripts/serverctl.py start     # 后台启动（脱离终端，关掉终端也不会停）
    python scripts/serverctl.py stop      # 停止
    python scripts/serverctl.py status    # 查看状态
    python scripts/serverctl.py restart
    python scripts/serverctl.py logs -f   # 跟踪日志

为什么不用 ``nohup ... &``：在 macOS 上进程仍留在同一个进程组里，
父 shell 退出时容易被一起回收。这里用标准的 double-fork + setsid 真正脱离。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IS_WINDOWS = os.name == "nt"

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "data" / "server.pid"
PORT_FILE = ROOT / "data" / "server.port"
LOG_FILE = ROOT / "data" / "server.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _python() -> str:
    """优先用项目自带的虚拟环境。"""
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _process_alive(pid: int) -> bool:
    """判断进程是否存活。

    注意：Windows 上 ``os.kill(pid, 0)`` 并不是「探测」，它会直接
    TerminateProcess 把进程杀掉，所以必须走 Win32 API。
    """
    if IS_WINDOWS:
        import ctypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # WAIT_OBJECT_0 表示进程已退出
            return kernel32.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int) -> None:
    """结束进程：Windows 用 taskkill（连同子进程），其它平台发 SIGTERM。"""
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    os.kill(pid, signal.SIGTERM)


def _read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if _process_alive(pid) else None


def _health(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _is_port_free(host: str, port: int) -> bool:
    """本地实现端口探测，避免 serverctl 依赖 telemsg 包本身。

    套接字选项必须和 uvicorn 一致：POSIX 上开 SO_REUSEADDR，
    否则 TIME_WAIT 残留会被误判成「端口被占用」，导致每次重启都换端口。
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _read_port_file() -> tuple[str, int] | None:
    """读取 telemsg serve 写下的实际监听地址。"""
    try:
        host, _, port = PORT_FILE.read_text().strip().partition(":")
        return host, int(port)
    except (FileNotFoundError, ValueError):
        return None


def start(host: str, port: int, *, wait: float = 20.0) -> int:
    # 1) 我们自己起的实例还活着吗？必须以它**实际监听的端口**为准，
    #    不能拿命令行传入的 port 去假装，否则会报出一个没人监听的地址。
    if (pid := _read_pid()) is not None:
        recorded = _read_port_file()
        if recorded and _health(*recorded):
            print(f"已经在运行（PID {pid}）→ http://{recorded[0]}:{recorded[1]}")
            return 0
        if recorded is None and _health(host, port):
            print(f"已经在运行（PID {pid}）→ http://{host}:{port}")
            return 0
        # pid 还活着但服务没起来（可能正在启动），交给后面的等待逻辑
    elif _health(host, port):
        # 端口上有响应，但不是我们记录的进程：可能是别的实例或别的程序
        recorded = _read_port_file()
        if recorded == (host, port):
            print(f"端口 {port} 上已有 TeleMsgCreator 在运行 → http://{host}:{port}")
            return 0

    if not _is_port_free(host, port):
        print(f"提示：端口 {port} 已被占用，服务会自动改用其它端口")

    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    argv = [_python(), "-m", "telemsg", "serve", "--host", host, "--port", str(port)]
    PORT_FILE.unlink(missing_ok=True)  # 清掉上一次的残留，避免读到旧端口
    # start_new_session=True 让子进程拿到自己的会话与进程组，
    # 这样父 shell 退出时不会把它一起回收（等价于 setsid，且跨平台）。
    with open(LOG_FILE, "ab", buffering=0) as log:
        proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    PID_FILE.write_text(str(proc.pid))

    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            tail = _tail_log()
            print(f"✖ 进程已退出（code={proc.returncode}）", file=sys.stderr)
            if tail:
                print(f"  日志末尾：\n{tail}", file=sys.stderr)
            print(f"  完整日志：{LOG_FILE}", file=sys.stderr)
            return 1
        actual = _read_port_file()
        if actual and _health(*actual):
            shown = actual[1]
            suffix = "" if shown == port else f"（{port} 被占用，已自动改用 {shown}）"
            print(f"✔ 已启动（PID {proc.pid}）→ http://{actual[0]}:{shown}{suffix}")
            print(f"  日志：{LOG_FILE}")
            return 0
        time.sleep(0.4)
    print(f"✖ 启动超时，请看日志：{LOG_FILE}", file=sys.stderr)
    return 1


def _tail_log(lines: int = 15) -> str:
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(f"    {line}" for line in content[-lines:])


def stop() -> int:
    pid = _read_pid()
    if pid is None:
        PID_FILE.unlink(missing_ok=True)
        PORT_FILE.unlink(missing_ok=True)
        print("没有在运行（或 PID 文件已失效）")
        return 0
    try:
        _terminate(pid)
    except OSError as exc:
        print(f"停止失败: {exc}", file=sys.stderr)
        return 1
    for _ in range(25):
        time.sleep(0.2)
        if not _process_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            PORT_FILE.unlink(missing_ok=True)
            print(f"✔ 已停止（PID {pid}）")
            return 0
    print(f"进程 {pid} 没有在 5 秒内退出，尝试强制结束", file=sys.stderr)
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    PORT_FILE.unlink(missing_ok=True)
    return 0


def status(host: str, port: int) -> int:
    pid = _read_pid()
    recorded = _read_port_file()
    actual = recorded or (host, port)
    alive = _health(*actual)
    if pid and alive:
        print(f"● 运行中（PID {pid}）→ http://{actual[0]}:{actual[1]}")
        if recorded and recorded[1] != port:
            print(f"  （注意：不是默认端口 {port}，启动时它被占用了）")
        return 0
    if pid:
        print(f"● 进程 {pid} 存在，但 HTTP 没有响应，请看日志：{LOG_FILE}")
        return 1
    print("○ 未运行")
    return 1


def logs(follow: bool, lines: int) -> int:
    if not LOG_FILE.exists():
        print("还没有日志")
        return 0
    if follow:
        return subprocess.call(["tail", "-n", str(lines), "-f", str(LOG_FILE)])
    return subprocess.call(["tail", "-n", str(lines), str(LOG_FILE)])


def main() -> int:
    parser = argparse.ArgumentParser(description="TeleMsgCreator 服务管理")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "logs"])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-f", "--follow", action="store_true", help="logs 时持续跟踪")
    parser.add_argument("-n", "--lines", type=int, default=40, help="logs 显示行数")
    args = parser.parse_args()

    if args.action == "start":
        return start(args.host, args.port)
    if args.action == "stop":
        return stop()
    if args.action == "restart":
        stop()
        return start(args.host, args.port)
    if args.action == "logs":
        return logs(args.follow, args.lines)
    return status(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
