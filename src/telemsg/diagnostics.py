"""把异常翻译成「人话 + 下一步怎么做」。

Telegram 的错误描述是英文且面向开发者（``Bad Request: chat not found``），
对使用者几乎没有指导意义。这里统一翻译，并给出可执行的修复动作。
CLI 与 Web 共用同一份文案，避免两边提示不一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConfigError, TelegramAPIError, TelemsgError, TransportError
from .logging_setup import redact


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """一条可读的诊断结果。"""

    message: str
    hint: str | None = None
    action: str | None = None
    raw: str | None = None

    @property
    def text(self) -> str:
        """一行式摘要，适合直接丢进 toast。"""
        parts = [self.message]
        if self.hint:
            parts.append(f"👉 {self.hint}")
        return " ".join(parts)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "message": self.message,
            "hint": self.hint,
            "action": self.action,
            "raw": self.raw,
        }


#: 按「错误码 + 描述关键字」匹配的规则表，靠前的先命中
_RULES: tuple[tuple[int | None, tuple[str, ...], Diagnosis], ...] = (
    (
        None,
        ("unauthorized",),
        Diagnosis(
            "Bot Token 无效或已被吊销",
            "去 @BotFather 用 /mybots 复制正确的 token；若曾泄露请用 /revoke 重发一个",
            action="open_settings",
        ),
    ),
    (
        401,
        (),
        Diagnosis(
            "Bot Token 无效（Telegram 返回 401）",
            "检查 token 是否复制完整、有没有多余空格，或去 @BotFather 重新获取",
            action="open_settings",
        ),
    ),
    (
        404,
        ("not found",),
        Diagnosis(
            "接口地址不存在（404）",
            "检查 TELEMSG_API_BASE 是否写对；若用自建 Bot API Server，确认它已启动",
        ),
    ),
    (
        400,
        ("chat not found",),
        Diagnosis(
            "找不到目标会话",
            "确认 @用户名拼写正确；私有频道要用 -100 开头的数字 ID；"
            "机器人必须已经被拉进该频道",
        ),
    ),
    (
        400,
        ("not enough rights", "have no rights"),
        Diagnosis(
            "机器人在该频道没有发帖权限",
            "频道 → 管理员 → 添加这个机器人 → 勾选「发布消息」（如需 pin 还要勾「置顶消息」）",
        ),
    ),
    (
        403,
        ("bot is not a member", "bot was kicked", "kicked"),
        Diagnosis(
            "机器人不在这个会话里，或已被移出",
            "把机器人重新拉进频道/群组，并给它发帖权限",
        ),
    ),
    (
        403,
        (),
        Diagnosis(
            "机器人无权向该会话发送消息",
            "检查它是否已被移出、是否被限制发言，以及是否具备管理员权限",
        ),
    ),
    (
        400,
        ("can't initiate conversation", "user is deactivated"),
        Diagnosis(
            "无法主动给这个用户发消息",
            "需要对方先和机器人说过话，才能向 TA 发送消息",
        ),
    ),
    (
        400,
        ("caption is too long",),
        Diagnosis(
            "图片说明超过 1024 字符",
            "把长文拆成独立的一条文本消息，或精简说明",
        ),
    ),
    (
        400,
        ("message is too long",),
        Diagnosis(
            "正文超过 4096 字符",
            "拆成多条消息发送",
        ),
    ),
    (
        400,
        ("button_data_invalid", "buttondata", "button data"),
        Diagnosis(
            "按钮回调数据（callback_data）不合法",
            "callback_data 必须是 1-64 字节（中文按 UTF-8 算，一个汉字 3 字节）",
        ),
    ),
    (
        400,
        ("can't parse entities", "parse entities", "unsupported start tag"),
        Diagnosis(
            "富文本格式解析失败",
            "HTML 标签没有闭合，或用了 Telegram 不支持的标签；"
            "可先切换成「纯文本」确认是格式问题",
        ),
    ),
    (
        400,
        ("wrong file identifier", "http url specified", "webpage_curl_failed", "wrong remote file"),
        Diagnosis(
            "媒体地址无法使用",
            "本地路径要真实存在；网络图片必须是可公开访问的直链；"
            "file_id 不能跨机器人复用（换过 token 就失效）",
        ),
    ),
    (
        400,
        ("media group", "media_group"),
        Diagnosis(
            "相册（媒体组）参数不合法",
            "相册需要 2-10 项，只有第一项能带说明，且不能与按钮同时使用",
        ),
    ),
    (
        400,
        ("photo_invalid_dimensions", "photo invalid", "image_process_failed"),
        Diagnosis(
            "图片尺寸不符合 Telegram 要求",
            "宽 + 高 不超过 10000 像素，长宽比不超过 20:1",
            action="open_settings",
        ),
    ),
    (
        400,
        ("file is too big", "too big"),
        Diagnosis(
            "文件超过 Bot API 上传上限",
            "标准 Bot API 单文件上限 50MB；更大的文件需要自建 Bot API Server",
        ),
    ),
    (
        413,
        (),
        Diagnosis("文件过大", "标准 Bot API 单文件上限 50MB"),
    ),
    (
        429,
        (),
        Diagnosis(
            "发送太快，被 Telegram 限流",
            "稍后重试即可；批量发送时降低并发，工具已按 retry_after 自动退避",
        ),
    ),
    (
        409,
        ("webhook",),
        Diagnosis(
            "机器人已设置 webhook，无法使用 getUpdates",
            "这不影响发帖；如需用 getUpdates 请先确认没有其它服务在用，"
            "再执行 telemsg webhook delete --yes",
        ),
    ),
    (
        400,
        ("message thread not found", "topic"),
        Diagnosis("论坛话题 ID 不存在", "确认话题 ID；不指定则为频道主楼"),
    ),
    (
        400,
        ("message to edit not found", "message can't be edited"),
        Diagnosis("要修改的消息不存在或不可编辑", "确认 message_id 与目标会话"),
    ),
)


def _match(error_code: int | None, description: str) -> Diagnosis | None:
    lowered = description.lower()
    for rule_code, keywords, diagnosis in _RULES:
        if rule_code is not None and error_code != rule_code:
            continue
        if keywords and not any(word in lowered for word in keywords):
            continue
        if keywords or rule_code is not None:
            return diagnosis
    return None


def explain(exc: BaseException) -> Diagnosis:
    """把任意异常翻译成可读诊断。未知异常会原样保留信息，不会吞掉细节。"""
    if isinstance(exc, TelegramAPIError):
        description = exc.description or ""
        matched = _match(exc.error_code, description)
        raw = f"[{exc.error_code}] {description}"
        if matched is not None:
            return Diagnosis(matched.message, matched.hint, matched.action, raw=raw)
        if exc.error_code is not None and exc.error_code >= 500:
            return Diagnosis(
                "Telegram 服务端临时故障",
                "工具已自动重试；持续失败可稍后再试",
                raw=raw,
            )
        return Diagnosis(f"Telegram 拒绝了这次请求：{description}", raw=raw)

    if isinstance(exc, ConfigError):
        return Diagnosis(
            "还没有配置 Bot Token",
            "在设置里填入 @BotFather 给的 token 就能开始发送",
            action="open_settings",
            raw=redact(str(exc)),
        )

    if isinstance(exc, TransportError):
        return Diagnosis(
            "网络请求失败",
            "检查本机网络或代理设置（如需代理可配置 TELEMSG_PROXY）",
            raw=redact(str(exc)),
        )

    if isinstance(exc, TelemsgError):
        return Diagnosis(redact(str(exc)))

    if isinstance(exc, FileNotFoundError):
        return Diagnosis(redact(str(exc)), "确认路径写对，或用「粘贴 URL」的方式引用网络图片")

    return Diagnosis(redact(str(exc) or exc.__class__.__name__))
