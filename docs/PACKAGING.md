# 打包方案

目标：**在 macOS 上直接产出能在 Windows 10 上双击运行的绿色包，目标机器不需要装 Python。**

## 一、为什么不能用 PyInstaller

这是整个方案里最关键的一条约束：

> PyInstaller **不能交叉编译**。在 macOS 上运行 PyInstaller，只会产出 macOS 的可执行文件。
> 想要 `.exe`，就必须在 Windows 上跑 PyInstaller。

如果坚持用 PyInstaller 出 Windows 包，就得准备一台 Windows 机器、或 Windows 虚拟机、
或 Wine——都是「先有鸡还是先有蛋」的麻烦。

## 二、方案总览

| | Windows 目标 | macOS 目标 |
| --- | --- | --- |
| 技术路线 | 官方 embeddable 运行时 + 交叉下载 wheel | PyInstaller（onedir） |
| 能否在 Mac 上构建 | ✅ 可以在任意系统上构建 | ❌ 必须在 macOS 上构建 |
| 目标机是否需要 Python | 不需要 | 不需要 |
| 产物 | `TeleMsgCreator-1.0.0-win64.zip`（~23 MB） | `TeleMsgCreator-1.0.0-macos-arm64.zip` |
| 入口 | 双击 `start.bat` | 双击 `TeleMsgCreator` |

Windows 这条路线之所以可行，是因为它**不需要编译任何东西**：Python 官方已经提供了
Windows 专用的免安装运行时，而依赖也都有现成的 Windows 版 wheel。
我们要做的只是「下载 + 摆放 + 打 zip」，这一步在哪个操作系统上做都一样。

```
        python-3.12.10-embed-amd64.zip          ← python.org 官方运行时
                     │
        pip --platform win_amd64                 ← 在 Mac 上拉取 Windows 版 wheel
                     │
        src/telemsg + 启动脚本 + .env 模板
                     │
                     ▼
        TeleMsgCreator-1.0.0-win64.zip           ← 拷到 Windows 解压即用
```

## 三、快速开始

```bash
# Windows 绿色版（在 Mac 上直接执行，无需 Windows）
make package-win

# macOS 版（需先 pip install pyinstaller）
make package-mac

# 两者都出
make package
```

等价的手工命令：

```bash
python scripts/build_release.py --target windows
python scripts/build_release.py --target windows --python-version 3.12.9   # 指定运行时版本
python scripts/build_release.py --target windows --no-pillow               # 省几 MB
python scripts/build_release.py --target windows --no-zip                  # 只留目录
```

产物在 `dist/`（已在 `.gitignore` 里）。

## 四、Windows 包的结构

```
TeleMsgCreator-1.0.0-win64/
├── python.exe / pythonw.exe / python312.dll     ← 自带运行时
├── python312.zip                                ← 标准库（注意是 .pyc 形式）
├── python312._pth                               ← 决定 sys.path 的关键文件
├── Lib/site-packages/
│   ├── telemsg/                                 ← 本项目源码（含 Web 模板与静态资源）
│   ├── fastapi/ uvicorn/ httpx/ pydantic/ …     ← 依赖
│   └── pydantic_core/_pydantic_core.cp312-win_amd64.pyd
├── start.bat         启动并自动打开浏览器
├── stop.bat          停止后台进程
├── doctor.bat        环境自检
├── edit-config.bat   用记事本编辑 .env
├── tools/stop.ps1
├── data/             运行时数据（数据库 / 上传的图片 / 日志）
├── .env.example
├── README-FIRST.txt  中文使用说明（UTF-8 BOM，双击记事本不乱码）
├── version.txt       版本、构建机、git 版本
└── manifest.json     全文件清单与摘要
```

## 五、踩过的坑（这一节比命令更重要）

### 1. `._pth` 开启隔离模式，必须手动加 site-packages

只要 `python312._pth` 存在，Python 就进入 **isolated mode**：`sys.path` 完全由它决定，
`PYTHONPATH` 被忽略。这其实是好事——打包版不会受目标机器环境干扰。
但代价是必须手动把 `Lib\site-packages` 写进去，并且把 `#import site` 的注释打开，
否则第三方包一律 import 失败。

### 2. `uvicorn[standard]` 在 Windows 上装不了

`uvicorn[standard]` 依赖 **uvloop**，而 uvloop 明确不支持 Windows。
症状是 pip 报 `ResolutionImpossible`，报错信息里只有一长串版本号，很难一眼看出原因。

所以 `pyproject.toml` 里专门有一个给打包用的依赖集：

```toml
[project.optional-dependencies]
web    = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]   # 开发/服务器用，要性能
bundle = ["fastapi>=0.110", "uvicorn>=0.29"]             # 打包用，避开 uvloop
```

打包脚本读的是 `bundle` 而不是 `web`。代价是打包版用的是纯 Python 的 h11 实现，
对本地单人使用的编辑器来说完全够用。

### 3. 必须 `--only-binary=:all:`

交叉安装时如果不加这个约束，pip 遇到没有 Windows wheel 的包会**在 Mac 上现场编译**，
产出的是 macOS 的 `.so`——放进包里，到 Windows 上必然崩，而且报错很难定位。

同理还必须显式指定 `--platform win_amd64 --python-version 3.12 --abi cp312`，
否则 pip 会按当前解释器（我们这里是 3.13）去解析，装出 cp313 的包。

打包脚本最后有一道**产物校验**专门拦这个：包里必须存在 `*.pyd`，
且**不能**存在任何 `*.so` / `*.dylib`。

### 4. `--no-compile`

不加这个参数，pip 会用**构建机的 Python 版本**（3.13）生成 `__pycache__/*.pyc`，
在 Windows 的 3.12 上属于无效字节码，白白占空间。

### 5. zip 内的文件名必须是 ASCII

Windows 自带的「解压全部」对 zip 里 UTF-8 文件名的支持一直不稳定，
中文名经常解出乱码。所以 Windows 包里的**文件名与批处理内容全部保持 ASCII**，
中文只出现在 `.txt` 说明文件的**内容**里，并且加上 UTF-8 BOM，记事本双击打开不乱码。

界面本身是中文的——那部分由 Python 在运行时输出，不受影响。

### 6. 标准库是 `.pyc` 而不是 `.py`

embeddable 里的 `python312.zip` 只放编译后的 `.pyc`（省体积）。
用「检查有没有 `asyncio/__init__.py`」的方式验证会误判成缺失，
校验时必须按 `.pyc` 找。

### 7. 公司代理导致的 TLS 证书校验失败

这台构建机所在网络做了 TLS 中间人：Python 的 `urllib` 走系统 CA 包会失败，
而 `curl` 在 macOS 上走钥匙串、在 Windows 10 上走 Schannel，通常正常。

打包脚本因此做了两层兜底：

* 下载优先用 `curl`，失败再退回 `urllib`；两者都失败时，报错信息里会给出
  `SSL_CERT_FILE` 与 `--insecure` 两个选项；
* pip 遇到 `CERTIFICATE_VERIFY_FAILED` 会自动改用 `--trusted-host` 重试一次，
  并在控制台明确提示「已降级」，不会悄悄绕过校验。

## 六、怎么验证产物是对的

**能在 Mac 上自动验证的**（打包脚本已内置）：

* `python.exe` / `python312.dll` / `python312._pth` 齐全；
* `Lib\site-packages` 已写入 `._pth`；
* 依赖目录齐全（fastapi / uvicorn / pydantic / httpx / typer / rich）；
* 存在 `.pyd` 且**不存在** `.so` / `.dylib`；
* 启动脚本与说明文件齐全；
* zip 内路径全为 ASCII；
* 包内 `telemsg` 源码与仓库逐字节一致；
* 所有 `.py` 可语法编译；
* 标准库 `.pyc` 覆盖了代码用到的模块。

**必须在 Windows 上做的**（打包脚本无法替代）：

```bat
doctor.bat      :: 环境自检：运行时 / 依赖 / 目录权限 / Token / 网络
start.bat       :: 真正跑一次
```

在没有 Windows 机器的情况下，`doctor.bat` 是排查「双击没反应」最快的手段。

## 七、常见问题

**Q：能不能做成单文件 exe？**
可以，但必须在 Windows 上用 PyInstaller。而且单文件模式每次启动都要把内容解压到临时目录，
启动慢、还容易被杀毒软件误报。绿色文件夹更稳，也方便用户看到 `data/` 里存了什么。

**Q：能不能支持 32 位 Windows？**
把 `embed-amd64` 换成 `embed-win32`，同时把 pip 的 `--platform` 改成 `win32` 即可。
脚本里这两处都是写死的常量，按需改一行。

**Q：为什么会被杀毒软件误报？**
因为包里有一个未签名的 `python.exe`。想彻底解决需要代码签名证书（Windows 上是 EV 证书）。
临时办法是把目录加入白名单，`README-FIRST.txt` 里已经写明了这一点。

**Q：分发时要注意什么？**
**不要把 `.env` 和 `data/` 一起发出去**——那等于把机器人账号和聊天数据一起给了别人。

**Q：macOS 包双击提示「无法验证开发者」？**
因为没做 Apple 签名。右键 →「打开」，或执行 `xattr -dr com.apple.quarantine ./TeleMsgCreator`。

## 八、接 CI 的话

Windows 包可以在 **Linux/macOS 的 CI** 上直接构建（这正是本方案的价值）。
`macOS` 包则需要 `macos-latest` 的 runner。

```yaml
- run: python scripts/build_release.py --target windows
- uses: actions/upload-artifact@v4
  with:
    name: TeleMsgCreator-win64
    path: dist/TeleMsgCreator-*-win64.zip
```

想要完全可复现的构建，再用 `--python-version` 把运行时版本钉死。
