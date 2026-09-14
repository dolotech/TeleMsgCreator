"""回归：导入 telemsg 不该拖出第三方依赖。

背景：打包器 telemsg.release 只用标准库，但 ``import telemsg`` 会执行
``__init__.py``。它曾经急切导入 TelegramClient/PostService，把 httpx、pydantic、
fastapi 整条链拉起来，于是在没装依赖的机器上跑打包脚本会直接报
``ModuleNotFoundError: No module named 'httpx'``。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

#: 运行时依赖：这些模块在被显式使用前都不该被加载
HEAVY_MODULES = ("httpx", "pydantic", "fastapi", "typer", "rich", "jinja2", "apscheduler")

_BLOCKER = """
import sys
BLOCKED = {blocked!r}

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + name)
        return None

sys.meta_path.insert(0, Blocker())
"""


def _run_snippet(body: str) -> subprocess.CompletedProcess[str]:
    code = _BLOCKER.format(blocked=HEAVY_MODULES) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )


def test_importing_release_brings_in_no_third_party_deps() -> None:
    """核心用例：模拟「机器上一个第三方库都没装」的情况。"""
    result = _run_snippet(
        """
        import telemsg.release
        leaked = sorted(m for m in BLOCKED if m in sys.modules)
        assert not leaked, "导入 telemsg.release 时被拖进来了：" + str(leaked)
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_importing_telemsg_package_is_cheap() -> None:
    """连 telemsg 本身也不该在导入时就拉起重依赖。"""
    result = _run_snippet(
        """
        import telemsg
        assert telemsg.__version__
        leaked = sorted(m for m in BLOCKED if m in sys.modules)
        assert not leaked, "导入 telemsg 时被拖进来了：" + str(leaked)
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr


def test_build_entry_point_survives_without_dependencies() -> None:
    """用户实际遇到的命令：在没装依赖的解释器里跑打包脚本。"""
    shim = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKER.format(blocked=HEAVY_MODULES) + textwrap.dedent(
            f"""
            import runpy, sys
            sys.argv = ["build_release.py", "--help"]
            runpy.run_path({str(shim)!r}, run_name="__main__")
            """
        )],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--target" in result.stdout


def test_lazy_attribute_access_still_works() -> None:
    """惰性化不能让 from telemsg import Draft 这种写法失效。"""
    import telemsg

    assert telemsg.Draft is not None
    assert telemsg.InlineButton is not None
    assert telemsg.TelemsgError is not None
    assert "Draft" in vars(telemsg)  # 访问过一次后应被缓存


def test_unknown_attribute_raises_attributeerror() -> None:
    import telemsg

    with pytest.raises(AttributeError, match="no attribute"):
        _ = telemsg.NotARealThing


def test_dunder_all_is_kept_in_sync_with_exports() -> None:
    """__all__ 必须与 _EXPORTS 一致，否则 from telemsg import * 会漏东西。"""
    import telemsg

    expected = set(telemsg._EXPORTS) | {"__version__"}
    assert set(telemsg.__all__) == expected
    assert len(telemsg.__all__) == len(set(telemsg.__all__)), "__all__ 有重复项"


def test_every_export_actually_resolves() -> None:
    import telemsg

    for name in telemsg._EXPORTS:
        assert getattr(telemsg, name) is not None, f"{name} 无法解析"
