"""Pipeline orchestration — runs the full article-to-audio conversion."""

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from extractor import extract_from_url, extract_from_file, is_x_url
from cleaner import clean_for_audio, llm_clean_for_audio
from summarizer import get_summary
from chunker import chunk_text
from tts import generate_audio_chunks, DEFAULT_VOICE, DEFAULT_SPEED, DEFAULT_WORKERS
from assembler import concat_to_m4b, build_transcript_vtt
from publisher import is_aws_configured, upload_audiobook, get_feed_url, find_existing_episode

OUTPUT_DIR = Path.home() / "A2Pod"


def sanitize_filename(title: str) -> str:
    """Create a safe filename from title."""
    clean = re.sub(r"[^\w\s-]", "", title)
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean[:80] or "article"


def run_pipeline(
    url: str | None = None,
    file_path: str | None = None,
    title: str | None = None,
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
    model: str | None = None,
    no_upload: bool = False,
    no_summary: bool = False,
    output: str | None = None,
    force: bool = False,
    workers: int = DEFAULT_WORKERS,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the full article-to-audio pipeline.

    Returns dict with output_path, vtt_path, title, size_mb, and optionally feed_url.
    """
    if model is None:
        from llm import DEFAULT_MODEL
        model = DEFAULT_MODEL

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Check for existing episode in the podcast feed
    if not force and not file_path and url:
        existing = find_existing_episode(url)
        if existing:
            progress("Already processed — returning existing episode.")
            return existing

    # Check upload capability early
    aws_ready = is_aws_configured()
    will_upload = aws_ready and not no_upload

    # Extract text
    source_url = None
    if file_path:
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

    word_count = len(text.split())
    est_minutes = word_count / 150
    chunks = chunk_text(text)
    progress(f'"{resolved_title}" — {word_count} words, {len(chunks)} chunks, ~{est_minutes:.0f} min audio')

    # Generate audio
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_files = generate_audio_chunks(
            chunks, voice, speed, tmpdir,
            on_progress=lambda msg: progress(msg.strip()),
            workers=workers,
        )

        OUTPUT_DIR.mkdir(exist_ok=True)
        filename = sanitize_filename(resolved_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output or str(OUTPUT_DIR / f"{filename}_{timestamp}.m4a")

        progress("Encoding M4A...")
        concat_to_m4b(wav_files, output_path, resolved_title)

        # Build VTT transcript from chunks + WAV durations
        vtt_path = output_path.replace(".m4a", ".vtt")
        build_transcript_vtt(chunks, wav_files, vtt_path)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    progress(f"Saved: {output_path} ({size_mb:.1f} MB)")

    result = {
        "output_path": output_path,
        "vtt_path": vtt_path,
        "title": resolved_title,
        "size_mb": size_mb,
        "summary": summary,
    }

    # Upload to S3 and update podcast feed
    if will_upload:
        progress("Publishing to podcast feed...")
        audio_url = upload_audiobook(output_path, resolved_title, source_url, summary, vtt_path)
        feed_url = get_feed_url()
        result["feed_url"] = feed_url
        result["audio_url"] = audio_url
        progress(f"Published. Feed: {feed_url}")

    progress("Done.")
    return result
