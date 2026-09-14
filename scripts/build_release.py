#!/usr/bin/env python3
"""兼容入口：真正的实现在 ``telemsg.release``。

保留这个薄封装是为了不破坏已有文档、CI 配置和肌肉记忆。
新代码请用 ``telemsg build`` 或 ``python -m telemsg.release``。

这个脚本刻意**不依赖任何第三方库**：``telemsg/__init__.py`` 是惰性的，
``telemsg.release`` 只用标准库，所以用系统自带的 python3 也能直接跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from telemsg.release import BuildError, main  # noqa: E402
except ImportError as exc:  # pragma: no cover - 环境异常兜底
    sys.stderr.write(
        f"✖ 无法加载打包器：{exc}\n"
        f"  当前解释器：{sys.executable}（Python {sys.version.split()[0]}）\n"
        "  打包器只依赖标准库，出现这个错误通常说明源码不完整。\n"
        "  可以改用项目虚拟环境：.venv/bin/python scripts/build_release.py\n"
    )
    raise SystemExit(1) from exc

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n已取消\n")
        raise SystemExit(130) from None
    except BuildError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
