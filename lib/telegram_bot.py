"""Telegram bot for a2pod — accepts URLs, runs pipeline, delivers audio."""

import asyncio
import configparser
import logging
import os
import platform
import re
import signal
import subprocess
import tempfile
import time
from functools import partial
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters,
)

from errors import PipelineError
from llm import get_provider_info, get_available_providers, set_provider, get_ollama_models
from pipeline import run_pipeline
from publisher import get_feed_url, find_episode, list_episodes, delete_episode, delete_all_episodes
from tts import (
    get_voice_info, get_available_voices, set_voice, VOICES,
    get_workers, get_recommended_workers, set_workers, WORKER_OPTIONS,
    get_speed, set_speed, SPEED_OPTIONS,
)

_CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
_RESTART_MARKER = Path.home() / ".config" / "a2pod" / ".restart_chat_id"

logger = logging.getLogger(__name__)


def _get_git_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


_GIT_VERSION = _get_git_version()

# Map pipeline progress messages to short user-facing status lines.
# Only messages matching a key here produce a status update in Telegram.
_PIPELINE_STEPS = [
    "Cleaning text",
    "Generating summary",
    "Generating intro",
    "Generating audio",
    "Encoding audio",
    "Publishing",
]

_W = "⬜"  # waiting
_A = "⏳"  # active
_D = "✅"  # done

# Maps pipeline "starting" messages → step label to mark active
_START_MAP = {
    "Fetching article...": "Fetching article",
    "Processing text...": "Processing text",
    "Extracting text from file...": "Extracting text",
    "Cleaning text...": "Cleaning text",
    "Generating summary...": "Generating summary",
    "Generating episode intro...": "Generating intro",
    "Encoding M4A...": "Encoding audio",
    "Publishing to podcast feed...": "Publishing",
}

# Maps pipeline "done" messages → step labels to mark done
_DONE_MAP = {
    "Text extracted.": ["Fetching article", "Processing text", "Extracting text"],
    "Text cleaned.": ["Cleaning text"],
    "Summary done.": ["Generating summary"],
    "Audio done.": ["Generating audio"],
    "Intro done.": ["Generating intro"],
    "Encoding done.": ["Encoding audio"],
    "Publishing done.": ["Publishing"],
}


def _init_status(first_step: str) -> list[str]:
    """Build the full status display with all steps visible from the start."""
    lines = [f"{_A} {first_step}"]
    lines.extend(f"{_W} {s}" for s in _PIPELINE_STEPS)
    return lines


def _update_step(status_lines: list[str], label: str, emoji: str, suffix: str = "") -> None:
    """Find a step by label and update its emoji and optional suffix."""
    for idx, line in enumerate(status_lines):
        # Lines are like "⬜ Cleaning text" or "⏳ Generating audio [2/8]"
        # Strip the emoji prefix and any suffix to match the label
        bare = line[2:].split(" [")[0].split("...")[0]
        if bare == label:
            status_lines[idx] = f"{emoji} {label}{suffix}"
            return


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
        "Send me an article URL, upload a .txt file, or paste text "
        "and I'll convert it to audio for the podcast feed.\n\n"
        "/model — show or switch LLM provider\n"
        "/voice — show or switch TTS voice\n"
        "/speed — show or set speech speed\n"
        "/workers — show or set TTS worker count\n"
        "/feed — get the podcast feed URL\n"
        "/status — bot status and debug info\n"
        "/delete <title or URL> — remove an episode\n"
        "/deleteall — remove all episodes\n"
        "/restart — restart the bot\n"
        "/help — more info"
    )


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    await update.message.reply_text(
        "Send me any article URL, upload a .txt file, or paste text and I'll:\n"
        "1. Extract and clean the text\n"
        "2. Generate speech with Kokoro TTS\n"
        "3. Publish to the podcast feed\n\n"
        "Supported: web articles, X/Twitter posts, .txt files, pasted text.\n\n"
        "/model — show/switch LLM provider and model\n"
        "/model <provider> — switch provider (ollama, openai, anthropic, gemini)\n"
        "/model <provider> <model> — switch provider and model\n"
        "/voice — show/switch TTS voice\n"
        "/voice <voice_id> — switch voice (e.g. af_heart, am_adam)\n"
        "/speed — show/set speech speed\n"
        "/speed <value> — set speed (0.8, 0.9, 1.0, 1.1, 1.2, 1.3, or 1.5)\n"
        "/workers — show/set TTS worker count\n"
        "/workers <count> — set workers (1, 2, 3, 4, 6, or 8)\n"
        "/feed — get the podcast feed URL\n"
        "/status — bot status and debug info\n"
        "/delete <title or URL> — remove an episode\n"
        "/deleteall — remove all episodes"
    )


async def _restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    logger.info("Restart requested by @%s", update.effective_user.username or update.effective_user.id)
    await update.message.reply_text("Restarting...")
    _RESTART_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _RESTART_MARKER.write_text(str(update.effective_chat.id))
    os.kill(os.getpid(), signal.SIGTERM)


async def _feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)
    await update.message.reply_text(get_feed_url())


async def _model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or switch the LLM provider and model."""
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    args = context.args

    if not args:
        provider, model = get_provider_info()
        available = get_available_providers()

        buttons = []
        for p in sorted(available):
            label = f"{'* ' if p == provider else ''}{p} ({available[p]})"
            buttons.append([InlineKeyboardButton(label, callback_data=f"model_{p}")])

        # If already on ollama, also show installed models for quick switching
        if provider == "ollama":
            ollama_models = get_ollama_models()
            if len(ollama_models) > 1:
                for m in ollama_models:
                    label = f"{'✓ ' if m == model else ''}{m}"
                    buttons.append([InlineKeyboardButton(label, callback_data=f"ollama_model_{m}")])

        await update.message.reply_text(
            f"Current LLM: *{provider}* / `{model}`",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
        return

    new_provider = args[0].lower()
    new_model = args[1] if len(args) > 1 else None

    try:
        provider, model = set_provider(new_provider, new_model)
        await update.message.reply_text(f"Switched to *{provider}* / `{model}`", parse_mode="Markdown")
        logger.info("LLM switched to %s / %s by @%s", provider, model,
                     update.effective_user.username or update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")


async def _voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or switch the TTS voice."""
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    args = context.args

    if not args:
        voice_id, _ = get_voice_info()
        available = get_available_voices()

        # Group by gender, 2 buttons per row
        female = [(vid, name) for vid, (name, g) in sorted(available.items()) if g == "Female"]
        male = [(vid, name) for vid, (name, g) in sorted(available.items()) if g == "Male"]

        buttons = []
        for group in (female, male):
            for i in range(0, len(group), 2):
                row = []
                for vid, name in group[i:i+2]:
                    label = f"{'* ' if vid == voice_id else ''}{name} ({vid})"
                    row.append(InlineKeyboardButton(label, callback_data=f"voice_{vid}"))
                buttons.append(row)

        friendly = VOICES[voice_id][0]
        await update.message.reply_text(
            f"Current voice: *{friendly}* (`{voice_id}`)",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
        return

    new_voice = args[0].lower()
    try:
        vid, friendly = set_voice(new_voice)
        await update.message.reply_text(f"Switched to voice *{friendly}* (`{vid}`)", parse_mode="Markdown")
        logger.info("Voice switched to %s by @%s", vid,
                     update.effective_user.username or update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")


async def _workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or set TTS worker count."""
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    args = context.args

    if not args:
        current = get_workers()
        recommended = get_recommended_workers()
        cores = os.cpu_count() or 0

        buttons = []
        row = []
        for w in WORKER_OPTIONS:
            label = str(w)
            if w == current:
                label = f"* {label}"
            if w == recommended:
                label += " (rec)"
            row.append(InlineKeyboardButton(label, callback_data=f"workers_{w}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await update.message.reply_text(
            f"TTS workers: *{current}* (recommended: {recommended} for {cores} cores)\n\n"
            f"More workers = faster generation but higher memory/GPU usage.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
        return

    try:
        count = int(args[0])
        set_workers(count)
        await update.message.reply_text(f"Workers set to *{count}*", parse_mode="Markdown")
        logger.info("Workers set to %d by @%s", count,
                     update.effective_user.username or update.effective_user.id)
    except (ValueError, TypeError) as e:
        await update.message.reply_text(f"Error: {e}")


async def _speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or set TTS speech speed."""
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    args = context.args

    if not args:
        current = get_speed()

        buttons = []
        row = []
        for s in SPEED_OPTIONS:
            label = f"{s}x"
            if s == current:
                label = f"* {label}"
            if s == 1.0:
                label += " (default)"
            row.append(InlineKeyboardButton(label, callback_data=f"speed_{s}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await update.message.reply_text(
            f"Speech speed: *{current}x*\n\n"
            f"Higher = faster narration. 1.0x is normal.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
        return

    try:
        speed = float(args[0])
        set_speed(speed)
        await update.message.reply_text(f"Speed set to *{speed}x*", parse_mode="Markdown")
        logger.info("Speed set to %s by @%s", speed,
                     update.effective_user.username or update.effective_user.id)
    except (ValueError, TypeError) as e:
        await update.message.reply_text(f"Error: {e}")


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    started_at = context.bot_data.get("started_at", 0)
    uptime_secs = int(time.time() - started_at) if started_at else 0
    days, rem = divmod(uptime_secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    uptime_str = " ".join(parts)

    started_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at)) if started_at else "unknown"

    provider, model = get_provider_info()
    voice_id, _ = get_voice_info()
    voice_name = VOICES.get(voice_id, (voice_id,))[0]
    active_jobs: set = context.bot_data.get("active_jobs", set())

    lines = [
        "Status: Running",
        f"Version: {_GIT_VERSION}",
        f"Uptime: {uptime_str}",
        f"Started: {started_str}",
        f"LLM: {provider} / {model}",
        f"Voice: {voice_name} ({voice_id})",
        f"Speed: {get_speed()}x",
        f"Workers: {get_workers()}",
        f"Active jobs: {len(active_jobs)}",
        f"Python: {platform.python_version()} ({platform.machine()})",
    ]
    await update.message.reply_text("\n".join(lines))


def _escape_markdown(text: str) -> str:
    """Escape Telegram Markdown V1 special characters."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_result(result: dict, elapsed: float) -> str:
    """Format a pipeline result dict into a Markdown message."""
    cached = result.get("cached", False)
    title = _escape_markdown(result["title"])
    summary = _escape_markdown(result.get("summary") or "")
    audio_url = result.get("audio_url")

    lines = []
    if cached:
        lines.append("Already processed:")
    lines.append(f"*{title}*")
    if summary:
        lines.append(f"\n{summary}")
    if audio_url:
        lines.append(f"\n[Listen]({audio_url})")
    if not cached:
        duration_secs = result.get("duration_secs", 0)
        meta = []
        if duration_secs:
            d_mins, d_secs = divmod(duration_secs, 60)
            meta.append(f"Listening time: {d_mins}m {d_secs:02d}s")
        b_mins, b_secs = divmod(int(elapsed), 60)
        meta.append(f"Build time: {b_mins}m {b_secs}s" if b_mins else f"Build time: {b_secs}s")
        lines.append(f"\n_{' · '.join(meta)}_")
    return "\n".join(lines)


def _run_pipeline_sync(loop: asyncio.AbstractEventLoop, chat_id: int,
                        status_message_id: int, bot, status_lines: list[str],
                        url=None, file_path=None, text=None, title=None) -> dict:
    """Run the sync pipeline in a thread, bridging progress back to async."""
    last_future = None

    def on_progress(msg: str) -> None:
        nonlocal last_future
        msg = msg.strip()
        # Chunk-style progress: "Cleaning text [2/5]", "Generating audio [3/8]", "Chunk [2/4] done"
        tts_start = re.match(r"Generating audio for (\d+) chunks", msg)
        chunk_match = not tts_start and re.match(r"(Cleaning text|Generating audio) \[(\d+)/(\d+)\]", msg)
        tts_match = not chunk_match and not tts_start and re.match(r"Chunk \[(\d+)/(\d+)\]", msg)
        if tts_start:
            _update_step(status_lines, "Generating audio", _A, f" [0/{tts_start.group(1)}]")
        elif chunk_match:
            label, i, total = chunk_match.group(1), chunk_match.group(2), chunk_match.group(3)
            _update_step(status_lines, label, _A, f" [{i}/{total}]")
        elif tts_match:
            i, total = tts_match.group(1), tts_match.group(2)
            _update_step(status_lines, "Generating audio", _A, f" [{i}/{total}]")
        elif msg in _DONE_MAP:
            for label in _DONE_MAP[msg]:
                _update_step(status_lines, label, _D)
        elif msg in _START_MAP:
            _update_step(status_lines, _START_MAP[msg], _A)
        else:
            return  # skip noisy messages

        status_text = "\n".join(status_lines)
        last_future = asyncio.run_coroutine_threadsafe(
            bot.edit_message_text(chat_id=chat_id, message_id=status_message_id, text=status_text),
            loop,
        )

    voice_id, _ = get_voice_info()
    workers = get_workers()
    speed = get_speed()
    result = run_pipeline(
        url=url, file_path=file_path, text=text, title=title,
        voice=voice_id, speed=speed, workers=workers, on_progress=on_progress,
    )

    # Wait for the last progress edit to finish before returning, so the
    # caller's final edit_text (showing the summary) isn't overwritten by a
    # still-pending progress update.
    if last_future:
        try:
            last_future.result(timeout=10)
        except Exception:
            pass

    return result


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        word_count = len(text.split())
        if word_count < 50:
            await update.message.reply_text(
                "Send me a URL, upload a .txt file, or paste a longer text (50+ words) "
                "to generate a podcast episode."
            )
            return
        # Offer to generate from pasted text
        context.user_data["pending_text"] = text
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes", callback_data="text_yes"),
            InlineKeyboardButton("No", callback_data="text_no"),
        ]])
        await update.message.reply_text(
            f"Generate a podcast episode from this text ({word_count} words)?",
            reply_markup=keyboard,
        )
        return

    url = url_match.group(0)
    active_jobs.add(user_id)
    logger.info("Job started for @%s: %s", username, url)

    # Send initial status message with all steps visible
    status_lines = _init_status("Fetching article")
    status_msg = await update.message.reply_text("\n".join(status_lines))

    loop = asyncio.get_running_loop()
    t0 = time.monotonic()

    try:
        result = await loop.run_in_executor(
            None,
            partial(
                _run_pipeline_sync,
                loop=loop,
                chat_id=update.effective_chat.id,
                status_message_id=status_msg.message_id,
                bot=context.bot,
                status_lines=status_lines,
                url=url,
            ),
        )

        elapsed = time.monotonic() - t0
        text = _format_result(result, elapsed)
        try:
            await status_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await status_msg.edit_text(text)
        if result.get("cached"):
            logger.info("Job done for @%s: %s (cached)", username, result["title"])
        else:
            logger.info("Job done for @%s: %s (%.1f MB, %ds)", username, result["title"], result["size_mb"], int(elapsed))

    except PipelineError as e:
        logger.error("Pipeline error for @%s on %s: %s", username, url, e)
        await status_msg.edit_text(f"Error: {e}")
    except Exception:
        logger.exception("Unexpected error for @%s on %s", username, url)
        await status_msg.edit_text("An unexpected error occurred. Check the bot logs.")
    finally:
        active_jobs.discard(user_id)


async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uploaded .txt files — run them through the pipeline."""
    allowed = context.bot_data["allowed_users"]
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)

    if not _is_authorized(user_id, allowed):
        logger.warning("Unauthorized access from @%s (id=%d)", username, user_id)
        return await _reject_unauthorized(update)

    active_jobs: set = context.bot_data.setdefault("active_jobs", set())
    if user_id in active_jobs:
        await update.message.reply_text("You already have a job in progress. Please wait for it to finish.")
        return

    doc = update.message.document
    file_name = doc.file_name or ""
    mime = doc.mime_type or ""

    if not (file_name.lower().endswith(".txt") or mime == "text/plain"):
        await update.message.reply_text("Only .txt files are supported. Please upload a plain text file.")
        return

    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("File too large. Please keep .txt files under 5 MB.")
        return

    active_jobs.add(user_id)
    logger.info("Document job started for @%s: %s", username, file_name)

    status_lines = _init_status("Extracting text")
    status_msg = await update.message.reply_text("\n".join(status_lines))

    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    tmp_path = None

    try:
        # Download file to a temp location
        tg_file = await doc.get_file()
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        os.close(tmp_fd)
        await tg_file.download_to_drive(tmp_path)

        doc_title = Path(file_name).stem if file_name else None

        result = await loop.run_in_executor(
            None,
            partial(
                _run_pipeline_sync,
                loop=loop,
                chat_id=update.effective_chat.id,
                status_message_id=status_msg.message_id,
                bot=context.bot,
                status_lines=status_lines,
                file_path=tmp_path,
                title=doc_title,
            ),
        )

        elapsed = time.monotonic() - t0
        text = _format_result(result, elapsed)
        try:
            await status_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await status_msg.edit_text(text)
        logger.info("Document job done for @%s: %s (%.1f MB, %ds)", username, result["title"], result["size_mb"], int(elapsed))

    except PipelineError as e:
        logger.error("Pipeline error for @%s on document %s: %s", username, file_name, e)
        await status_msg.edit_text(f"Error: {e}")
    except Exception:
        logger.exception("Unexpected error for @%s on document %s", username, file_name)
        await status_msg.edit_text("An unexpected error occurred. Check the bot logs.")
    finally:
        active_jobs.discard(user_id)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def _delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    if not context.args:
        await update.message.reply_text("Usage: /delete <title or URL>")
        return

    query = " ".join(context.args)
    episode = find_episode(query)
    if not episode:
        await update.message.reply_text(f"No episode found matching: {query}")
        return

    context.user_data["pending_delete"] = query
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, delete", callback_data="delete_yes"),
        InlineKeyboardButton("Cancel", callback_data="delete_no"),
    ]])
    await update.message.reply_text(
        f"Delete \"{episode['title']}\"?",
        reply_markup=keyboard,
    )


async def _deleteall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(update.effective_user.id, allowed):
        return await _reject_unauthorized(update)

    episodes = list_episodes()
    if not episodes:
        await update.message.reply_text("No episodes in the feed.")
        return

    count = len(episodes)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, delete everything", callback_data="deleteall_yes"),
        InlineKeyboardButton("Cancel", callback_data="deleteall_no"),
    ]])
    await update.message.reply_text(
        f"Delete all {count} episode(s) and their files?",
        reply_markup=keyboard,
    )


async def _button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    allowed = context.bot_data["allowed_users"]
    if not _is_authorized(query.from_user.id, allowed):
        await query.answer("Not authorized.")
        return

    await query.answer()
    data = query.data

    if data == "delete_yes":
        pending = context.user_data.pop("pending_delete", None)
        if not pending:
            await query.edit_message_text("Nothing to delete (expired).")
            return
        try:
            result = delete_episode(pending)
            await query.edit_message_text(
                f"Deleted \"{result['title']}\" ({result['files_deleted']} file(s) removed)."
            )
        except PipelineError as e:
            await query.edit_message_text(f"Error: {e}")

    elif data == "delete_no":
        context.user_data.pop("pending_delete", None)
        await query.edit_message_text("Cancelled.")

    elif data == "deleteall_yes":
        try:
            result = delete_all_episodes()
            await query.edit_message_text(
                f"Deleted {result['episodes_deleted']} episode(s), "
                f"{result['files_deleted']} file(s) removed."
            )
        except Exception as e:
            await query.edit_message_text(f"Error: {e}")

    elif data == "deleteall_no":
        await query.edit_message_text("Cancelled.")

    elif data == "text_yes":
        pending_text = context.user_data.pop("pending_text", None)
        if not pending_text:
            await query.edit_message_text("Text expired. Please paste it again.")
            return

        user_id = query.from_user.id
        username = query.from_user.username or str(user_id)
        active_jobs: set = context.bot_data.setdefault("active_jobs", set())

        if user_id in active_jobs:
            await query.edit_message_text("You already have a job in progress. Please wait for it to finish.")
            return

        active_jobs.add(user_id)
        logger.info("Text job started for @%s (%d words)", username, len(pending_text.split()))

        status_lines = _init_status("Processing text")
        await query.edit_message_text("\n".join(status_lines))
        status_message_id = query.message.message_id
        chat_id = query.message.chat_id

        loop = asyncio.get_running_loop()
        t0 = time.monotonic()

        try:
            result = await loop.run_in_executor(
                None,
                partial(
                    _run_pipeline_sync,
                    loop=loop,
                    chat_id=chat_id,
                    status_message_id=status_message_id,
                    bot=context.bot,
                    status_lines=status_lines,
                    text=pending_text,
                ),
            )

            elapsed = time.monotonic() - t0
            text = _format_result(result, elapsed)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=status_message_id,
                    text=text, parse_mode="Markdown",
                )
            except Exception:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=status_message_id, text=text,
                )
            logger.info("Text job done for @%s: %s (%.1f MB, %ds)", username, result["title"], result["size_mb"], int(elapsed))

        except PipelineError as e:
            logger.error("Pipeline error for @%s on pasted text: %s", username, e)
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_message_id, text=f"Error: {e}",
            )
        except Exception:
            logger.exception("Unexpected error for @%s on pasted text", username)
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_message_id,
                text="An unexpected error occurred. Check the bot logs.",
            )
        finally:
            active_jobs.discard(user_id)

    elif data == "text_no":
        context.user_data.pop("pending_text", None)
        await query.edit_message_text("Cancelled.")

    elif data.startswith("ollama_model_"):
        new_model = data.removeprefix("ollama_model_")
        try:
            provider, model = set_provider("ollama", new_model)
            await query.edit_message_text(
                f"Switched to *{provider}* / `{model}`", parse_mode="Markdown"
            )
            logger.info("LLM switched to %s / %s by @%s", provider, model,
                         query.from_user.username or query.from_user.id)
        except ValueError as e:
            await query.edit_message_text(f"Error: {e}")

    elif data.startswith("model_"):
        new_provider = data.removeprefix("model_")

        # If switching to ollama and multiple models are installed, show model picker
        if new_provider == "ollama":
            ollama_models = get_ollama_models()
            if len(ollama_models) > 1:
                _, current_model = get_provider_info()
                buttons = []
                for m in ollama_models:
                    label = f"{'✓ ' if m == current_model else ''}{m}"
                    buttons.append([InlineKeyboardButton(label, callback_data=f"ollama_model_{m}")])
                await query.edit_message_text(
                    "Pick an Ollama model:",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

        try:
            provider, model = set_provider(new_provider)
            await query.edit_message_text(
                f"Switched to *{provider}* / `{model}`", parse_mode="Markdown"
            )
            logger.info("LLM switched to %s / %s by @%s", provider, model,
                         query.from_user.username or query.from_user.id)
        except ValueError as e:
            await query.edit_message_text(f"Error: {e}")

    elif data.startswith("voice_"):
        new_voice = data.removeprefix("voice_")
        try:
            vid, friendly = set_voice(new_voice)
            await query.edit_message_text(
                f"Switched to voice *{friendly}* (`{vid}`)", parse_mode="Markdown"
            )
            logger.info("Voice switched to %s by @%s", vid,
                         query.from_user.username or query.from_user.id)
        except ValueError as e:
            await query.edit_message_text(f"Error: {e}")

    elif data.startswith("workers_"):
        try:
            count = int(data.removeprefix("workers_"))
            set_workers(count)
            await query.edit_message_text(f"Workers set to *{count}*", parse_mode="Markdown")
            logger.info("Workers set to %d by @%s", count,
                         query.from_user.username or query.from_user.id)
        except (ValueError, TypeError) as e:
            await query.edit_message_text(f"Error: {e}")

    elif data.startswith("speed_"):
        try:
            speed = float(data.removeprefix("speed_"))
            set_speed(speed)
            await query.edit_message_text(f"Speed set to *{speed}x*", parse_mode="Markdown")
            logger.info("Speed set to %s by @%s", speed,
                         query.from_user.username or query.from_user.id)
        except (ValueError, TypeError) as e:
            await query.edit_message_text(f"Error: {e}")


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

    async def _post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            ("start", "Start the bot"),
            ("model", "Show or switch LLM provider"),
            ("voice", "Show or switch TTS voice"),
            ("speed", "Show or set speech speed"),
            ("workers", "Show or set TTS worker count"),
            ("feed", "Get the podcast feed URL"),
            ("status", "Bot status and debug info"),
            ("delete", "Remove an episode"),
            ("deleteall", "Remove all episodes"),
            ("restart", "Restart the bot"),
            ("help", "How to use this bot"),
        ])
        if _RESTART_MARKER.exists():
            try:
                chat_id = int(_RESTART_MARKER.read_text().strip())
                await application.bot.send_message(chat_id, f"Back online. ({_GIT_VERSION})")
            except Exception:
                logger.warning("Failed to send restart confirmation", exc_info=True)
            finally:
                _RESTART_MARKER.unlink(missing_ok=True)

    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["allowed_users"] = allowed
    app.bot_data["started_at"] = time.time()

    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("feed", _feed))
    app.add_handler(CommandHandler("status", _status))
    app.add_handler(CommandHandler("delete", _delete))
    app.add_handler(CommandHandler("deleteall", _deleteall))
    app.add_handler(CommandHandler("restart", _restart))
    app.add_handler(CommandHandler("model", _model))
    app.add_handler(CommandHandler("voice", _voice))
    app.add_handler(CommandHandler("workers", _workers))
    app.add_handler(CommandHandler("speed", _speed))
    app.add_handler(CallbackQueryHandler(_button_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, _handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    logger.info("Bot started (allowed users: %s)", allowed)
    app.run_polling()
