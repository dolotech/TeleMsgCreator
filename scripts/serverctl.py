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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "data" / "server.pid"
LOG_FILE = ROOT / "data" / "server.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _python() -> str:
    """优先用项目自带的虚拟环境。"""
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _health(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start(host: str, port: int, *, wait: float = 20.0) -> int:
    if (pid := _read_pid()) is not None:
        print(f"已经在运行（PID {pid}）→ http://{host}:{port}")
        return 0
    if _health(host, port):
        print(f"端口 {port} 上已有服务在响应 → http://{host}:{port}")
        return 0

    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    argv = [_python(), "-m", "telemsg", "serve", "--host", host, "--port", str(port)]
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
            print(f"✖ 进程已退出（code={proc.returncode}），请看日志：{LOG_FILE}", file=sys.stderr)
            return 1
        if _health(host, port):
            print(f"✔ 已启动（PID {proc.pid}）→ http://{host}:{port}")
            print(f"  日志：{LOG_FILE}")
            return 0
        time.sleep(0.4)
    print("✖ 启动超时，请查看日志：", LOG_FILE, file=sys.stderr)
    return 1


def stop() -> int:
    pid = _read_pid()
    if pid is None:
        PID_FILE.unlink(missing_ok=True)
        print("没有在运行（或 PID 文件已失效）")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"停止失败: {exc}", file=sys.stderr)
        return 1
    for _ in range(25):
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
        except OSError:
            PID_FILE.unlink(missing_ok=True)
            print(f"✔ 已停止（PID {pid}）")
            return 0
    print(f"进程 {pid} 没有在 5 秒内退出，尝试强制结束", file=sys.stderr)
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    return 0


def status(host: str, port: int) -> int:
    pid = _read_pid()
    alive = _health(host, port)
    if pid and alive:
        print(f"● 运行中（PID {pid}）→ http://{host}:{port}")
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
