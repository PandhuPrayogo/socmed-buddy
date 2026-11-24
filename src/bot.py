from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.analyst import format_digest_message, run_digest_flow, run_item_analysis
from src.collectors import CollectorError, InstagramConnector, TikTokConnector, YouTubeConnector
from src.config import settings
from src.models import CanonicalItem
from src.db import (
    add_target,
    finish_run,
    get_item_by_id,
    get_system_status,
    get_unprocessed_items,
    init_db,
    insert_item,
    list_targets,
    purge_expired_raw_json,
    remove_target,
    start_run,
    update_target_cursor,
)

logger = logging.getLogger(__name__)

# Scheduler state
SCHEDULER_ACTIVE = True


def restricted(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        allowed = settings.telegram_allowed_chat_ids
        chat_id = update.effective_chat.id if update.effective_chat else None
        if allowed and (chat_id is None or chat_id not in allowed):
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "🤖 *Autonomous Social Content Monitoring Agent (Gemini-Powered)*\n\n"
        "Available commands:\n"
        "• `/add <platform> <handle>` - Add monitoring target (`youtube`, `tiktok`, `instagram`)\n"
        "• `/list` - List all active monitoring targets\n"
        "• `/remove <id>` - Remove a monitoring target\n"
        "• `/run [platform]` - Trigger immediate scraping pass\n"
        "• `/digest` - Analyze unprocessed items with Gemini & generate digest\n"
        "• `/why <id>` - View source evidence and citations for an item\n"
        "• `/status` - Check system health, queues, and active LLM models\n"
        "• `/budget` - Monitor API quota and budget usage\n"
        "• `/pause` / `/resume` - Control the automated background scheduler\n\n"
        "Or send raw text directly for quick content analysis via Gemini Flash."
    )
    if update.effective_message:
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


@restricted
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("Format: `/add <youtube|tiktok|instagram> <handle>`", parse_mode=ParseMode.MARKDOWN)
        return

    platform = args[0].lower()
    handle = args[1].strip()

    if platform not in ("youtube", "tiktok", "instagram"):
        await message.reply_text("Platform must be one of: `youtube`, `tiktok`, `instagram`", parse_mode=ParseMode.MARKDOWN)
        return

    await message.reply_text(f"Validating {handle} on {platform}…")

    external_id = None
    try:
        if platform == "youtube":
            if not settings.youtube_api_key:
                await message.reply_text("⚠️ YOUTUBE_API_KEY is not configured in .env.")
                return
            connector = YouTubeConnector(settings.youtube_api_key)
            async with httpx.AsyncClient() as client:
                channel_id, uploads_id, title = await connector.resolve_channel(client, handle)
                external_id = uploads_id
                handle = f"@{handle.lstrip('@')}"
        elif platform == "tiktok":
            if not settings.apify_token:
                await message.reply_text("⚠️ APIFY_TOKEN is not configured in .env.")
                return
            connector = TikTokConnector(settings.apify_token)
            async with httpx.AsyncClient() as client:
                valid = await connector.validate_target(client, handle)
                if not valid:
                    await message.reply_text(f"TikTok account '{handle}' is invalid.")
                    return
        elif platform == "instagram":
            if not settings.meta_access_token or not settings.ig_user_id:
                await message.reply_text("⚠️ META_ACCESS_TOKEN or IG_USER_ID is not configured in .env.")
                return
            connector = InstagramConnector(settings.meta_access_token, settings.ig_user_id)
            async with httpx.AsyncClient() as client:
                valid = await connector.validate_target(client, handle)
                if not valid:
                    await message.reply_text(
                        f"Instagram account '{handle}' was not found or is not a Business/Creator account."
                    )
                    return

        target_id = add_target(
            db_path=settings.db_path,
            platform=platform,
            handle=handle,
            external_id=external_id,
        )
        await message.reply_text(f"✅ Target #{target_id} ({platform}: {handle}) registered successfully.")
    except Exception as e:
        logger.error(f"Error adding target: {e}")
        await message.reply_text(f"Failed to add target: {str(e)}")


@restricted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    targets = list_targets(settings.db_path)
    if not targets:
        await message.reply_text("No targets registered yet. Use `/add <platform> <handle>`.")
        return

    lines = ["📋 *Monitoring Targets:*\n"]
    for t in targets:
        status_emoji = "🟢" if t["status"] == "active" else "⚪"
        last_check = t["last_checked_at"][:16] if t["last_checked_at"] else "never"
        lines.append(
            f"{status_emoji} *#{t['id']} [{t['platform'].capitalize()}]* `{t['handle']}`\n"
            f"   Items: {t['items_count']} | Last checked: {last_check}"
        )

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@restricted
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await message.reply_text("Format: `/remove <id>`")
        return

    target_id = int(args[0])
    success = remove_target(settings.db_path, target_id)
    if success:
        await message.reply_text(f"✅ Target #{target_id} disabled from monitoring (history preserved).")
    else:
        await message.reply_text(f"Target #{target_id} not found.")


@restricted
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global SCHEDULER_ACTIVE
    SCHEDULER_ACTIVE = False
    if update.effective_message:
        await update.effective_message.reply_text("⏸️ Automated scheduler paused.")


@restricted
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global SCHEDULER_ACTIVE
    SCHEDULER_ACTIVE = True
    if update.effective_message:
        await update.effective_message.reply_text("▶️ Automated scheduler resumed.")


async def execute_collection(platform_filter: str | None = None) -> tuple[int, float, list[str]]:
    """Runs a collection pass across all active targets."""
    targets = [t for t in list_targets(settings.db_path) if t["status"] == "active"]
    if platform_filter:
        targets = [t for t in targets if t["platform"] == platform_filter]

    total_new = 0
    total_cost = 0.0
    errors: list[str] = []

    for t in targets:
        run_id = start_run(settings.db_path, "collect", t["platform"], t["id"])
        try:
            items = []
            new_cursor = t["cursor"]
            cost = 0.0

            if t["platform"] == "youtube":
                if not settings.youtube_api_key:
                    raise CollectorError("configMissing", "YOUTUBE_API_KEY is not configured in .env")
                yc = YouTubeConnector(settings.youtube_api_key)
                items, new_cursor, cost = await yc.collect(
                    target_id=t["id"],
                    handle=t["handle"],
                    external_id=t["external_id"],
                    cursor=t["cursor"],
                )
            elif t["platform"] == "tiktok":
                if not settings.apify_token:
                    raise CollectorError("configMissing", "APIFY_TOKEN is not configured in .env")
                tc = TikTokConnector(settings.apify_token)
                items, new_cursor, cost = await tc.collect(
                    target_id=t["id"],
                    handle=t["handle"],
                    cursor=t["cursor"],
                )
            elif t["platform"] == "instagram":
                if not settings.meta_access_token or not settings.ig_user_id:
                    raise CollectorError("configMissing", "META_ACCESS_TOKEN or IG_USER_ID is not configured in .env")
                ic = InstagramConnector(settings.meta_access_token, settings.ig_user_id)
                items, new_cursor, cost = await ic.collect(
                    target_id=t["id"],
                    username=t["handle"],
                    cursor=t["cursor"],
                )

            new_count = 0
            for item in items:
                _, is_new = insert_item(settings.db_path, item)
                if is_new:
                    new_count += 1

            now_iso = datetime.now(timezone.utc).isoformat()
            if new_cursor:
                update_target_cursor(settings.db_path, t["id"], new_cursor, now_iso)

            finish_run(settings.db_path, run_id, "ok", items_new=new_count, cost_units=cost)
            total_new += new_count
            total_cost += cost

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Collection failed for target {t['id']}: {err_msg}")
            finish_run(
                settings.db_path,
                run_id,
                "failed",
                error_class=type(e).__name__,
                detail=err_msg,
            )
            errors.append(f"{t['platform']}:{t['handle']} -> {err_msg}")

    return total_new, total_cost, errors


@restricted
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    args = context.args or []
    platform = args[0].lower() if args else None

    await message.reply_text("⏳ Scraping latest data across target platforms…")
    total_new, cost, errors = await execute_collection(platform)

    report = [f"✅ Done: *{total_new}* new items collected."]
    if cost > 0:
        report.append(f"Apify quota cost: ${cost:.4f}")
    if errors:
        report.append("\n⚠️ *Warnings:*")
        report.extend([f"• {e}" for e in errors[:5]])

    await message.reply_text("\n".join(report), parse_mode=ParseMode.MARKDOWN)


@restricted
async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    unprocessed = get_unprocessed_items(settings.db_path, limit=20)
    if not unprocessed:
        await message.reply_text("All content has been analyzed. No new items in queue.")
        return

    if not settings.gemini_api_key and not settings.openai_api_key:
        await message.reply_text("⚠️ GEMINI_API_KEY is not configured in .env.")
        return

    await message.reply_text(
        f"🧠 Analyzing {len(unprocessed)} items using "
        f"Gemini Flash (extraction) & Gemini Pro (synthesis)…"
    )
    run_id = start_run(settings.db_path, "analyze")
    try:
        digest, processed_ids = run_digest_flow(settings.db_path, unprocessed)
        finish_run(settings.db_path, run_id, "ok", items_new=len(processed_ids))
        chunks = format_digest_message(digest, settings.db_path)
        for chunk in chunks:
            await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Digest failed: {e}")
        finish_run(settings.db_path, run_id, "failed", error_class=type(e).__name__, detail=str(e))
        await message.reply_text(f"❌ Digest analysis failed: {str(e)}")


@restricted
async def cmd_why(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await message.reply_text("Format: `/why <item_id>`")
        return

    item_id = int(args[0])
    item = get_item_by_id(settings.db_path, item_id)
    if not item:
        await message.reply_text(f"Item #{item_id} not found.")
        return

    metrics = json.loads(item["metrics_json"]) if item["metrics_json"] else {}
    excerpt = (item["text"] or "").strip()[:500]
    if len(item["text"] or "") > 500:
        excerpt += "…"

    response = (
        f"🔍 *Source Evidence for Item #{item['id']}*\n"
        f"• *Platform:* {item['platform'].capitalize()}\n"
        f"• *Author:* `{item['author']}`\n"
        f"• *Published:* {item['published_at'] or 'N/A'}\n"
        f"• *Source Link:* [Open Content]({item['url']})\n"
        f"• *Metrics:* Views: {metrics.get('views', 0):,} | Likes: {metrics.get('likes', 0):,} | Comments: {metrics.get('comments', 0):,}\n\n"
        f"*Original Text Excerpt:*\n_{excerpt}_"
    )
    await message.reply_text(response, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


@restricted
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    status = get_system_status(settings.db_path)
    sched_label = "Active (30m)" if SCHEDULER_ACTIVE else "Paused"

    last_error_str = "None"
    if status["last_error"]:
        last_error_str = f"{status['last_error']['error_class']}: {status['last_error']['detail'][:80]}"

    text = (
        "📈 *Agentic AI System Status*\n\n"
        f"• *Scheduler:* {sched_label}\n"
        f"• *Normal Model:* `{settings.gemini_normal_model}`\n"
        f"• *Specific Model:* `{settings.gemini_specific_model}`\n"
        f"• *Active Targets:* {status['active_targets']}\n"
        f"• *Total Stored Items:* {status['total_items']}\n"
        f"• *Unprocessed Queue:* {status['queue_depth']}\n"
        f"• *Estimated Cost (MTD):* ${status['month_cost_units']:.4f}\n"
        f"• *Last Error:* `{last_error_str}`"
    )
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@restricted
async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    status = get_system_status(settings.db_path)
    used = status["month_cost_units"]
    ceiling = settings.monthly_budget_usd
    remaining = max(0.0, ceiling - used)

    text = (
        "💰 *Monthly Budget & Quota*\n\n"
        f"• *Budget Ceiling:* ${ceiling:.2f}\n"
        f"• *Current Usage:* ${used:.4f}\n"
        f"• *Remaining Balance:* ${remaining:.4f}\n"
    )
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@restricted
async def handle_direct_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    if not settings.gemini_api_key and not settings.openai_api_key:
        await message.reply_text("⚠️ GEMINI_API_KEY is not configured in .env.")
        return

    await message.reply_text("Analyzing text with Gemini Flash…")
    try:
        temp_item = CanonicalItem(
            target_id=0,
            platform="youtube",
            platform_item_id="manual",
            url="",
            author="User",
            published_at=datetime.now(timezone.utc).isoformat(),
            title="Manual Input",
            text=message.text,
            metrics={},
            raw_json={},
        )
        analysis = run_item_analysis(temp_item)
        res_text = (
            f"📌 *Topic:* {analysis.topic}\n"
            f"📝 *Summary:* {analysis.summary}\n"
            f"🎯 *Format:* {analysis.format or 'N/A'}\n"
            f"💡 *Key Signals:* {', '.join(analysis.notable_signals) if analysis.notable_signals else '-'}"
        )
        await message.reply_text(res_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Text analysis failed: {e}")
        await message.reply_text(f"Analysis failed: {str(e)}")


async def scheduled_collector_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job executed by JobQueue every 30 minutes."""
    if not SCHEDULER_ACTIVE:
        return
    logger.info("Running scheduled collector pass...")
    try:
        await execute_collection()
    except Exception as e:
        logger.error(f"Scheduled collection error: {e}")


async def scheduled_purge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic retention purge: removes raw API JSON older than 30 days."""
    try:
        purged = purge_expired_raw_json(settings.db_path, retention_days=30)
        if purged > 0:
            logger.info(f"Purged raw payloads for {purged} expired items.")
    except Exception as e:
        logger.error(f"Purge job error: {e}")


def create_bot_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in .env")

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("why", cmd_why))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_text))

    # Register scheduler jobs if job_queue is present
    if app.job_queue:
        app.job_queue.run_repeating(scheduled_collector_job, interval=1800, first=60)
        app.job_queue.run_repeating(scheduled_purge_job, interval=86400, first=300)

    return app
