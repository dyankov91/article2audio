"""Pipeline orchestration — runs the full article-to-audio conversion."""

import configparser
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from extractor import extract_from_url, extract_from_file, is_x_url
from cleaner import clean_for_audio, llm_clean_for_audio
from summarizer import get_summary
from chunker import chunk_text
from tts import generate_audio_chunks, DEFAULT_VOICE, DEFAULT_SPEED, DEFAULT_WORKERS, VOICES
from assembler import concat_to_m4b, build_transcript_vtt
from intro import generate_intro, get_intro_duration
from publisher import publish_episode, find_existing_episode

_CONFIG_PATH = os.path.expanduser("~/.config/a2pod/config")

OUTPUT_DIR = Path.home() / "A2Pod"


def sanitize_filename(title: str) -> str:
    """Create a safe filename from title."""
    clean = re.sub(r"[^\w\s-]", "", title)
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean[:80] or "article"


def _load_podcast_name() -> str:
    """Read [podcast] name from config."""
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH)
    return cfg.get("podcast", "name", fallback="A2Pod")


def _title_from_text(text: str) -> str:
    """Derive a title from the first line or first few words of raw text."""
    first_line = text.strip().split("\n", 1)[0].strip()
    if len(first_line) <= 100:
        return first_line
    words = first_line.split()[:8]
    return " ".join(words) + "..."


def run_pipeline(
    url: str | None = None,
    file_path: str | None = None,
    text: str | None = None,
    title: str | None = None,
    voice: str | None = None,
    speed: float = DEFAULT_SPEED,
    model: str | None = None,
    no_summary: bool = False,
    no_intro: bool = False,
    output: str | None = None,
    force: bool = False,
    workers: int = DEFAULT_WORKERS,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the full article-to-audio pipeline.

    Returns dict with output_path, vtt_path, title, size_mb, and optionally feed_url.
    """
    if voice is None:
        from tts import get_voice_info
        voice, _ = get_voice_info()

    if model is None:
        from llm import DEFAULT_MODEL
        model = DEFAULT_MODEL

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Check for existing episode in the podcast feed
    if not force and not file_path and not text and url:
        existing = find_existing_episode(url)
        if existing:
            progress("Already processed — returning existing episode.")
            return existing

    # Extract text
    source_url = None
    if text:
        progress("Processing text...")
        resolved_title = title or _title_from_text(text)
    elif file_path:
        progress("Extracting text from file...")
        text = extract_from_file(file_path)
        resolved_title = title or Path(file_path).stem
    else:
        source_url = url
        progress("Fetching article...")
        text, auto_title = extract_from_url(url)
        resolved_title = title or auto_title or "Untitled Article"
    progress("Text extracted.")

    # Clean text for audio (regex pass first, then LLM pass)
    progress("Cleaning text...")
    text = clean_for_audio(text)

    # For cloud providers, run summary generation in parallel with LLM cleaning
    # (summary only needs the regex-cleaned text, not the LLM-cleaned version)
    summary = None
    from llm import _provider
    if not no_summary and _provider != "ollama":
        from concurrent.futures import ThreadPoolExecutor
        progress("Generating summary...")
        with ThreadPoolExecutor(max_workers=2) as pool:
            clean_future = pool.submit(llm_clean_for_audio, text, model, progress)
            summary_future = pool.submit(get_summary, text, resolved_title, model)
            text = clean_future.result()
            summary = summary_future.result()
    else:
        text = llm_clean_for_audio(text, model, on_progress=progress)
        if not no_summary:
            progress("Generating summary...")
            summary = get_summary(text, resolved_title, model)
    progress("Text cleaned.")
    progress("Summary done.")

    word_count = len(text.split())
    est_minutes = word_count / 150
    chunks = chunk_text(text)
    progress(f'"{resolved_title}" — {word_count} words, {len(chunks)} chunks, ~{est_minutes:.0f} min audio')

    # Generate audio
    with tempfile.TemporaryDirectory() as tmpdir:
        # When workers >= 2, content TTS runs in child processes via
        # ProcessPoolExecutor, so intro TTS can safely run in the main
        # process in parallel.  When workers <= 1 or only one chunk,
        # content TTS runs in the main process (sequential fallback),
        # so we keep intro sequential to avoid MLX contention.
        parallel_intro = not no_intro and workers >= 2 and len(chunks) > 1

        if parallel_intro:
            from concurrent.futures import ThreadPoolExecutor

            podcast_name = _load_podcast_name()
            progress("Generating episode intro...")

            _progress_lock = threading.Lock()

            def _thread_safe_progress(msg: str) -> None:
                with _progress_lock:
                    progress(msg.strip())

            with ThreadPoolExecutor(max_workers=2) as pool:
                content_future = pool.submit(
                    generate_audio_chunks,
                    chunks, voice, speed, tmpdir,
                    on_progress=_thread_safe_progress,
                    workers=workers,
                )
                intro_future = pool.submit(
                    generate_intro,
                    resolved_title, voice, speed, tmpdir, podcast_name,
                )
                content_wavs = content_future.result()
                intro_wavs = intro_future.result()

            progress("Audio done.")
            intro_offset = get_intro_duration(intro_wavs)
            wav_files = intro_wavs + content_wavs
            progress("Intro done.")
        else:
            content_wavs = generate_audio_chunks(
                chunks, voice, speed, tmpdir,
                on_progress=lambda msg: progress(msg.strip()),
                workers=workers,
            )
            progress("Audio done.")

            intro_offset = 0.0
            if not no_intro:
                progress("Generating episode intro...")
                podcast_name = _load_podcast_name()
                intro_wavs = generate_intro(
                    resolved_title, voice, speed, tmpdir, podcast_name,
                )
                intro_offset = get_intro_duration(intro_wavs)
                wav_files = intro_wavs + content_wavs
                progress("Intro done.")
            else:
                wav_files = content_wavs

        OUTPUT_DIR.mkdir(exist_ok=True)
        filename = sanitize_filename(resolved_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output or str(OUTPUT_DIR / f"{filename}_{timestamp}.m4a")

        progress("Encoding M4A...")
        concat_to_m4b(wav_files, output_path, resolved_title)

        # Build VTT transcript from chunks + content WAV durations
        vtt_path = output_path.replace(".m4a", ".vtt")
        build_transcript_vtt(chunks, content_wavs, vtt_path, intro_offset=intro_offset)

    progress("Encoding done.")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    progress(f"Saved: {output_path} ({size_mb:.1f} MB)")

    result = {
        "output_path": output_path,
        "vtt_path": vtt_path,
        "title": resolved_title,
        "size_mb": size_mb,
        "summary": summary,
    }

    progress("Publishing to podcast feed...")
    voice_name = VOICES.get(voice, (voice,))[0]
    feed_url = publish_episode(output_path, resolved_title, source_url, summary, vtt_path,
                               voice_name=voice_name)
    result["feed_url"] = feed_url
    progress("Publishing done.")
    progress(f"Published. Feed: {feed_url}")

    progress("Done.")
    return result
