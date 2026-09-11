"""环境自检。

打包成绿色版发给别人之后，「跑不起来」是最常见的问题，而用户能提供的信息
往往只有「双击没反应」。``telemsg doctor`` 把该查的一次性列出来，并给出
下一步该怎么做。
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

#: 运行时必需的依赖（模块名, 展示名, 是否必需）
REQUIRED_MODULES: tuple[tuple[str, str, bool], ...] = (
    ("pydantic", "pydantic", True),
    ("httpx", "httpx", True),
    ("typer", "typer", True),
    ("rich", "rich", True),
    ("apscheduler", "APScheduler", True),
    ("fastapi", "fastapi", False),
    ("uvicorn", "uvicorn", False),
    ("jinja2", "Jinja2", False),
    ("multipart", "python-multipart", False),
    ("PIL", "Pillow（图片尺寸校验）", False),
)


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str | None = None
    fatal: bool = False


def run_checks(settings: Settings, *, online: bool = True) -> list[Check]:
    checks: list[Check] = []
    checks.append(_python_check())
    checks.append(_frozen_check())
    checks.extend(_dependency_checks())
    checks.append(_writable_check("数据目录", Path(settings.db_path).parent))
    checks.append(_writable_check("媒体上传目录", Path(settings.upload_dir)))
    checks.append(_env_file_check(settings))
    checks.append(_token_check(settings))
    checks.append(_web_check())
    if online:
        checks.append(_network_check(settings))
    return checks


def _python_check() -> Check:
    version = ".".join(str(v) for v in sys.version_info[:3])
    detail = f"{platform.python_implementation()} {version} · {platform.system()} {platform.machine()}"
    ok = sys.version_info >= (3, 10)
    return Check(
        "Python 运行时",
        ok,
        detail,
        hint=None if ok else "需要 Python 3.10 及以上；官方打包版已内置运行时，出现此项异常说明包被破坏",
        fatal=not ok,
    )


def _frozen_check() -> Check:
    frozen = bool(getattr(sys, "frozen", False))
    # 绿色版是通过 embeddable 运行时启动的，靠 sys.prefix 是否等于可执行文件目录来近似判断
    portable = Path(sys.executable).parent == Path(sys.prefix)
    mode = "打包版（绿色免安装）" if frozen or portable else "开发环境（源码运行）"
    return Check("运行方式", True, mode)


def _dependency_checks() -> list[Check]:
    checks: list[Check] = []
    for module, label, required in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - 缺依赖不该让自检本身崩掉
            checks.append(
                Check(
                    f"依赖 {label}",
                    False,
                    f"导入失败：{type(exc).__name__}: {exc}",
                    hint="官方打包版请重新解压完整压缩包；源码运行请执行 pip install -e \".[web,media]\"",
                    fatal=required,
                )
            )
        else:
            checks.append(Check(f"依赖 {label}", True, "OK"))
    return checks


def _writable_check(name: str, path: Path) -> Check:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".telemsg-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            name,
            False,
            f"{target} 不可写：{exc}",
            hint="把整个目录换到有写权限的位置（例如桌面或 D 盘），不要放在 Program Files 下",
            fatal=True,
        )
    return Check(name, True, str(target))


def _env_file_check(settings: Settings) -> Check:
    path = Path(settings.env_file)
    if path.exists():
        mode = oct(path.stat().st_mode & 0o777)
        return Check("配置文件", True, f"{path}（权限 {mode}）")
    return Check(
        "配置文件",
        True,
        f"{path} 尚不存在，首次在界面里保存设置时会自动创建",
    )


def _token_check(settings: Settings) -> Check:
    from .logging_setup import mask_token

    token = settings.resolved_token
    if not token:
        return Check(
            "Bot Token",
            False,
            "未配置",
            hint="打开网页后按引导填写，或编辑 .env 里的 TELEMSG_BOT_TOKEN",
        )
    source = "环境变量" if os.environ.get("TELEMSG_BOT_TOKEN") else str(settings.env_file)
    return Check("Bot Token", True, f"已配置 {mask_token(token)}（来自 {source}）")


def _web_check() -> Check:
    try:
        import uvicorn  # noqa: F401
        from fastapi import FastAPI  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return Check(
            "Web 编辑器",
            False,
            f"不可用：{exc}",
            hint="打包版请重新解压；源码运行请 pip install -e \".[web]\"",
        )
    return Check("Web 编辑器", True, "可用")


def _network_check(settings: Settings) -> Check:
    import httpx

    url = f"{settings.api_base}/"
    try:
        response = httpx.get(url, timeout=8.0, follow_redirects=True, proxy=settings.proxy or None)
    except Exception as exc:  # noqa: BLE001
        return Check(
            "连接 Telegram",
            False,
            f"{url} 不可达：{type(exc).__name__}",
            hint="检查网络、公司代理或防火墙；需要代理时在 .env 里设置 TELEMSG_PROXY",
            fatal=True,
        )
    return Check("连接 Telegram", True, f"HTTP {response.status_code}")


def worst_exit_code(checks: list[Check]) -> int:
    return 1 if any(c.fatal or not c.ok for c in checks) else 0
