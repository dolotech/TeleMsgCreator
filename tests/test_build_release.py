"""打包脚本的可测逻辑。

真正下载 wheel、组装发行包的部分需要网络，这里只覆盖纯函数与命令拼装，
保证「参数拼错导致打出一个跑不起来的包」这类问题能被 CI 挡住。
"""

from __future__ import annotations

import platform
import subprocess
import zipfile
from pathlib import Path

import pytest

from telemsg import release

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def builder():
    """被测模块。保留这个 fixture 是为了让各用例显式表达「依赖打包器」。"""
    return release


# ------------------------------------------------------------------ _pth
def test_patch_pth_adds_site_packages_and_site_import(builder, tmp_path: Path) -> None:
    pth = tmp_path / "python312._pth"
    pth.write_text("python312.zip\n.\n#import site\n", encoding="utf-8")
    builder.patch_pth(pth)
    text = pth.read_text(encoding="utf-8")
    assert "Lib\\site-packages" in text
    assert "import site" in text
    assert "#import site" not in text  # 必须真的生效，而不是留在注释里


def test_patch_pth_is_idempotent(builder, tmp_path: Path) -> None:
    pth = tmp_path / "python312._pth"
    pth.write_text("python312.zip\n.\n", encoding="utf-8")
    builder.patch_pth(pth)
    once = pth.read_text(encoding="utf-8")
    builder.patch_pth(pth)
    assert pth.read_text(encoding="utf-8") == once
    assert once.count("Lib\\site-packages") == 1


def test_patch_pth_handles_missing_file(builder, tmp_path: Path) -> None:
    pth = tmp_path / "python312._pth"
    builder.patch_pth(pth)
    assert "Lib\\site-packages" in pth.read_text(encoding="utf-8")


# ------------------------------------------------------------------ BOM
def test_ensure_utf8_bom_added_once(builder, tmp_path: Path) -> None:
    target = tmp_path / "README-FIRST.txt"
    target.write_text("中文说明", encoding="utf-8")
    builder.ensure_utf8_bom(target)
    builder.ensure_utf8_bom(target)
    data = target.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\xef\xbb\xbf") == 1
    assert data.decode("utf-8-sig") == "中文说明"


# -------------------------------------------------------------- 项目元信息
def test_project_version_matches_pyproject(builder) -> None:
    version = builder.project_version()
    assert version.count(".") == 2, version


def test_project_dependencies_avoid_uvloop(builder) -> None:
    """打包依赖里绝不能出现 uvicorn[standard]——它的 uvloop 不支持 Windows。"""
    deps = builder.project_dependencies()
    joined = " ".join(deps).lower()
    assert "uvicorn[" not in joined, "uvicorn 带了 extras，交叉安装 Windows wheel 会解析失败"
    assert "uvicorn>=" in joined
    for required in ("fastapi", "httpx", "pydantic", "typer", "rich"):
        assert required in joined, f"缺少运行时依赖 {required}"


def test_project_dependencies_are_unique(builder) -> None:
    deps = builder.project_dependencies()
    keys = [d.split(">=")[0].split("<")[0].strip().lower() for d in deps]
    assert len(keys) == len(set(keys)), f"依赖有重复：{keys}"


# --------------------------------------------------------------- pip 命令
def test_cross_install_builds_windows_command(builder, tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    target = tmp_path / "site-packages"
    builder.cross_install_windows(["httpx>=0.27"], target, python_version="3.12.10")

    cmd = captured["cmd"]
    assert "--platform" in cmd and cmd[cmd.index("--platform") + 1] == "win_amd64"
    assert cmd[cmd.index("--python-version") + 1] == "3.12"
    assert cmd[cmd.index("--abi") + 1] == "cp312"
    assert cmd[cmd.index("--implementation") + 1] == "cp"
    assert "--only-binary=:all:" in cmd, "必须只接受预编译 wheel，否则会在 Mac 上编译出错误二进制"
    assert "--no-compile" in cmd, "不该用构建机的 Python 版本生成字节码"
    assert str(target) in cmd


def test_cross_install_retries_with_trusted_host_on_tls_error(
    builder, tmp_path: Path, monkeypatch
) -> None:
    """公司代理做 TLS 中间人时，应自动降级重试而不是直接失败。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="SSLError: CERTIFICATE_VERIFY_FAILED")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    builder.cross_install_windows(["httpx>=0.27"], tmp_path / "sp", python_version="3.12.10")
    assert len(calls) == 2
    assert "--trusted-host" not in calls[0]
    assert "--trusted-host" in calls[1]


def test_cross_install_raises_with_hint_on_unresolvable(builder, tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: ResolutionImpossible")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    with pytest.raises(builder.BuildError) as excinfo:
        builder.cross_install_windows(["x"], tmp_path / "sp", python_version="3.12.4")
    assert "uvloop" in str(excinfo.value)


# ------------------------------------------------------------------- 打包
def test_zip_dir_uses_ascii_arcnames(builder, tmp_path: Path) -> None:
    source = tmp_path / "TeleMsgCreator-1.0.0-win64"
    (source / "Lib" / "site-packages").mkdir(parents=True)
    (source / "python.exe").write_bytes(b"MZ")
    (source / "Lib" / "site-packages" / "x.py").write_text("ok", encoding="utf-8")

    archive = builder.zip_dir(source, tmp_path / "out.zip")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert names == [
        "TeleMsgCreator-1.0.0-win64/Lib/site-packages/x.py",
        "TeleMsgCreator-1.0.0-win64/python.exe",
    ]
    assert all(name.isascii() for name in names), "Windows 自带解压对非 ASCII 文件名支持不稳定"


def test_verify_windows_package_flags_empty_dir(builder, tmp_path: Path) -> None:
    problems = builder.verify_windows_package(tmp_path)
    assert problems
    assert any("python.exe" in p for p in problems)
    assert any("_pth" in p or "pth" in p for p in problems)


def test_verify_windows_package_accepts_minimal_valid_layout(builder, tmp_path: Path) -> None:
    stage = tmp_path / "pkg"
    site = stage / "Lib" / "site-packages"
    (site / "telemsg").mkdir(parents=True)
    (site / "telemsg" / "cli.py").write_text("", encoding="utf-8")
    for dep in ("fastapi", "uvicorn", "pydantic", "httpx", "typer", "rich"):
        (site / dep).mkdir()
    (site / "pydantic_core" / "x.cp312-win_amd64.pyd").parent.mkdir(parents=True, exist_ok=True)
    (site / "pydantic_core" / "x.cp312-win_amd64.pyd").write_bytes(b"\x00")
    (stage / "python.exe").write_bytes(b"MZ")
    (stage / "python312._pth").write_text("python312.zip\n.\nLib\\site-packages\n", encoding="utf-8")
    for name in ("start.bat", "stop.bat", "doctor.bat", "README-FIRST.txt"):
        (stage / name).write_text("", encoding="utf-8")

    assert builder.verify_windows_package(stage) == []


def test_verify_windows_package_rejects_foreign_binaries(builder, tmp_path: Path) -> None:
    """防止误装成 macOS/Linux 版 wheel —— 那在 Windows 上必然启动失败。"""
    stage = tmp_path / "pkg"
    site = stage / "Lib" / "site-packages"
    (site / "telemsg").mkdir(parents=True)
    (site / "telemsg" / "cli.py").write_text("", encoding="utf-8")
    for dep in ("fastapi", "uvicorn", "pydantic", "httpx", "typer", "rich"):
        (site / dep).mkdir()
    (site / "pydantic_core").mkdir()
    (site / "pydantic_core" / "_pydantic_core.cpython-312-darwin.so").write_bytes(b"\x00")
    (stage / "python.exe").write_bytes(b"MZ")
    (stage / "python312._pth").write_text("Lib\\site-packages\n", encoding="utf-8")
    for name in ("start.bat", "stop.bat", "doctor.bat", "README-FIRST.txt"):
        (stage / name).write_text("", encoding="utf-8")

    problems = builder.verify_windows_package(stage)
    assert any("非 Windows 二进制" in p for p in problems)
    assert any(".pyd" in p for p in problems)


def test_fallback_deps_are_used_when_pyproject_unreadable(builder, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(builder, "ROOT", tmp_path)  # 没有 pyproject.toml
    assert builder.project_dependencies() == builder.FALLBACK_DEPS


def test_windows_package_ships_ascii_launchers(builder) -> None:
    """批处理文件名保持 ASCII，避免 Windows 自带解压把中文名解坏。"""
    template_dir = builder.PACKAGING / "windows"
    names = [p.name for p in template_dir.rglob("*") if p.is_file()]
    assert names, "打包模板缺失"
    non_ascii = [n for n in names if not n.isascii()]
    assert not non_ascii, f"Windows 包的模板文件名必须全是 ASCII：{non_ascii}"
    assert "start.bat" in names and "doctor.bat" in names


# ------------------------------------------------------------ 项目根目录定位
def test_find_project_root_locates_repo(builder) -> None:
    root = builder.find_project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "packaging" / "windows").is_dir()
    assert root == ROOT


def test_find_project_root_uses_env_override(builder, tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "src-tree"
    (fake / "packaging").mkdir(parents=True)
    monkeypatch.setenv("TELEMSG_SOURCE_ROOT", str(fake))
    assert builder.find_project_root() == fake.resolve()


def test_find_project_root_rejects_env_without_packaging(builder, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TELEMSG_SOURCE_ROOT", str(tmp_path))
    with pytest.raises(builder.BuildError) as excinfo:
        builder.find_project_root()
    assert "packaging" in str(excinfo.value)


def test_find_project_root_gives_readable_error(builder, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TELEMSG_SOURCE_ROOT", raising=False)
    with pytest.raises(builder.BuildError) as excinfo:
        builder.find_project_root(tmp_path / "nowhere")
    message = str(excinfo.value)
    assert "源码仓库" in message and "TELEMSG_SOURCE_ROOT" in message


# ---------------------------------------------------------------- 目标解析
def test_resolve_target_expands_current(builder) -> None:
    assert builder.resolve_target("current") == (
        "macos" if platform.system() == "Darwin" else "windows"
    )
    assert builder.resolve_target("all") == "all"
    assert builder.resolve_target("windows") == "windows"


# ------------------------------------------------------------------ 入口
def test_legacy_script_is_a_thin_shim() -> None:
    """旧文档与 CI 里写的 scripts/build_release.py 必须继续可用。"""
    shim = ROOT / "scripts" / "build_release.py"
    assert shim.is_file()
    source = shim.read_text(encoding="utf-8")
    assert "from telemsg.release import" in source
    # 真正的实现只有一份，薄封装里不该再出现构建逻辑
    assert "def build_windows" not in source
    assert len(source.splitlines()) < 40, "薄封装不该重新长出实现"


def test_build_targets_is_the_single_entry_for_both_cli_paths(builder) -> None:
    import inspect

    assert callable(builder.build_targets)
    signature = inspect.signature(builder.build_targets)
    for name in ("python_version", "out_dir", "make_zip", "include_pillow", "insecure"):
        assert name in signature.parameters, f"build_targets 缺少参数 {name}"


def test_cli_exposes_build_command() -> None:
    from typer.testing import CliRunner

    from telemsg.cli import app

    result = CliRunner().invoke(app, ["build", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.stdout
