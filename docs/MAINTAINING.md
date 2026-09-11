# 维护者指南

面向接手这个仓库的人。目标：看完这一页能独立开发、测试、打包、发布。

> 相关文档：[提交与推送规范](../CONTRIBUTING.md) ·
> [打包方案细节](PACKAGING.md) · [Telegram 限制速查](TELEGRAM_LIMITS.md) ·
> [竞品调研](RESEARCH.md)

---

## 1. 这个项目在做什么

Telegram 的「图片/视频 + 文字 + 内联按钮」消息**只能由机器人发出**。现成的发帖机器人
普遍只给你「文字 + 链接」两种按钮、没有 API、频道数据还留在对方服务器上。

本项目是一个可自托管的替代品：一个核心库 + 一套 CLI + 一个 Web 编辑器。

```
用户输入 ─▶ Draft（领域模型）─▶ 校验 ─▶ 编译成 Bot API 请求 ─▶ 发送 ─▶ 审计入库
                 │                 │
                 │                 └─ 硬性限制（字符数 / 字节数 / 文件尺寸…）
                 └─ 同时驱动 Web 端的 1:1 预览
```

**唯一的真相来源是 Telegram Bot API 文档**（<https://core.telegram.org/bots/api>）。
所有硬性限制都集中在 `src/telemsg/limits.py`，改动前先核对文档。

---

## 2. 目录结构

```
src/telemsg/
├── limits.py        Telegram 硬性限制常量（改这里前先查官方文档）
├── models.py        InlineButton / Keyboard / Media / Draft（pydantic v2）
├── validation.py    发送前校验，产出可读的 issue 列表 + 中性 notes
├── markup.py        HTML / MarkdownV2 转义，轻量 Markdown → Telegram HTML
├── payload.py       Draft → Bot API 请求（含 multipart 附件编译）
├── client.py        异步客户端：重试、退避、结构化错误
├── ratelimit.py     全局 / 群组 / 单会话三级限流
├── netutil.py       端口探测与选择（语义必须与 uvicorn 一致）
├── render.py        预览渲染 + HTML 白名单消毒
├── store.py         SQLite：模板 / 定时 / 审计 / 媒体库
├── service.py       业务门面：校验 → 发送 → 落库
├── scheduler.py     定时任务执行器（含 cron 星期语义修正）
├── diagnostics.py   把 Telegram 英文报错翻译成「问题 + 怎么办」
├── logging_setup.py 日志配置 + token 脱敏（安全关键）
├── doctor.py        环境自检
├── release.py       跨平台打包器
├── cli.py           命令行入口（Typer）
└── web/             FastAPI 应用 + 原生 JS 编辑器（无构建步骤）

packaging/           打包模板：windows/*.bat、macos/entry.py
scripts/             ship.sh（推送流程）、serverctl.py（本地服务管理）
tests/               全部离线可跑
docs/                本目录
```

---

## 3. 开发环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,web,media]"

telemsg doctor          # 环境自检，先跑这个
telemsg serve --open    # 起编辑器
```

`make help` 列出全部可用目标。

---

## 4. 测试

```bash
make test        # 离线用例，不联网、不发消息
make test-live   # 真实 API 联调（需 TELEMSG_LIVE_TEST=1，默认只调 getMe）
make lint
```

约定：

* **单元测试绝不访问网络**。客户端与调度测试用 `httpx.MockTransport` 拦掉。
* **绝不把真实 token 写进测试**。用形如 `1234567890:AAHfakeToken...` 的假值——
  `scripts/ship.sh` 的凭证扫描依赖 `fake` / `xxx` / `Example` 这类占位字样来放行。
* 涉及端口绑定的用例在受限环境（沙箱、部分 CI）会自动跳过，这是**预期行为**而不是失败。
* 新增功能请同时补测试。前端改动至少要保证 `node --check` 通过——
  `tests/test_web_assets.py` 会跑它，并核对 JS 里用到的元素 id 在模板中存在
  （这两条各挡住过一次真实事故）。

---

## 5. 提交与推送

提交信息用 Conventional Commits，格式与取舍见 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。

**不要直接 `git push`**，用：

```bash
make check   # = scripts/ship.sh --dry-run，只检查不推
make ship    # 检查全过才推送
```

五道关：仓库状态 → 敏感信息扫描 → lint + 测试 → 远端同步检查 → 推送后校验。

> 为什么这么严：Bot Token 一旦进入公开仓库就是既成事实，`git revert` 删不掉历史，
> 只能去 @BotFather 用 `/revoke` 重发。这个项目在开发过程中**已经两次**差点把真实
> token 写进测试文件，都是靠推送前的扫描拦下的。

---

## 6. 发布与打包

### 三条等价入口

```bash
make package-win                                   # 最常用
telemsg build --target windows                     # CLI 子命令，推荐
python -m telemsg.release --target windows         # 直接跑模块
python scripts/build_release.py --target windows   # 旧写法，薄封装，仍可用
```

实现在 `src/telemsg/release.py`，**只有这一份**；`scripts/build_release.py` 是
兼容用的薄封装（有测试保证它不会重新长出实现）。

目标：`windows`（任意系统可构建）/ `macos`（必须 macOS）/ `all` / `current`。

### 为什么 Windows 包能在 Mac 上构建

PyInstaller 不能交叉编译，所以在 Mac 上出 Windows 包**不能**用它。这里走的是
「官方 embeddable 运行时 + 交叉下载 Windows wheel」，全程只下载和摆放，不编译。

```
python-3.12.x-embed-amd64.zip                    ← python.org 官方免安装运行时
          │
  pip --platform win_amd64 --only-binary=:all:   ← 在 Mac 上拉 Windows 版 wheel
          │
  src/telemsg + packaging/windows/*
          ▼
  dist/TeleMsgCreator-<ver>-win64.zip            ← Windows 解压双击即用
```

完整原理、产物结构与踩坑记录见 [`PACKAGING.md`](PACKAGING.md)。

### 发布检查清单

- [ ] `make test` 全绿，`make lint` 无告警
- [ ] `pyproject.toml` 里的版本号已更新
- [ ] `telemsg doctor` 在本机能跑通
- [ ] `make package-win` 产物校验通过（脚本自动检查 `.pyd` 与 `.so` 混入）
- [ ] 解压产物**实测**一次 `start.bat` / `doctor.bat`（在真实 Windows 上）
- [ ] 确认 `dist/` 未被提交（已在 `.gitignore`）
- [ ] 提醒使用者：分发前删掉 `.env` 与 `data/`

> 打包器能在 Mac 上验证的结构问题（平台二进制、`._pth`、源码一致性、语法编译）
> 它都验了；**但「能不能真的在 Windows 上跑起来」必须到 Windows 上验**，
> 这一步无法省略。

---

## 7. 用户首次使用引导（onboarding）的维护

新用户第一次打开页面时会看到四步向导：**获取 Token → 验证 → 目标频道 → 完成**。
这是产品最重要的转化路径，改动时注意：

* 向导由 `web/static/app.js` 里的 `openSetup()` / `gotoStep()` 驱动，
  DOM 在 `web/templates/index.html` 的 `#setupModal`。
* **必须先验证再落盘**：`POST /api/settings` 会先用新 token 调 `getMe`，失败就返回
  中文原因 + 修复建议，绝不写入半个错误的配置。改这里时不要把顺序调过来。
* 写 `.env` 走「临时文件 + 原子替换」，权限强制 `0600`。测试通过 `env_file`
  注入临时路径，**永远不要让它写到仓库根目录的真实 `.env`**。
* 如果 shell 里已经有 `TELEMSG_BOT_TOKEN`，环境变量优先级高于 `.env`，
  向导会明确提示——这是为了避免「改了文件重启没生效却找不到原因」。
* 文案原则：说清**下一步做什么**，而不只是报错。错误提示统一走
  `diagnostics.py` 的 `explain()`，CLI 与 Web 共用同一份文案。

---

## 8. 关键设计决策（改之前先读）

| 决策 | 原因 |
| --- | --- |
| 日志层强制脱敏 | httpx 在 INFO 级会打印带 token 的完整 URL；已压到 WARNING 并加过滤器兜底 |
| `bundle` 依赖集不含 `uvicorn[standard]` | 它依赖的 uvloop 不支持 Windows，会让交叉安装直接解析失败 |
| 交叉安装必须 `--only-binary=:all:` | 否则 pip 会在 Mac 上现场编译出 macOS 二进制，到 Windows 必崩 |
| zip 内文件名全 ASCII | Windows 自带解压对 UTF-8 文件名支持不稳定，中文会乱码 |
| 端口探测要设 `SO_REUSEADDR` | 不设会把 `TIME_WAIT` 误判成占用，导致每次重启端口往前漂一格 |
| Windows 上用 `SO_EXCLUSIVEADDRUSE` | Windows 的 `SO_REUSEADDR` 是「允许抢占端口」的语义，会导致漏判 |
| cron 星期做了转换 | 标准 cron 里 `1` 是周一，APScheduler 里 `1` 是周二 |
| 媒体组带按钮直接报错 | Telegram 的 album 不支持 inline keyboard，静默失败比报错难排查得多 |
| 空 `Draft` 返回占位预览 | 编辑器初始状态本来就该是空的，不该刷 422 错误 |

---

## 9. 排障手册

| 症状 | 先看哪里 |
| --- | --- |
| 双击没反应 / 起不来 | `doctor.bat`（或 `telemsg doctor`），逐项给出原因 |
| 页面打不开 | `data/server.port` 里是**实际**监听地址；端口被占会自动顺延 |
| 提示 chat not found | 机器人没进频道或没有「发布消息」权限——`diagnostics.py` 有对应文案 |
| 发送成功但没看到消息 | `telemsg history` 查审计，再看 `data/uploads/` 里媒体是否完整 |
| 定时任务不触发 | 需要 `telemsg run-scheduler` 常驻；`telemsg schedule list` 看状态 |
| 日志里疑似出现 token | 立刻查 `logging_setup.py` 的过滤器；确认后去 @BotFather `/revoke` |
| 打包产物在 Windows 崩 | 在 Windows 上跑 `doctor.bat`，把输出带回来 |

---

## 10. 已知限制与技术债

* **媒体组不能带按钮**——Telegram 的限制，不是缺陷。
* 标准 Bot API 单文件上传上限 50 MB，更大需自建 Bot API Server。
* `callback_data` 按钮需要你自己的机器人接收 `callback_query`；本项目只负责发送。
* `web_app` 按钮仅私聊生效。
* macOS 包依赖 PyInstaller 且必须在 macOS 上构建；未做 Apple 签名，首次打开需右键放行。
* Windows 包未做代码签名，可能被杀毒软件误报（`python.exe` 来自 python.org，未修改）。
* 打包器无法在非 Windows 环境验证「目标机能否真正运行」，这是流程上的已知缺口。
* 定时任务是单进程轮询（`data/telemsg.sqlite3`），多实例同时跑会重复发送；
  需要水平扩展时应改造为带锁的队列。
