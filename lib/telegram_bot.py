"""Telegram bot for a2pod — accepts URLs, runs pipeline, delivers audio."""

import asyncio
import configparser
import logging
import re
from functools import partial
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from errors import PipelineError
from pipeline import run_pipeline
from publisher import get_feed_url

_CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"

logger = logging.getLogger(__name__)

# Map pipeline progress messages to short user-facing status lines.
# Only messages matching a key here produce a status update in Telegram.
_STATUS_MAP = {
    "Fetching article...": "Fetching article...",
    "Extracting text from file...": "Extracting text...",
    "Cleaning text...": "Cleaning text...",
    "Encoding M4A...": "Encoding audio...",
    "Publishing to podcast feed...": "Publishing...",
}


def load_telegram_config() -> tuple[str, set[int]]:
    """Read [telegram] section from config. Returns (bot_token, allowed_user_ids)."""
    cfg = configparser.RawConfigParser()
    cfg.read(_CONFIG_PATH)

    token = cfg.get("telegram", "bot_token", fallback="").strip()
    if not token:
        raise SystemExit(
            f"Telegram bot token not configured.\n"
            f"Add it to {_CONFIG_PATH}:\n\n"
            f"[telegram]\nbot_token = YOUR_BOT_TOKEN\n"
            f"allowed_users = YOUR_USER_ID"
        )

    raw_users = cfg.get("telegram", "allowed_users", fallback="").strip()
    if not raw_users:
        raise SystemExit(
            f"No allowed users configured.\n"
            f"Add allowed_users to [telegram] section in {_CONFIG_PATH}:\n\n"
            f"allowed_users = 123456789,987654321"
        )

    allowed = set()
    for uid in raw_users.split(","):
        uid = uid.strip()
        if uid.isdigit():
            allowed.add(int(uid))

    if not allowed:
        raise SystemExit("No valid user IDs in allowed_users config.")

    return token, allowed


def _is_authorized(user_id: int, allowed: set[int]) -> bool:
    return user_id in allowed


async def _reject_unauthorized(update: Update) -> None:
    await update.message.reply_text("Sorry, you are not authorized to use this bot.")


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    await update.message.reply_text(
        "Send me an article URL and I'll convert it to audio for the podcast feed.\n\n"
        "/feed — get the podcast feed URL\n"
        "/help — more info"
    )


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    await update.message.reply_text(
        "Send me any article URL and I'll:\n"
        "1. Extract and clean the text\n"
        "2. Generate speech with Kokoro TTS\n"
        "3. Publish to the podcast feed\n\n"
        "Supported: web articles, X/Twitter posts and articles.\n\n"
        "/feed — get the podcast feed URL"
    )


async def _feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    feed_url = get_feed_url()
    if feed_url:
        await update.message.reply_text(feed_url)
    else:
        await update.message.reply_text("Podcast feed not configured. Set up AWS in install.sh first.")


def _run_pipeline_sync(url: str, loop: asyncio.AbstractEventLoop, chat_id: int,
                        status_message_id: int, bot, status_lines: list[str]) -> dict:
    """Run the sync pipeline in a thread, bridging progress back to async."""

    def on_progress(msg: str) -> None:
        # Check for TTS chunk progress
        chunk_match = re.match(r"Generating audio \[(\d+)/(\d+)\]", msg)
        if chunk_match:
            i, total = chunk_match.group(1), chunk_match.group(2)
            tts_line = f"Generating audio [{i}/{total}]..."
            # Replace existing TTS line or append
            for idx, line in enumerate(status_lines):
                if line.startswith("Generating audio"):
                    status_lines[idx] = tts_line
                    break
            else:
                status_lines.append(tts_line)
        elif msg in _STATUS_MAP:
            status_lines.append(_STATUS_MAP[msg])
        else:
            return  # skip noisy messages

        text = "\n".join(status_lines)
        asyncio.run_coroutine_threadsafe(
            bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=text),
            loop,
        )

    return run_pipeline(url=url, no_upload=False, on_progress=on_progress)


async def _handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)

    if not _is_authorized(user_id, allowed):
        logger.warning("Unauthorized access from @%s (id=%d)", username, user_id)
        return await _reject_unauthorized(update)

    # Per-user serialization: prevent concurrent jobs
    active_jobs: set = context.bot_data.setdefault("active_jobs", set())
    if user_id in active_jobs:
        await update.message.reply_text("You already have a job in progress. Please wait for it to finish.")
        return

    text = update.message.text.strip()

    # Extract URL from message
    url_match = re.search(r"https?://\S+", text)
    if not url_match:
        await update.message.reply_text("Please send a valid URL starting with http:// or https://")
        return

    url = url_match.group(0)
    active_jobs.add(user_id)
    logger.info("Job started for @%s: %s", username, url)

    # Send initial status message
    status_lines = ["Starting..."]
    status_msg = await update.message.reply_text(status_lines[0])

    loop = asyncio.get_running_loop()

    try:
        result = await loop.run_in_executor(
            None,
            partial(
                _run_pipeline_sync,
                url=url,
                loop=loop,
                chat_id=update.effective_chat.id,
                status_message_id=status_msg.message_id,
                bot=context.bot,
                status_lines=status_lines,
            ),
        )

        title = result["title"]
        size_mb = result["size_mb"]
        summary = result.get("summary") or ""
        audio_url = result.get("audio_url")

        lines = [f"*{title}*"]
        if summary:
            lines.append(f"\n{summary}")
        if audio_url:
            lines.append(f"\n[Listen]({audio_url})")

        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
        logger.info("Job done for @%s: %s (%.1f MB)", username, title, size_mb)

    except PipelineError as e:
        logger.error("Pipeline error for @%s on %s: %s", username, url, e)
        await status_msg.edit_text(f"Error: {e}")
    except Exception:
        logger.exception("Unexpected error for @%s on %s", username, url)
        await status_msg.edit_text("An unexpected error occurred. Check the bot logs.")
    finally:
        active_jobs.discard(user_id)


def run_bot() -> None:
    """Start the Telegram bot with long-polling."""
    token, allowed = load_telegram_config()

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    # Silence noisy HTTP request logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = Application.builder().token(token).build()
    app.bot_data["allowed_users"] = allowed

    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("feed", _feed))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_url))

    logger.info("Bot started (allowed users: %s)", allowed)
    app.run_polling()
