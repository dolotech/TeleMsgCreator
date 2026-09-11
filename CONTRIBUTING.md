# 贡献与发布约定

> 接手项目请先读 [维护者指南](docs/MAINTAINING.md)——里面有架构地图、
> 测试策略、发布流程和关键设计决策。

## 提交信息

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <中文祈使句描述，不超过 50 字>

<空行>

<正文：说清「做了什么」和「为什么」>
```

`type` 取值：

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修 bug（尤其是会让使用者误判为「工具坏了」的问题） |
| `docs` | 只改文档 |
| `test` | 只改测试 |
| `chore` | 构建、依赖、脚本等杂项 |
| `refactor` | 不改变外部行为的重构 |

约定：

1. **标题写「为什么值得改」，正文写「改了什么」。** 不要出现 "update"、"fix bug" 这类没有信息量的标题。
2. **一个提交只做一件事。** 每个提交都应该是可构建、可测试的状态。
3. **破坏性变更**在 type 后加 `!`（如 `feat(api)!:`），并在正文里写明迁移方式。
4. 正文里的数字和错误码要具体。例如写「caption 上限 1024」，而不是「caption 有限制」。

示例（本仓库真实提交）：

```
fix: 修正媒体来源误判、CLI 传 URL 被破坏，并把隐式行为显式化

三处会被误当成「工具坏了」的真实缺陷：

1. --photo/--video/--document 之前声明成 Path，而 Path("https://a/b.jpg")
   会把双斜杠折叠成 https:/a/b.jpg……
```

## 推送流程

不要直接 `git push`，用封装好的脚本：

```bash
scripts/ship.sh              # 检查 + 推送
scripts/ship.sh --dry-run    # 只检查，不推送
make ship                    # 等价于上面第一条
```

它会依次把关：

1. **仓库状态** —— 工作区必须干净，且不能处于 detached HEAD；没有上游时提示 `git push -u`。
2. **敏感信息** —— 必须忽略 `.env`；扫描形如 `123456789:AAH…` 的 token（示例占位值需含 `xxx`/`Example` 等字样）；
   再用本机 `.env` 里的真实 token 做一次精确比对；同时检查私钥特征。
3. **质量门禁** —— `ruff check` + 全部测试。
4. **远端同步** —— `git fetch` 后判断领先/落后；分叉直接终止，落后则快进。
5. **推送并验证** —— 推送后重新 fetch，确认 `HEAD` 与 `origin/<branch>` 指向同一个 commit。

> 为什么不等「推完再看」：Bot Token 一旦进入公开仓库就是既成事实，
> 事后 `git revert` 也删不掉历史，只能去 @BotFather `/revoke` 重发。

## 测试

```bash
make test        # 157 个离线用例，不依赖网络
make test-live   # 真实 API 联调（需 TELEMSG_LIVE_TEST=1，默认只调 getMe）
make lint
```

新增功能请同时补测试。涉及真实凭证的用例一律走 `TELEMSG_*` 环境变量，
**不允许把 token 写进测试文件**——用形如 `1234567890:AAHfakeToken...` 的假值。
