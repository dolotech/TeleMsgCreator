# 「图文 + 按钮」Telegram 消息工具调研

> 调研时间：2026-09-11　方法：搜索引擎（Brave）+ 直接访问产品页 / Telegram 公开频道页核验。
> 所有「实测」条目都是我逐个打开页面后摘录的原文，未验证的我会明确标注。

## 一、结论先说

**这类工具存在，而且不少。** 但分成两拨，各自都有明显缺口：

| 类型 | 代表 | 能做什么 | 缺什么 |
| --- | --- | --- | --- |
| 电报机器人（对话式） | @PostBot、@ControllerBot、@InlineButtonCreatorBot、@RichPostBot | 图文 + 按钮 + 定时发送 | 数据全在别人服务器；按钮能力受限于机器人实现；无 API、无审计、不可自托管 |
| SaaS 后台 | Telepost（app.telepost.me）、SUCH、Telewer | 面板化排期与按钮 | 订阅制、英文/俄文为主、能力被套餐限制 |
| 开源小工具 | html5-ninja/inline-button-for-telegram-channel 等 | 生成带按钮的消息 | 只是网页生成器，无排期/审计/模板/批量，很多已停止维护 |

**没有找到**同时满足这几点的现成方案：自托管、覆盖 Bot API 全部按钮类型（含新版
`style` 配色 / `copy_text` / `web_app` / `pay`）、媒体组、实时预览、模板、定时、广播、
发送审计、CLI + Web 双入口。这正是本仓库 `TeleMsgCreator` 要补的位。

## 二、实测核验过的电报机器人

下列数字来自 `t.me/<bot>` 公开页面（Telegram 官方展示的月活），描述为页面原文摘录。

| 机器人 | 月活（页面展示） | 页面原文 / 能力 | 链接 |
| --- | --- | --- | --- |
| **@PostBot** | 3,459,996 | “⚡️ Ultimate post builder. Templates, inline buttons, premium emojis, one-click sending to any chat!” | https://t.me/PostBot |
| **@TelepostBot** | 636,563 | 俄语系排期工具，配套面板 app.telepost.me | https://t.me/TelepostBot |
| **@ControllerBot** | 245,319 | “Awesome bot for channel owners that helps you to create rich posts, view stats and more.” | https://t.me/ControllerBot |
| **@InlineButtonCreatorBot** | 27,029 | 生成内联按钮；中文教程提到它在频道里发送时可隐藏“via 来源” | https://t.me/InlineButtonCreatorBot |
| **@MenubuilderBot** | 16,164 | “creates Menu Bots, eShops, works in Groups, has its own Ads Market” | https://t.me/MenubuilderBot |
| **@RichPostBot** | 未展示 | **中文描述**：“专为 TG 营销设计的卡片助手。自定义图文 + 跳转按钮，Inline 模式一键群发，提高点击率必备工具。” | https://t.me/RichPostBot |
| **@Inlinebuttons_bot** | 25.1k（Botostore） | “I can create inline buttons for everyone.” 支持 inline 模式发到任意聊天；要发到频道需自建实例 | https://botostore.com/c/inlinebuttons_bot/ |
| @LivePostBot | 未展示 | 仅显示 “@livebot”，无描述，未验证其按钮能力 | https://t.me/LivePostBot |
| @SkeddyBot | 12,813 | 提醒工具（**不是**发帖工具） | https://t.me/SkeddyBot |

另外 `@PostMakerBot`、`@TGPostBot`、`@PostEditorBot`、`@PostPadBot`、`@ChannelPostBot`
这些用户名存在，但页面没有可核验的功能描述，**不能确认**是否属于同类工具：
`@ChannelPostBot` 的简介是 “Suggest your content to be posted in a channel”，其实是**投稿**机器人。

## 三、实测核验过的网站 / 开源项目

| 名称 | 实测发现 | 链接 |
| --- | --- | --- |
| inline-button-for-telegram-channel | 仓库简介：“Create awesome Telegram message with inline buttons and without coding skill”，是一个无需写代码的页面生成器（Star 数很少，长期未更新） | https://github.com/html5-ninja/inline-button-for-telegram-channel |
| SUCH | 官方博客有《How to Attach Buttons to Telegram Channel Posts》，产品主站定位是 Telegram 客服机器人（37,000+ bots），帖子按钮是其附加能力 | https://www.such.chat/blog/how-to-attach-buttons-to-telegram-channel-posts |
| Telewer | 无代码 Telegram 机器人搭建器 | https://telewer.com/ |
| Botostore | 机器人目录站，收录 131.1k+ 机器人，可按 “inline buttons” 等分类检索 | https://botostore.com/c/inlinebuttons_bot/ |
| Robopost | 社媒排期/自动化平台（含 Telegram），首页通篇未提按钮能力，**未验证**支持内联按钮 | https://robopost.app/ |
| itgoyo/TelegramBot | 中文社区维护的机器人大全（1.9k Star），可用来继续挖机器人 | https://github.com/itgoyo/TelegramBot |

**中文语境下的常见答案**（搜索“电报 图文 按钮 机器人”时出现）多为教程而非工具，例如
`yummy.best` 的《在 Telegram 创建带按钮的消息》推荐的就是 `@InlineButtonCreatorBot`；
`topromax.com` 的《电报制作消息按钮》同理。

## 四、为什么仍然值得自己做

1. **数据主权**：所有现成的发布机器人都会把你的频道、图文素材、发布时间留在对方服务器。
2. **按钮能力被阉割**：Bot API 早已支持按钮 `style`（红/绿/蓝）、`copy_text`（复制文本）、
   `web_app`、`pay`、`login_url` 等，但绝大多数机器人 UI 只让你选“文字 + 链接”。
3. **没有 API**：想把“发一条带按钮的频道帖”接进自己的 CI / 数据管线，几乎不可能。
4. **媒体组限制**：专辑（album）不能带按钮，这一点几乎所有工具都不会告诉你，只会静默失败。
5. **限流不可控**：Telegram 对群组有 20 条/分钟的限制，第三方工具批量发时经常整批失败。
6. **审计与回滚**：发出去的消息 id、失败原因、重试过程——只有自建才能留档。

## 五、检索方法与局限

* 搜索引擎使用 Brave；中文、英文、俄文三种关键词都试过。
* `core.telegram.org/bots/api` 直接访问成功，本项目的所有硬限制都来自该文档
  （见 [`TELEGRAM_LIMITS.md`](./TELEGRAM_LIMITS.md)）。
* 局限：
  * 本次环境无法访问 GitHub 搜索 API（限流）与部分站点（如 postbot.info 连接被重置），
    个别产品的按钮能力标注为“未验证”；
  * 机器人月活是 Telegram 页面展示值，会随时间变化；
  * 俄语/波斯语圈还有不少同类机器人（如各类 “пост-мейкер”），本次未能穷尽。

## 六、参考链接

* Telegram Bot API：https://core.telegram.org/bots/api
* Bot API 按钮类型：https://core.telegram.org/api/bots/buttons
* 内联键盘构造器（GramIO）：https://gramio.dev/keyboards/inline-keyboard
* 社区讨论“怎么给频道帖加按钮”：https://www.reddit.com/r/Telegram/comments/1cdl7fk/
