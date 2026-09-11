#!/usr/bin/env python3
"""跨平台打包：在当前系统上产出可直接运行的发行包。

设计取舍
--------
PyInstaller **不能跨平台编译**——在 macOS 上跑 PyInstaller 只会产出 macOS 的
可执行文件。所以「在 Mac 上打 Windows 包」不能用 PyInstaller，改用官方
**embeddable 发行版 + 交叉下载 Windows wheel** 的方式组装绿色免安装包：

1. 下载 python.org 的 ``python-<ver>-embed-amd64.zip``（Windows 专用的免安装运行时）；
2. 用 ``pip download/install --platform win_amd64 --only-binary=:all:``
   在 Mac 上直接拉取 **Windows 版 wheel**（含 pydantic-core、Pillow 这类
   带 C/Rust 扩展的包），不需要 Wine、不需要 Windows 虚拟机；
3. 把本项目源码与启动脚本一起塞进去，打包成 zip。

在 Windows 上解压即用，**目标机器不需要安装 Python**。

macOS 目标则用 PyInstaller（必须在本机执行，这是 PyInstaller 的硬性限制）。

用法::

    python scripts/build_release.py --target windows
    python scripts/build_release.py --target macos
    python scripts/build_release.py --target all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import ssl
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build"
CACHE = BUILD / "cache"
DIST = ROOT / "dist"

#: 找不到 pyproject 时的兜底依赖列表（正常情况下以 pyproject 为准）
FALLBACK_DEPS = [
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "httpx>=0.27",
    "typer>=0.12",
    "rich>=13.7",
    "Jinja2>=3.1",
    "python-multipart>=0.0.9",
    "APScheduler>=3.10",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "Pillow>=10.2",
]

#: Windows embeddable 的候选版本，按顺序尝试（python.org 上的稳定版）
EMBED_VERSION_CANDIDATES = ("3.12.10", "3.12.9", "3.12.8", "3.12.7", "3.12.6")


class BuildError(RuntimeError):
    """打包过程中的可预期失败。"""


def log(step: str, message: str) -> None:
    print(f"\033[1;36m▸ {step}\033[0m {message}", flush=True)


def ok(message: str) -> None:
    print(f"  \033[32m✔\033[0m {message}", flush=True)


def warn(message: str) -> None:
    print(f"  \033[33m!\033[0m {message}", flush=True)


# --------------------------------------------------------------- 基础信息
def project_version() -> str:
    try:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def project_dependencies() -> list[str]:
    """从 pyproject 读取运行时依赖，避免打包脚本和项目配置各写一份。"""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 及以下
        warn("当前 Python 没有 tomllib，改用内置依赖列表")
        return list(FALLBACK_DEPS)
    try:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"读取 pyproject.toml 失败（{exc}），改用内置依赖列表")
        return list(FALLBACK_DEPS)
    deps = data.get("project", {}).get("dependencies") or FALLBACK_DEPS
    optional = data.get("project", {}).get("optional-dependencies", {})
    deps = list(deps)
    # 发行包用 bundle 而不是 web：web 里的 uvicorn[standard] 依赖 uvloop，Windows 装不了
    deps.extend(optional.get("bundle", optional.get("web", [])))
    deps.extend(optional.get("media", []))
    # 去掉重复（保留先后顺序）
    seen: set[str] = set()
    unique: list[str] = []
    for dep in deps:
        key = dep.split(">=")[0].split("==")[0].split("<")[0].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(dep)
    return unique


def git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


# ------------------------------------------------------------------ 下载
def _curl_download(url: str, dest: Path) -> bool:
    """用系统 curl 下载。

    某些公司网络用 TLS 中间人代理，Python 的 urllib 走系统 CA 包会校验失败，
    而 curl 在 macOS 上走钥匙串、在 Windows 10 上走 Schannel，通常能正常工作。
    """
    curl = shutil.which("curl")
    if not curl:
        return False
    result = subprocess.run(
        [curl, "-fsSL", "--retry", "2", "--connect-timeout", "30", "-o", str(dest), url],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def _urllib_download(url: str, dest: Path, *, insecure: bool) -> None:
    if insecure:
        context = ssl._create_unverified_context()  # noqa: S323 - 仅在企业代理场景下由用户显式开启
    else:
        context = ssl.create_default_context()
        bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if bundle and Path(bundle).exists():
            context.load_verify_locations(bundle)
    with urllib.request.urlopen(url, timeout=60, context=context) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def download(url: str, dest: Path, *, retries: int = 3, insecure: bool = False) -> Path:
    """带缓存的下载；已存在且非空则直接复用。

    优先用 curl（对代理环境更友好），失败再退回 Python 自带的 urllib。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        ok(f"使用缓存 {dest.name}（{dest.stat().st_size / 1048576:.1f} MB）")
        return dest
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            log("下载", f"{url}（第 {attempt} 次）")
            if _curl_download(url, dest):
                ok(f"{dest.name}（{dest.stat().st_size / 1048576:.1f} MB，curl）")
                return dest
            _urllib_download(url, dest, insecure=insecure)
            size = dest.stat().st_size
            if size == 0:
                raise BuildError(f"下载到空文件：{url}")
            ok(f"{dest.name}（{size / 1048576:.1f} MB）")
            return dest
        except (urllib.error.URLError, OSError, BuildError, ssl.SSLError) as exc:
            last_error = exc
            dest.unlink(missing_ok=True)
    hint = ""
    if isinstance(last_error, ssl.SSLError):
        hint = (
            "\n  看起来是 TLS 证书校验失败（常见于公司网络的中间人代理）。可任选其一："
            "\n    · 导出公司根证书后设置环境变量 SSL_CERT_FILE=/path/to/ca.pem"
            "\n    · 加 --insecure 跳过校验（仅建议在受信任的内网中使用）"
        )
    raise BuildError(f"下载失败：{url}\n  最后一次错误：{last_error}{hint}")


def fetch_embeddable(version: str | None, *, cache: Path, insecure: bool = False) -> tuple[Path, str]:
    """下载 Windows embeddable 运行时，返回 (zip 路径, 实际版本)。"""
    candidates = (version,) if version else EMBED_VERSION_CANDIDATES
    errors: list[str] = []
    for candidate in candidates:
        url = f"https://www.python.org/ftp/python/{candidate}/python-{candidate}-embed-amd64.zip"
        try:
            path = download(url, cache / f"python-{candidate}-embed-amd64.zip", insecure=insecure)
            return path, candidate
        except BuildError as exc:
            errors.append(f"  {candidate}: {str(exc).splitlines()[0]}")
    raise BuildError("没有可用的 embeddable 版本：\n" + "\n".join(errors))


# ------------------------------------------------------------- Windows 打包
@dataclass(slots=True)
class WindowsOptions:
    python_version: str | None = None
    out_dir: Path = DIST
    make_zip: bool = True
    clean: bool = True
    include_pillow: bool = True
    insecure: bool = False


def patch_pth(pth_file: Path, *, site_packages: str = "Lib\\site-packages") -> None:
    """让 embeddable 运行时能加载 ``Lib\\site-packages``。

    ``._pth`` 一旦存在就开启「隔离模式」：``sys.path`` 完全由它决定，
    ``PYTHONPATH`` 被忽略。这正好保证打包版不受目标机器环境影响，
    但必须手动把 site-packages 加进去。
    """
    lines = pth_file.read_text(encoding="utf-8").splitlines() if pth_file.exists() else []
    kept: list[str] = []
    has_site_packages = False
    imports_site = False
    for line in lines:
        stripped = line.strip()
        if stripped == site_packages:
            has_site_packages = True
            kept.append(site_packages)
            continue
        if stripped in {"import site", "#import site"}:
            imports_site = True
            kept.append("import site")
            continue
        kept.append(line)
    if not has_site_packages:
        kept.append(site_packages)
    if not imports_site:
        kept.append("import site")
    pth_file.write_text("\n".join(kept) + "\n", encoding="utf-8")


def cross_install_windows(
    packages: list[str], target: Path, *, python_version: str, insecure: bool = False
) -> None:
    """在当前系统上把 Windows 版 wheel 装进目标目录。"""
    minor = ".".join(python_version.split(".")[:2])  # 3.12
    abi = "cp" + minor.replace(".", "")  # cp312
    trusted_hosts = ["--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org"]
    base = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--platform",
        "win_amd64",
        "--python-version",
        minor,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "--only-binary=:all:",
        "--upgrade",
        "--no-compile",
        "--quiet",
    ]
    log("依赖", f"交叉安装 Windows wheel（cp{minor.replace('.', '')}-win_amd64）")
    cmd = [*base, *(trusted_hosts if insecure else []), *packages]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    # 公司网络常用 TLS 中间人代理，Python 的 CA 包校验不过；自动重试一次并说明原因
    if result.returncode != 0 and "CERTIFICATE_VERIFY_FAILED" in (result.stderr or "") and not insecure:
        warn("pip 遇到 TLS 证书校验失败（多半是公司代理），自动改用 --trusted-host 重试")
        cmd = [*base, *trusted_hosts, *packages]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        extra_hint = ""
        if "ResolutionImpossible" in detail:
            extra_hint = (
                "\n解析冲突：通常是某个依赖在 Windows 上没有 wheel（例如 uvloop 不支持 Windows）。"
                "\n请检查 pyproject.toml 里的 bundle 依赖集，必要时为打包单独指定依赖。"
            )
        raise BuildError(
            "交叉安装依赖失败。通常是某个包没有提供 Windows wheel。\n"
            "可尝试：升级 pip（pip install -U pip），或用 --python-version 换一个运行时版本。\n"
            f"命令：{' '.join(cmd[:8])} …{extra_hint}\n{detail[-2000:]}"
        )
    ok(f"已安装 {len(packages)} 个依赖到 {target.name}")


def copy_project_into(target_site_packages: Path) -> None:
    """把 telemsg 包复制进 site-packages（含 web 模板与静态资源）。"""
    src_pkg = SRC / "telemsg"
    if not src_pkg.is_dir():
        raise BuildError(f"找不到源码目录：{src_pkg}")
    dst_pkg = target_site_packages / "telemsg"
    if dst_pkg.exists():
        shutil.rmtree(dst_pkg)
    shutil.copytree(
        src_pkg,
        dst_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    ok(f"已复制 telemsg 源码（{sum(1 for _ in dst_pkg.rglob('*') if _.is_file())} 个文件）")


def copy_templates(stage: Path, *, target: str, version: str) -> None:
    """放入启动脚本、说明与示例配置。"""
    template_dir = PACKAGING / target
    if not template_dir.is_dir():
        raise BuildError(f"缺少打包模板目录：{template_dir}")
    count = 0
    for item in sorted(template_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(template_dir)
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        if dest.suffix.lower() == ".txt" and target == "windows":
            ensure_utf8_bom(dest)
        if dest.suffix == ".bat":
            # 保持可读，Windows 下由 cmd 执行，权限位无意义但无害
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
        count += 1
    (stage / "version.txt").write_text(
        f"TeleMsgCreator {version}\n"
        f"target: {target}\n"
        f"built-at: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n"
        f"git: {git_revision()}\n"
        f"built-on: {platform.system()} {platform.machine()} / Python {platform.python_version()}\n",
        encoding="utf-8",
    )
    ok(f"已复制 {count} 个模板文件（{target}）")


def ensure_utf8_bom(path: Path) -> None:
    """给 Windows 的 .txt 补上 UTF-8 BOM。

    记事本在旧版本里会把无 BOM 的 UTF-8 当成本地代码页，中文会变成乱码。
    加上 BOM 后双击打开就是正常中文。
    """
    data = path.read_bytes()
    if not data.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(b"\xef\xbb\xbf" + data)


def verify_windows_package(stage: Path) -> list[str]:
    """检查产物结构，尽早发现「下成 macOS 包」这类低级错误。"""
    problems: list[str] = []
    exe = stage / "python.exe"
    if not exe.exists():
        problems.append("缺少 python.exe（embeddable 运行时没解压成功）")

    site_packages = stage / "Lib" / "site-packages"
    if not (site_packages / "telemsg" / "cli.py").exists():
        problems.append("缺少 telemsg 源码包")
    for required in ("fastapi", "uvicorn", "pydantic", "httpx", "typer", "rich"):
        if not (site_packages / required).exists():
            problems.append(f"缺少依赖目录：{required}")

    # pydantic-core / Pillow 是二进制扩展，必须是 Windows 的 .pyd 而不是 .so/.dylib
    pyd = list(site_packages.rglob("*.pyd"))
    if not pyd:
        problems.append("没有任何 .pyd 文件——很可能误装了 macOS/Linux 版 wheel")
    foreign = list(site_packages.rglob("*.so")) + list(site_packages.rglob("*.dylib"))
    if foreign:
        problems.append(f"发现了非 Windows 二进制（{len(foreign)} 个 .so/.dylib），wheel 平台不正确")

    pth = [p for p in stage.glob("python*._pth")]
    if not pth:
        problems.append("缺少 pythonXY._pth（embeddable 的路径配置）")
    elif "Lib\\site-packages" not in pth[0].read_text(encoding="utf-8"):
        problems.append("._pth 里没有加入 Lib\\site-packages，打包版会 import 失败")

    for name in ("start.bat", "stop.bat", "doctor.bat", "README-FIRST.txt"):
        if not (stage / name).exists():
            problems.append(f"缺少 {name}")
    return problems


def build_windows(options: WindowsOptions) -> Path:
    version = project_version()
    stage = options.out_dir / f"TeleMsgCreator-{version}-win64"

    if options.clean and stage.exists():
        log("清理", f"删除旧的 {stage.name}")
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    embed_zip, embed_version = fetch_embeddable(
        options.python_version, cache=CACHE, insecure=options.insecure
    )
    log("解压", f"{embed_zip.name} → {stage.name}")
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(stage)
    ok(f"已解压 embeddable Python {embed_version}")

    pth_files = sorted(stage.glob("python*._pth"))
    if not pth_files:
        raise BuildError("解压后没找到 ._pth 文件，embeddable 包结构异常")
    patch_pth(pth_files[0])
    ok(f"已配置 {pth_files[0].name}（加入 Lib\\site-packages 与 import site）")

    deps = project_dependencies()
    if not options.include_pillow:
        deps = [d for d in deps if "pillow" not in d.lower()]
        warn("按要求跳过 Pillow：无法校验图片尺寸，其它功能不受影响")
    site_packages = stage / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    cross_install_windows(
        deps, site_packages, python_version=embed_version, insecure=options.insecure
    )
    copy_project_into(site_packages)
    copy_templates(stage, target="windows", version=version)

    (stage / "data").mkdir(exist_ok=True)

    log("校验", "检查产物结构")
    problems = verify_windows_package(stage)
    if problems:
        for problem in problems:
            print(f"    \033[31m✖\033[0m {problem}")
        raise BuildError("产物校验未通过，见上方问题列表")
    ok("结构完整：python.exe + .pyd 二进制 + site-packages + 启动脚本")

    # 清单必须在压缩前生成，否则不会进 zip
    write_manifest(stage, target="windows")
    ok("已生成 manifest.json（文件名 / 大小 / 摘要）")

    if options.make_zip:
        archive = zip_dir(stage, options.out_dir / f"{stage.name}.zip")
        ok(f"已生成 {archive.name}（{archive.stat().st_size / 1048576:.1f} MB）")
    return stage


# --------------------------------------------------------------- macOS 打包
def build_macos(*, out_dir: Path, make_zip: bool) -> Path:
    version = project_version()
    if platform.system() != "Darwin":
        raise BuildError("macOS 目标必须在 macOS 上构建——PyInstaller 不支持交叉编译")
    if shutil.which("pyinstaller") is None:
        raise BuildError(
            "未安装 PyInstaller。请先执行：\n"
            "  .venv/bin/python -m pip install pyinstaller\n"
            "（Windows 包不需要它，本脚本的 windows 目标可以在任意系统上构建）"
        )

    entry = PACKAGING / "macos" / "entry.py"
    if not entry.exists():
        raise BuildError(f"缺少入口脚本：{entry}")

    work = BUILD / "pyinstaller"
    log("PyInstaller", "构建 macOS 发行目录（onedir）")
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "TeleMsgCreator",
        "--distpath",
        str(out_dir),
        "--workpath",
        str(work / "build"),
        "--specpath",
        str(work / "spec"),
        "--collect-submodules",
        "telemsg",
        "--collect-data",
        "telemsg",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "apscheduler",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        str(entry),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BuildError(f"PyInstaller 失败：\n{(result.stderr or result.stdout)[-2000:]}")
    app_dir = out_dir / "TeleMsgCreator"
    if not app_dir.exists():
        raise BuildError(f"PyInstaller 没有产出预期目录：{app_dir}")

    for name in ("README-FIRST.txt", ".env.example"):
        source = PACKAGING / "windows" / name
        if source.exists():
            shutil.copy2(source, app_dir / name)

    if make_zip:
        archive = zip_dir(app_dir, out_dir / f"TeleMsgCreator-{version}-macos-{platform.machine()}.zip")
        ok(f"已生成 {archive.name}（{archive.stat().st_size / 1048576:.1f} MB）")
    return app_dir


# ------------------------------------------------------------------ 打包
def zip_dir(source: Path, dest: Path) -> Path:
    """把目录压成 zip，顶层保留目录名，便于解压后不散落一地。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(source.rglob("*")):
            if item.is_dir():
                continue
            # 一律使用 ASCII 相对路径：Windows 自带解压对 UTF-8 文件名的支持不稳定
            archive.write(item, arcname=f"{source.name}/{item.relative_to(source).as_posix()}")
    return dest


def write_manifest(stage: Path, *, target: str) -> Path:
    """生成清单（文件名 + 大小 + 摘要），方便校验分发包是否被篡改。"""
    entries = []
    for item in sorted(stage.rglob("*")):
        if item.is_file():
            digest = hashlib.sha256(item.read_bytes()).hexdigest()[:16]
            entries.append(
                {
                    "path": item.relative_to(stage).as_posix(),
                    "size": item.stat().st_size,
                    "sha256_16": digest,
                }
            )
    manifest = stage / "manifest.json"
    manifest.write_text(
        json.dumps({"target": target, "version": project_version(), "files": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在任意系统上产出可运行的发行包",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", choices=["windows", "macos", "all", "current"], default="current")
    parser.add_argument("--python-version", default=None, help="Windows embeddable 版本，默认自动挑选")
    parser.add_argument("--out", type=Path, default=DIST, help="产物目录（默认 dist/）")
    parser.add_argument("--no-zip", action="store_true", help="只留目录，不压 zip")
    parser.add_argument("--no-pillow", action="store_true", help="不打包 Pillow（省几 MB，放弃图片尺寸校验）")
    parser.add_argument("--keep-staging", action="store_true", help="保留中间目录（默认也保留，方便排查）")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="跳过 TLS 校验（公司网络有中间人代理时使用；仅建议在受信任内网里开启）",
    )
    args = parser.parse_args()

    target = args.target
    if target == "current":
        target = "macos" if platform.system() == "Darwin" else "windows"

    print(f"\033[1mTeleMsgCreator 打包\033[0m  版本 {project_version()}  目标 {target}")
    print(f"构建机：{platform.system()} {platform.machine()} · Python {platform.python_version()}\n")
    args.out.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    try:
        if target in {"windows", "all"}:
            build_windows(
                WindowsOptions(
                    python_version=args.python_version,
                    out_dir=args.out,
                    make_zip=not args.no_zip,
                    include_pillow=not args.no_pillow,
                    insecure=args.insecure,
                )
            )
        if target in {"macos", "all"}:
            build_macos(out_dir=args.out, make_zip=not args.no_zip)
    except BuildError as exc:
        print(f"\n\033[1;31m✖ 打包失败\033[0m\n{exc}", file=sys.stderr)
        return 1

    print("\n\033[1;32m打包完成\033[0m")
    for item in sorted(args.out.glob("*.zip")):
        print(f"  {item}  （{item.stat().st_size / 1048576:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
