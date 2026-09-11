"""PyInstaller 入口：双击后直接启动 Web 编辑器。

PyInstaller 会把这里当作程序主入口，所以不需要用户理解子命令。
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()  # 防止打包后多进程自我复制
    from telemsg.cli import main as cli_main

    if len(sys.argv) <= 1:
        sys.argv = [sys.argv[0], "serve", "--open"]
    cli_main()


if __name__ == "__main__":
    main()
