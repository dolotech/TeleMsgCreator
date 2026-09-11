# Telegram 硬性限制速查

全部来自官方文档 <https://core.telegram.org/bots/api>（2026-09 复核）。
本项目的 [`src/telemsg/limits.py`](../src/telemsg/limits.py) 与
[`src/telemsg/validation.py`](../src/telemsg/validation.py) 按这些数字做校验。

## 文本

| 项 | 限制 | 说明 |
| --- | --- | --- |
| 消息正文 | 4096 字符 | `sendMessage.text` |
| 媒体说明 | 1024 字符 | `sendPhoto.caption` 等，超了直接报错 |
| 媒体组说明 | 1024 字符 | 且**只有第一条** `InputMedia` 能带 caption |

> 想发长文：拆消息，或正文放消息、细节放频道评论 / Telegraph 页面。

## 内联按钮

`InlineKeyboardButton` 的官方说明是：
「除 `text`、`icon_custom_emoji_id`、`style` 之外，**必须且只能**有一个字段用来指定按钮类型」。

| 字段 | 类型 | 备注 |
| --- | --- | --- |
| `text` | String | 按钮文字 |
| `style` | String | `danger`(红) / `success`(绿) / `primary`(蓝)，新版特性 |
| `icon_custom_emoji_id` | String | 需要 Premium / Fragment 用户名 |
| `url` | String | `http(s)://` 或 `tg://` |
| `callback_data` | String | **1–64 字节**（UTF-8 计），超了报错 |
| `web_app` | WebAppInfo | 仅私聊可用 |
| `login_url` | LoginUrl | HTTPS 授权登录 |
| `switch_inline_query` / `_current_chat` / `_chosen_chat` | String | 内联分享；频道中不支持 |
| `copy_text` | CopyTextButton | 点击复制文本（Bot API 7.11+），**不需要后端** |
| `callback_game` / `pay` | | Pay 按钮必须是第一行第一个 |

布局建议：每行 ≤ 3 个按钮（官方无硬限制，但客户端会压缩显示）。

## 媒体

| 项 | 限制 |
| --- | --- |
| 单张照片 | ≤ 10 MB |
| 照片尺寸 | 宽 + 高 ≤ 10000 |
| 照片宽高比 | ≤ 20:1 |
| 媒体组 | 2–10 条 |
| 上传文件（标准 Bot API） | ≤ 50 MB |
| 上传文件（自建 Bot API Server） | ≤ 2000 MB |
| 媒体组限制 | **不支持 inline keyboard**；`document` 不能与 photo/video 混用；不支持 `audio` |

## 速率

| 场景 | 限制 |
| --- | --- |
| 全局 | 约 30 条/秒 |
| 同一群组 | 约 20 条/分钟 |
| 同一会话 | 建议 ≥ 1 秒/条 |
| 超出时 | 返回 `429` + `parameters.retry_after`，必须按该值退避 |

## 其他坑

* `message_thread_id` 才能发进论坛话题。
* `protect_content=True` 会禁用转发与保存。
* 频道里的 `callback_data` 按钮，只有机器人能收到 `callback_query` 时才有反应；
  纯链接按钮（`url` / `copy_text`）不需要后端。
* 用 `file_id` 复用已上传的媒体，可以避免重复上传（本项目的媒体库会记录 `file_id`）。
