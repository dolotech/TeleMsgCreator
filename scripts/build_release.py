#!/usr/bin/env python3
"""兼容入口：真正的实现在 ``telemsg.release``。

保留这个薄封装是为了不破坏已有文档、CI 配置和肌肉记忆。
新代码请用 ``telemsg build`` 或 ``python -m telemsg.release``。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telemsg.release import BuildError, main  # noqa: E402

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
