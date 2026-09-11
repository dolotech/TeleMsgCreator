# TeleMsgCreator

用 **图片 / 视频 + 文字 + 内联按钮** 的方式发布 Telegram 消息的生产级工具。

自带图形化编辑器（拖图、排按钮、实时预览）和完整的命令行，覆盖 Bot API 的全部按钮类型，
并内置 Telegram 的硬性约束校验、限流退避、定时发送、模板、广播与发送审计。

> 想知道市面上已经有哪些同类网站和机器人？见 [`docs/RESEARCH.md`](docs/RESEARCH.md)。
> 被卡在「为什么不让我发」的时候，见 [`docs/TELEGRAM_LIMITS.md`](docs/TELEGRAM_LIMITS.md)。

## 它解决什么问题

Telegram 的「图文 + 按钮」消息只能由机器人发出，而现成的发帖机器人普遍有三个毛病：
按钮类型只给「文字 + 链接」、没有 API、你的频道数据全在别人服务器上。
TeleMsgCreator 把这些补齐：

* 覆盖 `url` / `callback_data` / `copy_text` / `web_app` / `login_url` / `switch_inline_query` / `pay` 全部按钮类型，以及新版按钮配色（红/绿/蓝）；
* 支持单图 / 视频 / GIF / 文件 / 相册（2–10 条）与 spoiler 打码；
* 发送前先做 Telegram 官方约束校验（4096 / 1024 / 64 字节 / 10 MB / 10000 像素 / 宽高比 20 等）；
* 内置令牌桶限流与 `429 retry_after` 退避，批量发送不会把频道发炸；
* 模板、定时任务、多目标广播、发送审计，全部落 SQLite；
* CLI 与 Web 双入口，Web 端有 1:1 观感的实时预览。

## 快速开始

```bash
git clone <this-repo> && cd TeleMsgCreator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[web,media]"

cp .env.example .env          # 填入 @BotFather 给你的 token
telemsg me                    # 验证 token 能否连通
telemsg serve                 # 打开 http://127.0.0.1:8765 开始可视化编辑
```

只要核心发送能力的话，`pip install -e .` 就够了（不装 Web 依赖）。

### 让站点常驻运行

直接 `telemsg serve` 会占用当前终端；关掉终端服务就没了。想让它一直在：

```bash
python scripts/serverctl.py start     # 后台启动，关掉终端也不会停
python scripts/serverctl.py status    # 看状态
python scripts/serverctl.py logs -f   # 跟踪日志
python scripts/serverctl.py stop      # 停止
```

默认监听 `127.0.0.1:8765`，端口可用 `--port 9000` 改。
启动日志与 PID 文件放在 `data/`（已被 `.gitignore` 忽略）。

**端口被占用时会自动换一个**（8765 → 8766 → …，最多试 10 个，再不行就交给系统分配），
并在控制台明确告诉你实际地址；`telemsg serve --no-port-fallback` 可以关掉这个行为，
改成直接报错。实际监听的地址会写到 `data/server.port`，`serverctl status` 读的就是它。

> 注意：判断「端口是否可用」时必须和 uvicorn 用同样的套接字选项（POSIX 上开
> `SO_REUSEADDR`）。否则刚重启时端口上残留的 `TIME_WAIT` 会被误判为「被占用」，
> 导致每重启一次端口就往前漂一格。

### 用之前必须做的两件事

1. **把机器人拉进目标频道并给发帖权限**（频道 → 管理员 → 添加机器人 → 勾选「发布消息」）。
2. **频道用户名以 `@` 开头填进 `chat_id`**；私有频道用 `-100` 开头的数字 ID。

## Web 编辑器

`telemsg serve` 之后打开 `http://127.0.0.1:8765`：

**第一次打开会弹出引导**：填 Bot Token → 自动调 `getMe` 验证 →（可选）填默认频道 → 开始编辑。
验证通过才会写入配置文件，填错了会当场告诉你错在哪、怎么改。之后随时可以点右上角「⚙ 设置」重新打开。

* 左侧填目标会话、正文，拖入图片 / 视频；
* 按钮按「行」编排，每行可以放多个，逐个选择动作类型与配色；
* 右侧是实时预览——就是你手机里会长成的样子；
* 下面是校验结果：哪里超字数、哪个按钮 `callback_data` 太长，会直接指出来；
* 「立即发送」「加入定时」「保存模板」「查看 API 请求」各司其职。

给公网访问时务必设 `TELEMSG_UI_PASSWORD`（用户名固定为 `telemsg`）。

### 引导里都能填什么

| 项 | 说明 |
| --- | --- |
| Bot Token | 必填。会先调 `getMe` 验证，失败会给出中文原因（无效 / 被吊销 / 网络不通） |
| 默认目标频道 | 可选。填了会顺手 `getChat` 探一下能不能访问；访问不到只是提醒，不会阻止保存 |
| API 地址 | 可选。自建 Bot API Server 时改这里，留空即官方接口 |

保存会写入 `.env`（写之前是「临时文件 + 原子替换」，并强制 `0600` 权限，因为这是等同于密码的东西）。
也可以在弹窗里选择只让配置在本次运行内生效、不落盘。

> ⚠️ 如果启动服务时 shell 里已经有 `TELEMSG_BOT_TOKEN` 环境变量，它的优先级**高于** `.env`。
> 引导里检测到这种情况会明确提示，避免你改了 `.env` 却发现重启后没生效。

## 命令行

```bash
# 1. 图片 + 两个按钮（最常用）
telemsg send -c @my_channel \
  --photo ./cover.jpg \
  --text "<b>秋季新品</b> 上线了" \
  -b "🛒 立即购买|https://example.com/buy" \
  -b "📋 复制优惠码|copy:SAVE20" \
  --per-row 2

# 2. 先干跑，看看到底会发出什么请求（不联网）
telemsg send --json draft.json --dry-run

# 3. 生成离线预览 HTML，双击即可用浏览器看效果
telemsg preview --json draft.json -o preview.html

# 4. 定时 / 周期发送
telemsg schedule add --json draft.json -c @my_channel --at "2026-10-01T09:00:00+08:00"
telemsg schedule add --json draft.json -c @my_channel --cron "0 9 * * 1-5"
telemsg run-scheduler                 # 常驻进程，负责到点发送

# 5. 广播到多个频道
telemsg broadcast --json draft.json --chats "@a,@b,-1001234567890" --concurrency 4

# 6. 找 chat_id / 排查 webhook
telemsg chats            # 列出机器人最近见过的会话（webhook 占用时会给出提示）
telemsg webhook info     # 看 webhook 挂在哪、有没有待处理更新

# 7. 模板与审计
telemsg templates list
telemsg history -n 20
```

完整命令列表：`telemsg --help`。

## 草稿 JSON 格式

这是 CLI、模板、定时任务和外部程序统一使用的中间格式：

```json
{
  "name": "新品上线",
  "chat_id": "@my_channel",
  "text": "<b>秋季新品</b> 来了",
  "parse_mode": "HTML",
  "media": {
    "kind": "photo",
    "source": "https://example.com/cover.jpg",
    "has_spoiler": false
  },
  "keyboard": {
    "rows": [
      [
        { "text": "🛒 购买", "url": "https://example.com/buy", "style": "primary" },
        { "text": "📋 复制码", "copy_text": { "text": "SAVE20" }, "style": "success" }
      ],
      [{ "text": "💬 客服", "url": "https://t.me/support" }]
    ]
  },
  "disable_notification": false,
  "protect_content": false
}
```

`media.source` 可以是本地路径、公网 URL 或 `file_id`。带 `media` 时 `text` 会自动变成 caption。

`keyboard` 也支持简写字符串（换行分行，`文案|目标`，目标支持 `https://` / `cb:` / `copy:` / `query:`）：

```json
{ "keyboard": "🛒 购买|https://shop.example.com|📋 复制|copy:SAVE20\n💬 客服|https://t.me/support" }
```

用 `telemsg schema` 可以打印完整 JSON Schema。

## 按钮类型对照

| 类型 | 写 JSON 的方式 | 需要后端吗 | 说明 |
| --- | --- | --- | --- |
| 跳转链接 | `{"text":"…","url":"https://…"}` | 否 | 最常用 |
| 复制文本 | `{"text":"…","copy_text":{"text":"…"}}` | 否 | Bot API 7.11+，营销场景很好用 |
| 回调 | `{"text":"…","callback_data":"…"}` | 是 | 你的机器人要处理 `callback_query`；频道里尤其要确认权限 |
| Web App | `{"text":"…","web_app":{"url":"https://…"}}` | 是 | 仅私聊 |
| 内联分享 | `{"text":"…","switch_inline_query":"…"}` | 是 | 频道中不支持 |
| 支付 | `{"text":"…","pay":true}` | 是 | 必须是第一行第一个按钮 |

三个动作字段**必须且只能有一个**，`style`（`primary` / `success` / `danger`）和
`icon_custom_emoji_id` 是可选修饰。

## 项目结构

```
src/telemsg/
  limits.py       Telegram 硬性限制常量
  models.py       InlineButton / Keyboard / Media / Draft
  validation.py   发送前校验，产出可读的 issue 列表
  markup.py       HTML / MarkdownV2 转义，轻量 Markdown -> Telegram HTML
  payload.py      草稿 -> Bot API 请求（含 multipart 上传）
  client.py       异步客户端：重试、限流、结构化错误
  ratelimit.py    全局 / 群组 / 单会话三级限流
  render.py       预览渲染 + HTML 白名单消毒
  store.py        SQLite：模板 / 定时 / 审计 / 媒体库
  service.py      业务门面：校验 -> 发送 -> 落库
  scheduler.py    计划任务执行器（含 cron 星期语义修正）
  cli.py          命令行
  web/            FastAPI + 原生 JS 的图形编辑器
```

## 生产部署

```bash
docker compose up -d          # Web 编辑器 + 调度守护进程
```

### 打包成免安装的绿色版

目标机器**不需要安装 Python**：

```bash
make package-win     # Windows 绿色版：在 macOS 上就能直接构建
make package-mac     # macOS 版（需要 pyinstaller，且必须在 macOS 上构建）
make package         # 两者都出
```

产物在 `dist/`。Windows 包拷过去解压，双击 `start.bat` 即用；
跑不起来就双击 `doctor.bat` 看自检结果。

> 关键点：PyInstaller **不能交叉编译**，所以「在 Mac 上出 Windows 包」走的是
> 官方 embeddable 运行时 + 交叉下载 Windows wheel 的路线，全程不需要 Windows 机器。
> 完整原理、结构说明与踩过的坑见 [`docs/PACKAGING.md`](docs/PACKAGING.md)。

裸机则跑两个进程：

```bash
telemsg serve          # 或 uvicorn telemsg.web.app:create_app --factory
telemsg run-scheduler  # 也可以用外部 cron 调 `telemsg run-scheduler --once`
```

安全清单：

* token 只放环境变量 / `.env`，日志里永远是脱敏的；
* `TELEMSG_UI_PASSWORD` 必设，并放在反向代理后面走 HTTPS；
* Web 默认只监听 `127.0.0.1`，别直接暴露公网；
* `data/`（SQLite + 上传的媒体）要定期备份，并确认 `.gitignore` 已生效。

## 测试

```bash
make test      # 114 个用例，全部离线：模型 / 校验 / 载荷 / 限流 / 客户端 / 调度 / 存储 / Web / 日志脱敏 / CLI / 前端资源
make test-live # 用真实 token 跑联调（默认只调 getMe；配了 TELEMSG_TEST_CHAT 才会真发一条并立即撤回）
make lint
```

客户端与调度测试全部基于 `httpx.MockTransport`，不需要真实 token，也不会真发消息。

要提交代码的话，别直接 `git push`——用 `make ship`：它会先跑 lint 与测试、
扫描凭证泄漏、确认没有分叉，再推送并校验结果。提交信息格式见
[CONTRIBUTING.md](CONTRIBUTING.md)。

想要端到端验证真实 token：

```bash
TELEMSG_LIVE_TEST=1 .venv/bin/python -m pytest tests/test_live.py -v -s
# 连「真的能发出去」一起验（会发一条然后立刻撤回，务必填测试频道）：
TELEMSG_LIVE_TEST=1 TELEMSG_TEST_CHAT=@my_test_channel make test-live
```

## 安全说明

* Bot Token 等同于这个机器人的账号密码：**拿到 token 的人可以用它的身份发任何消息**。别提交到 git、别贴进公开群。
* 本项目已 `.gitignore` 掉 `.env`；日志层另有脱敏过滤器（[`logging_setup.py`](src/telemsg/logging_setup.py)），会把 `bot<数字>:<token>` 统一替换成 `bot<TOKEN>`，且默认不再打印 httpx 的请求 URL。
* 如果 token 曾经外泄，去 [@BotFather](https://t.me/BotFather) 用 `/revoke` 换一个，再更新 `.env`。
* 若机器人已经挂着 webhook（`telemsg webhook info` 可查），说明有别的服务在接管它的更新。删 webhook 前务必确认，否则那个服务会直接失效。

## 出错时会看到什么

Telegram 的原始报错是英文且面向开发者，本项目统一翻译成「问题 + 怎么办」
（[`diagnostics.py`](src/telemsg/diagnostics.py)，CLI 与 Web 共用同一份文案）：

| Telegram 原文 | 你会看到 |
| --- | --- |
| `Bad Request: chat not found` | 找不到目标会话 👉 确认 `@用户名` 拼写、私有频道用 `-100` 数字 ID、机器人必须已进频道 |
| `Forbidden: bot is not a member` | 机器人不在这个会话里，或已被移出 👉 重新拉进频道并给发帖权限 |
| `Bad Request: not enough rights` | 机器人在该频道没有发帖权限 👉 频道 → 管理员 → 勾选「发布消息」 |
| `Bad Request: message caption is too long` | 图片说明超过 1024 字符 👉 长文拆成独立的一条消息 |
| `Bad Request: BUTTON_DATA_INVALID` | 按钮回调数据（callback_data）不合法 👉 上限 64 字节，一个汉字算 3 字节 |
| `Too Many Requests: retry after N` | 发送太快被限流 👉 工具已按 retry_after 自动退避 |
| `Unauthorized` | Bot Token 无效或已被吊销 👉 去 @BotFather 重新获取 |

原始报错不会丢，会附在末尾（`原文：[401] Unauthorized`），方便对照官方文档排查。

## 已知限制

* **媒体组（album）不能带按钮**——这是 Telegram 的限制，本项目会直接报错而不是静默失败；
* 标准 Bot API 单个上传文件上限 50 MB，超过需自建 Bot API Server（`TELEMSG_API_BASE`）；
* `callback_data` 按钮在频道里能否响应，取决于机器人是否真的在接收该频道的回调；
* `web_app` 按钮只在私聊生效；
* 定时任务需要一个常驻进程（或用外部 cron 调 `--once`）。

## License

MIT，见 [LICENSE](LICENSE)。
