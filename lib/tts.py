"""TTS generation using MLX Audio (Kokoro)."""

import configparser
import logging
import os
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable

import numpy as np

MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
LANG_CODE = "a"  # English
SAMPLE_RATE = 24000

_CONFIG_PATH = os.path.expanduser("~/.config/a2pod/config")


def _load_tts_config() -> int:
    """Read [tts] from config. Returns workers count."""
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH)
    return cfg.getint("tts", "workers", fallback=2)


DEFAULT_WORKERS = _load_tts_config()


def _default_progress(msg: str) -> None:
    """Default progress callback — prints to stdout."""
    print(msg, end="", flush=True)


def _generate_chunk(args: tuple) -> tuple[int, str | None, float]:
    """Generate a single WAV chunk in a worker process.

    Each worker loads its own model instance. Returns (index, wav_path, duration)
    or (index, None, 0) on failure.
    """
    import soundfile as sf

    idx, chunk, voice, speed, tmpdir, model_id = args

    logging.disable(logging.WARNING)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    from mlx_audio.tts.utils import load_model
    model = load_model(model_id)

    audio_segments = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for result in model.generate(chunk, voice=voice, speed=speed, lang_code=LANG_CODE):
            audio_segments.append(np.array(result.audio))

    if not audio_segments:
        return (idx, None, 0.0)

    audio = np.concatenate(audio_segments)
    duration = len(audio) / SAMPLE_RATE
    wav_path = os.path.join(tmpdir, f"chunk_{idx:04d}.wav")
    sf.write(wav_path, audio, SAMPLE_RATE)
    return (idx, wav_path, duration)


def generate_audio_chunks(
    chunks: list[str],
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
    tmpdir: str = "/tmp",
    model_id: str = MODEL,
    on_progress: Callable[[str], None] | None = None,
    workers: int = DEFAULT_WORKERS,
) -> list[str]:
    """Generate WAV files for each text chunk using parallel workers.

    Returns list of WAV file paths in order.
    """
    progress = on_progress or _default_progress
    total = len(chunks)

    if workers <= 1 or total <= 1:
        return _generate_sequential(chunks, voice, speed, tmpdir, model_id, progress)

    progress(f"  Generating audio for {total} chunks ({workers} workers)...\n")

    work_items = [
        (i, chunk, voice, speed, tmpdir, model_id)
        for i, chunk in enumerate(chunks, 1)
    ]

    results: dict[int, tuple[str | None, float]] = {}
    done_count = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_generate_chunk, item): item[0] for item in work_items}
        for future in as_completed(futures):
            idx, wav_path, duration = future.result()
            results[idx] = (wav_path, duration)
            done_count += 1
            if wav_path:
                progress(f"  Chunk [{done_count}/{total}] done — {duration:.0f}s\n")
            else:
                progress(f"  Chunk [{done_count}/{total}] skipped (no audio)\n")

    # Collect in original order
    wav_files = []
    for i in range(1, total + 1):
        wav_path, _ = results[i]
        if wav_path:
            wav_files.append(wav_path)

    return wav_files


def _generate_sequential(
    chunks: list[str],
    voice: str,
    speed: float,
    tmpdir: str,
    model_id: str,
    progress: Callable[[str], None],
) -> list[str]:
    """Fallback: generate chunks one at a time in the current process."""
    import soundfile as sf

    logging.disable(logging.WARNING)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    progress("  Loading TTS model...")
    from mlx_audio.tts.utils import load_model
    model = load_model(model_id)
    progress(" done\n")

    wav_files = []
    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        progress(f"  Generating audio [{i}/{total}]...")

        audio_segments = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for result in model.generate(chunk, voice=voice, speed=speed, lang_code=LANG_CODE):
                audio_segments.append(np.array(result.audio))

        if not audio_segments:
            progress(" skipped (no audio)\n")
            continue

        audio = np.concatenate(audio_segments)
        duration = len(audio) / SAMPLE_RATE
        wav_path = os.path.join(tmpdir, f"chunk_{i:04d}.wav")
        sf.write(wav_path, audio, SAMPLE_RATE)
        wav_files.append(wav_path)
        progress(f" {duration:.0f}s\n")

    return wav_files
