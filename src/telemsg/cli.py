"""命令行入口：``telemsg --help``。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .client import TelegramClient, mask_token
from .config import Settings, get_settings
from .errors import TelegramAPIError, TelemsgError
from .logging_setup import setup_logging
from .models import Draft, Keyboard, Media
from .service import PostService
from .store import Store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="TeleMsgCreator —— 用图文 + 按钮的方式发布 Telegram 消息。",
)
schedule_app = typer.Typer(help="管理计划任务", no_args_is_help=True)
templates_app = typer.Typer(help="管理消息模板", no_args_is_help=True)
webhook_app = typer.Typer(help="查看或移除机器人 webhook", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")
app.add_typer(templates_app, name="templates")
app.add_typer(webhook_app, name="webhook")

console = Console()
err_console = Console(stderr=True, style="bold red")


# ---------------------------------------------------------------- helpers
def _setup_logging(level: str, verbose: bool) -> None:
    handler = RichHandler(console=console, rich_tracebacks=True, show_path=verbose)
    logging.basicConfig(
        level=logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[handler],
    )
    # 压掉 httpx 的请求日志（URL 里带 token），并给所有 handler 挂脱敏过滤器
    setup_logging(level, verbose=verbose, handler=handler)


def _settings(
    token: str | None = None,
    api_base: str | None = None,
    db: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    log_level: str = "INFO",
) -> Settings:
    base = get_settings()
    updates: dict[str, Any] = {}
    if token:
        updates["bot_token"] = token
    if api_base:
        updates["api_base"] = api_base
    if db:
        updates["db_path"] = db
    if dry_run:
        updates["dry_run"] = True
    settings = base.model_copy(update=updates) if updates else base
    settings.ensure_dirs()
    _setup_logging(log_level or settings.log_level, verbose)
    return settings


def _run(coro):
    return asyncio.run(coro)


def _fail(message: str, code: int = 1) -> None:
    err_console.print(f"✖ {message}")
    raise typer.Exit(code)


def _build_keyboard(buttons: list[str], per_row: int) -> Keyboard:
    if not buttons:
        return Keyboard()
    parsed = [button for button in buttons]
    keyboard = Keyboard.from_spec("\n".join("|".join(parsed[i : i + per_row]) for i in range(0, len(parsed), per_row)))
    return keyboard


def _draft_from_options(
    *,
    chat: str | None,
    text: str | None,
    parse_mode: str | None,
    photo: Path | None,
    video: Path | None,
    document: Path | None,
    caption: str | None,
    buttons: list[str],
    per_row: int,
    spoil: bool,
    silent: bool,
    protect: bool,
    thread_id: int | None,
    name: str | None,
) -> Draft:
    keyboard = _build_keyboard(buttons, max(1, per_row))
    media_path = photo or video or document
    kind = "photo" if photo else "video" if video else "document" if document else None
    media = None
    if media_path:
        media = Media(
            kind=kind or "photo",
            source=str(media_path),
            caption=caption or text,
            parse_mode=(parse_mode or "HTML") if (caption or text) else None,
            has_spoiler=spoil and kind == "photo",
        )
        text_value = None
    else:
        text_value = text
    return Draft(
        name=name,
        chat_id=chat,
        text=text_value,
        parse_mode=parse_mode or ("HTML" if text and not media else None),
        media=media,
        keyboard=keyboard,
        disable_notification=silent,
        protect_content=protect,
        message_thread_id=thread_id,
    )


def _load_draft(json_file: Path | None, template: str | None, settings: Settings) -> Draft | None:
    if json_file:
        raw = sys.stdin.read() if str(json_file) == "-" else Path(json_file).read_text(encoding="utf-8")
        try:
            return Draft.from_json(raw)
        except Exception as exc:  # noqa: BLE001
            _fail(f"草稿 JSON 解析失败：{exc}")
    if template:
        draft = Store(settings.db_path).get_template(template)
        if draft is None:
            _fail(f"模板不存在：{template}")
        return draft
    return None


def _print_report(report) -> None:
    if not report.issues:
        console.print("[green]✔ 校验通过，未发现问题[/green]")
        return
    table = Table(title="校验结果", show_lines=False, header_style="bold")
    table.add_column("级别", width=8)
    table.add_column("字段", style="cyan")
    table.add_column("说明")
    for issue in report.issues:
        color = "red" if issue.severity.value == "error" else "yellow"
        table.add_row(f"[{color}]{issue.severity.value}[/{color}]", issue.field, issue.message)
    console.print(table)


# ---------------------------------------------------------------- commands
@app.command("version")
def version() -> None:
    """打印版本号。"""
    console.print(f"TeleMsgCreator [bold]{__version__}[/bold]")


@app.command("me")
def me(
    token: str | None = typer.Option(None, "--token", help="Bot Token（默认读环境变量）"),
    api_base: str | None = typer.Option(None, "--api-base", help="自建 Bot API 地址"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """检查 token 是否可用，并显示机器人信息。"""
    settings = _settings(token, api_base, verbose=verbose)

    async def run() -> dict:
        async with TelegramClient(settings=settings) as client:
            return await client.get_me()

    try:
        info = _run(run())
    except TelemsgError as exc:
        _fail(str(exc))
    console.print(Panel.fit(
        f"机器人：[bold]{info.get('first_name')}[/bold] (@{info.get('username')})\n"
        f"ID：{info.get('id')}\n"
        f"Token：{mask_token(settings.resolved_token)}\n"
        f"API：{settings.api_base}",
        title="连接正常",
        border_style="green",
    ))


def _common_options(  # noqa: PLR0913 - CLI 需要这些开关
    token: str | None,
    api_base: str | None,
    db: Path | None,
    dry_run: bool,
    verbose: bool,
) -> Settings:
    return _settings(token, api_base, db, dry_run, verbose)


@app.command("chats")
def chats(
    limit: int = typer.Option(100, "--limit", help="最多检查多少条历史更新"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """列出机器人最近见过的会话，用来找频道 / 群组的数字 chat_id。"""
    settings = _settings(token, api_base, verbose=verbose)

    async def run() -> list[dict]:
        async with TelegramClient(settings=settings) as client:
            return await client.discover_chats(limit=limit)

    try:
        found = _run(run())
    except TelegramAPIError as exc:
        if exc.error_code == 409:
            console.print(
                "[yellow]这个机器人当前挂着 webhook，getUpdates 不可用。[/yellow]\n"
                "这[bold]不影响发帖[/bold]（sendMessage / sendPhoto 照常工作），"
                "只是没法用它去发现 chat_id。\n"
                "两个办法：\n"
                "  1) 直接在频道设置里或已发布的帖子里拿 chat_id（@用户名 或 -100 开头）；\n"
                "  2) 确认没有其它服务在用这个机器人后，执行：\n"
                "     telemsg webhook info          # 先看清楚是谁在用\n"
                "     telemsg webhook delete --yes  # 再决定是否摘掉\n"
            )
            raise typer.Exit(0) from exc
        _fail(str(exc))
    except TelemsgError as exc:
        _fail(str(exc))

    if not found:
        console.print(
            "[yellow]没有发现任何会话。[/yellow]\n"
            "getUpdates 只保留最近 24 小时且未被 webhook 消费的更新，请：\n"
            "  1) 先把它拉进目标频道并给发帖权限；\n"
            "  2) 在频道里发一条消息，或把机器人设为管理员；\n"
            "  3) 然后重新执行本命令。"
        )
        raise typer.Exit(0)

    table = Table(title="机器人见过的会话")
    table.add_column("chat_id", style="cyan")
    table.add_column("类型")
    table.add_column("标题 / 用户名")
    table.add_column("可直接用的目标")
    for item in sorted(found, key=lambda x: str(x.get("type"))):
        name = item.get("title") or item.get("username") or item.get("first_name") or "-"
        if item.get("username"):
            name = f"{name} (@{item['username']})"
        usable = f"@{item['username']}" if item.get("username") else str(item["id"])
        table.add_row(str(item["id"]), str(item.get("type") or "-"), name, usable)
    console.print(table)


@app.command("send")
def send(
    chat: str | None = typer.Option(None, "--chat", "-c", help="目标会话：@channel 或 -1001234567890"),
    text: str | None = typer.Option(None, "--text", "-t", help="正文；带图时自动作为 caption"),
    photo: Path | None = typer.Option(None, "--photo", "-p", help="图片路径 / URL / file_id"),
    video: Path | None = typer.Option(None, "--video", help="视频路径 / URL / file_id"),
    document: Path | None = typer.Option(None, "--document", help="文件路径 / URL / file_id"),
    caption: str | None = typer.Option(None, "--caption", help="与 --text 不同时使用"),
    button: list[str] = typer.Option(
        [],
        "--button",
        "-b",
        help="按钮，可重复：'文案|https://链接' / '文案|cb:数据' / '文案|copy:文本'",
    ),
    per_row: int = typer.Option(1, "--per-row", help="每行放几个按钮"),
    parse_mode: str | None = typer.Option(None, "--parse-mode", help="HTML / MarkdownV2"),
    spoil: bool = typer.Option(False, "--spoiler", help="图片加遮罩"),
    silent: bool = typer.Option(False, "--silent", help="静默发送"),
    protect: bool = typer.Option(False, "--protect", help="禁止转发/保存"),
    thread_id: int | None = typer.Option(None, "--thread-id", help="论坛话题 ID"),
    json_file: Path | None = typer.Option(None, "--json", help="从 JSON 草稿读取（- 表示 stdin）"),
    template: str | None = typer.Option(None, "--template", help="使用已保存的模板"),
    save_as: str | None = typer.Option(None, "--save-as", help="发送后保存为模板"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只编译请求，不真的发送"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
    db: Path | None = typer.Option(None, "--db"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """发送一条图文 + 按钮消息。"""
    settings = _common_options(token, api_base, db, dry_run, verbose)
    draft = _load_draft(json_file, template, settings)
    if draft is None:
        draft = _draft_from_options(
            chat=chat,
            text=text,
            parse_mode=parse_mode,
            photo=photo,
            video=video,
            document=document,
            caption=caption,
            buttons=list(button),
            per_row=per_row,
            spoil=spoil,
            silent=silent,
            protect=protect,
            thread_id=thread_id,
            name=save_as,
        )
    elif chat:
        draft.chat_id = chat

    async def run() -> None:
        async with TelegramClient(settings=settings) as client:
            service = PostService(client, settings=settings)
            report = service.validate(draft)
            _print_report(report)
            if not report.ok:
                raise typer.Exit(2)
            if settings.dry_run:
                spec = await client.dry_run(draft)
                console.print(Panel(json.dumps(spec, ensure_ascii=False, indent=2), title="将要发送的请求"))
                console.print("[yellow]dry-run：未真正发送[/yellow]")
                return
            result = await service.send(draft)
            console.print(
                Panel.fit(
                    f"chat_id : {result.chat_id}\nmethod  : {result.method}\n"
                    f"message : {result.message_ids or result.message_id}",
                    title="[green]发送成功[/green]",
                    border_style="green",
                )
            )
            if save_as:
                service.save_template(save_as, draft)
                console.print(f"[green]已保存模板：{save_as}[/green]")

    try:
        _run(run())
    except typer.Exit:
        raise
    except TelegramAPIError as exc:
        _fail(f"{exc.method} 调用失败：[{exc.error_code}] {exc.description}")
    except TelemsgError as exc:
        _fail(str(exc))


@app.command("validate")
def validate(
    json_file: Path | None = typer.Option(None, "--json", help="草稿 JSON（- 表示 stdin）"),
    template: str | None = typer.Option(None, "--template"),
    token: str | None = typer.Option(None, "--token"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """只做校验，不发送（适合放进 CI）。"""
    settings = _settings(token, None, db)
    draft = _load_draft(json_file, template, settings)
    if draft is None:
        _fail("请用 --json 或 --template 指定草稿")
    from .validation import validate_draft

    report = validate_draft(draft)
    _print_report(report)
    raise typer.Exit(0 if report.ok else 2)


@app.command("preview")
def preview(
    json_file: Path | None = typer.Option(None, "--json", help="草稿 JSON（- 表示 stdin）"),
    template: str | None = typer.Option(None, "--template"),
    out: Path = typer.Option(Path("preview.html"), "--out", "-o", help="输出 HTML 路径"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """生成离线预览 HTML（双击即可用浏览器查看真实效果）。"""
    settings = _settings(None, None, db)
    draft = _load_draft(json_file, template, settings)
    if draft is None:
        _fail("请用 --json 或 --template 指定草稿")
    from .render import render_standalone_html

    out.write_text(render_standalone_html(draft), encoding="utf-8")
    console.print(f"[green]✔ 预览已写入[/green] {out.resolve()}")


@app.command("broadcast")
def broadcast(
    chats: str = typer.Option(..., "--chats", help="逗号分隔的目标：@a,@b,-100123"),
    json_file: Path | None = typer.Option(None, "--json", help="草稿 JSON（- 表示 stdin）"),
    template: str | None = typer.Option(None, "--template"),
    concurrency: int = typer.Option(4, "--concurrency", help="并发数（仍受速率限制约束）"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
    db: Path | None = typer.Option(None, "--db"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """把同一条消息广播到多个会话。"""
    settings = _common_options(token, api_base, db, dry_run, verbose)
    draft = _load_draft(json_file, template, settings)
    if draft is None:
        _fail("请用 --json 或 --template 指定草稿")
    targets = [c.strip() for c in chats.split(",") if c.strip()]
    if not targets:
        _fail("--chats 不能为空")

    async def run() -> list:
        async with TelegramClient(settings=settings) as client:
            return await PostService(client, settings=settings).broadcast(
                draft, targets, concurrency=concurrency
            )

    try:
        results = _run(run())
    except TelemsgError as exc:
        _fail(str(exc))
    table = Table(title="广播结果")
    table.add_column("目标")
    table.add_column("结果")
    ok = 0
    for r in results:
        if r.ok:
            ok += 1
            table.add_row(str(r.chat_id), f"[green]成功 message_id={r.message_id}[/green]")
        else:
            table.add_row(str(r.chat_id), f"[red]失败 {r.error}[/red]")
    console.print(table)
    console.print(f"成功 {ok}/{len(results)}")
    raise typer.Exit(0 if ok == len(results) else 1)


@schedule_app.command("add")
def schedule_add(
    chat: str = typer.Option(..., "--chat", "-c"),
    at: str | None = typer.Option(None, "--at", help="ISO 时间，如 2026-09-12T09:00:00+08:00"),
    cron: str | None = typer.Option(None, "--cron", help="5 段式表达式，如 '0 9 * * *'"),
    json_file: Path | None = typer.Option(None, "--json", help="草稿 JSON（- 表示 stdin）"),
    template: str | None = typer.Option(None, "--template"),
    name: str | None = typer.Option(None, "--name"),
    token: str | None = typer.Option(None, "--token"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """新增计划任务。"""
    settings = _settings(token, None, db)
    draft = _load_draft(json_file, template, settings)
    if draft is None:
        _fail("请用 --json 或 --template 指定草稿")
    if not at and not cron:
        _fail("必须提供 --at 或 --cron")
    store = Store(settings.db_path)
    run_at = None
    if at:
        try:
            run_at = datetime.fromisoformat(at)
        except ValueError:
            _fail(f"--at 不是合法 ISO 时间：{at}")
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
    next_run = run_at
    if cron:
        from .scheduler import next_cron_time

        try:
            next_run = next_cron_time(cron)
        except ValueError as exc:
            _fail(str(exc))
    sid = store.add_schedule(draft, chat_id=chat, name=name, run_at=run_at, cron=cron, next_run_at=next_run)
    console.print(f"[green]✔ 已创建计划任务[/green] id={sid} 下次运行：{next_run}")


@schedule_app.command("list")
def schedule_list(
    status: str | None = typer.Option(None, "--status"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """列出计划任务。"""
    settings = _settings(None, None, db)
    rows = Store(settings.db_path).list_schedules(status=status)
    table = Table(title="计划任务")
    for col in ("ID", "名称", "目标", "下次运行", "状态", "失败次数"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["id"], r.get("name") or "-", r["chat_id"], r.get("next_run_at") or "-",
            r["status"], str(r.get("attempts") or 0),
        )
    console.print(table)


@schedule_app.command("cancel")
def schedule_cancel(
    schedule_id: str = typer.Argument(...),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """取消计划任务。"""
    settings = _settings(None, None, db)
    if Store(settings.db_path).cancel_schedule(schedule_id):
        console.print(f"[green]✔ 已取消 {schedule_id}[/green]")
    else:
        _fail(f"未找到待执行的计划任务：{schedule_id}")


@app.command("run-scheduler")
def run_scheduler_cmd(
    poll: float = typer.Option(5.0, "--poll", help="轮询间隔（秒）"),
    once: bool = typer.Option(False, "--once", help="只跑一轮后退出（适合外部 cron）"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
    db: Path | None = typer.Option(None, "--db"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """启动计划任务执行器（常驻进程）。"""
    settings = _common_options(token, api_base, db, False, verbose)
    from .scheduler import SchedulerRunner

    async def run() -> None:
        async with TelegramClient(settings=settings) as client:
            runner = SchedulerRunner(PostService(client, settings=settings), poll_interval=poll)
            if once:
                count = await runner.run_once()
                console.print(f"本轮处理 {count} 个任务")
            else:
                await runner.run_forever()

    try:
        _run(run())
    except KeyboardInterrupt:  # pragma: no cover - 人工中断
        console.print("已停止")


@webhook_app.command("info")
def webhook_info(
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
) -> None:
    """查看机器人当前是否挂着 webhook（webhook 会让 getUpdates 不可用）。"""
    settings = _settings(token, api_base)

    async def run() -> dict:
        async with TelegramClient(settings=settings) as client:
            return await client.get_webhook_info()

    try:
        info = _run(run())
    except TelemsgError as exc:
        _fail(str(exc))

    url = info.get("url") or ""
    if not url:
        console.print(
            Panel.fit(
                "没有设置 webhook。\n" "可以用 `telemsg chats` 从历史更新里发现 chat_id。",
                title="webhook 状态",
                border_style="green",
            )
        )
        return
    host = url.split("//")[-1].split("/")[0]
    console.print(
        Panel.fit(
            f"地址主机：{host}\n"
            f"路径：{'/' + url.split('/')[3] if len(url.split('/')) > 3 else ''}"
            "（已隐藏具体 token，请自行核对）\n"
            f"有 secret_token：{'是' if info.get('secret_token') else '否'}\n"
            f"待处理更新：{info.get('pending_update_count', 0)}\n"
            f"最近错误：{info.get('last_error_message') or '无'}\n\n"
            "[yellow]注意：只要 webhook 还在，getUpdates 就会返回 409。[/yellow]\n"
            "如果这是别的服务在用，请不要删除，否则那个服务会收不到消息。",
            title="webhook 状态",
            border_style="yellow",
        )
    )


@webhook_app.command("delete")
def webhook_delete(
    yes: bool = typer.Option(False, "--yes", help="确认删除（必须显式指定）"),
    drop_pending: bool = typer.Option(False, "--drop-pending", help="同时丢弃待处理更新"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
) -> None:
    """删除 webhook。会让原接收端立刻收不到更新，请先确认没别的服务在用。"""
    if not yes:
        _fail("这是破坏性操作：会让现有接收端失效。确认后请加 --yes")
    settings = _settings(token, api_base)

    async def run() -> bool:
        async with TelegramClient(settings=settings) as client:
            return await client.delete_webhook(drop_pending_updates=drop_pending)

    try:
        ok = _run(run())
    except TelemsgError as exc:
        _fail(str(exc))
    console.print("[green]✔ webhook 已删除[/green]" if ok else "[yellow]删除未生效[/yellow]")


@templates_app.command("list")
def templates_list(db: Path | None = typer.Option(None, "--db")) -> None:
    """列出模板。"""
    settings = _settings(None, None, db)
    rows = Store(settings.db_path).list_templates()
    table = Table(title="模板")
    table.add_column("名称")
    table.add_column("更新时间")
    for r in rows:
        table.add_row(r["name"], r["updated_at"])
    console.print(table)


@templates_app.command("delete")
def templates_delete(
    name: str = typer.Argument(...),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """删除模板。"""
    settings = _settings(None, None, db)
    if Store(settings.db_path).delete_template(name):
        console.print(f"[green]✔ 已删除 {name}[/green]")
    else:
        _fail(f"模板不存在：{name}")


@app.command("history")
def history(
    limit: int = typer.Option(20, "--limit", "-n"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """查看最近的发送记录。"""
    settings = _settings(None, None, db)
    rows = Store(settings.db_path).recent_sends(limit)
    table = Table(title="发送记录")
    for col in ("时间", "目标", "方法", "结果", "message_id"):
        table.add_column(col)
    for r in rows:
        state = "[green]成功[/green]" if r["ok"] else f"[red]{r['error'] or '失败'}[/red]"
        table.add_row(r["created_at"], r["chat_id"] or "-", r["method"] or "-", state, str(r["message_id"] or "-"))
    console.print(table)


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    token: str | None = typer.Option(None, "--token"),
    api_base: str | None = typer.Option(None, "--api-base"),
    db: Path | None = typer.Option(None, "--db"),
    reload: bool = typer.Option(False, "--reload", help="代码热重载（开发用）"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """启动 Web 编辑器（图形界面里拖图片、排按钮、一键发送）。"""
    settings = _common_options(token, api_base, db, False, verbose)
    try:
        import uvicorn
    except ModuleNotFoundError:
        _fail("未安装 Web 依赖，请执行：pip install 'telemsg[web]'")
    from .web.app import create_app

    application = create_app(settings)
    console.print(
        Panel.fit(
            f"Web 编辑器： http://{host or settings.ui_host}:{port or settings.ui_port}\n"
            f"数据库： {settings.db_path}\n"
            f"Token： {mask_token(settings.resolved_token)}"
            + ("\n[yellow]已开启基础认证（用户名 telemsg）[/yellow]" if settings.ui_password else ""),
            title="TeleMsgCreator",
            border_style="cyan",
        )
    )
    uvicorn.run(
        application,
        host=host or settings.ui_host,
        port=port or settings.ui_port,
        reload=reload,
        log_level="debug" if verbose else "info",
    )


@app.command("schema")
def schema() -> None:
    """打印草稿 JSON Schema（方便写模板/外部程序对接）。"""
    console.print_json(json.dumps(Draft.model_json_schema(), ensure_ascii=False))


@app.command("example")
def example() -> None:
    """打印一个可直接使用的草稿 JSON 示例。"""
    sample = Draft(
        name="示例：新品上线",
        chat_id="@your_channel",
        text="<b>秋季新品</b> 来了\n点击下方按钮查看详情👇",
        parse_mode="HTML",
        media=Media(kind="photo", source="https://picsum.photos/1200/800", caption=None),
        keyboard=Keyboard.from_spec(
            "🛒 立即购买|https://example.com/buy\n"
            "📄 产品文档|https://example.com/docs\n"
            "📋 复制优惠码|copy:TELEMSG2026\n"
            "💬 联系我们|https://t.me/your_support"
        ),
    )
    console.print_json(sample.model_dump_json(exclude_none=True))


def main() -> None:  # pragma: no cover - 由 console_scripts 调用
    try:
        app()
    except TelemsgError as exc:
        err_console.print(f"✖ {exc}")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
