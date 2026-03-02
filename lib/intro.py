"""Episode intro generation — jingle, spoken title, silence."""

import os
import wave

import numpy as np


SAMPLE_RATE = 24000


def generate_jingle(output_path: str, sample_rate: int = SAMPLE_RATE) -> str:
    """Generate a pleasant bell/chime chord programmatically.

    C major triad (C5+E5+G5+C6) with exponential decay and a softer
    second strike at t=0.6s for a "ding-ding" feel. ~2.5s duration.
    """
    import soundfile as sf

    duration = 2.5
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # C major chord: C5(523), E5(659), G5(784), C6(1047)
    freqs = [523.25, 659.25, 783.99, 1046.50]
    chord = sum(np.sin(2 * np.pi * f * t) for f in freqs)

    # Exponential decay envelope
    envelope = np.exp(-t * 2.5)
    strike1 = chord * envelope

    # Second softer strike at t=0.6s
    offset_samples = int(0.6 * sample_rate)
    strike2 = np.zeros_like(t)
    t2 = t[:-offset_samples] if offset_samples > 0 else t
    chord2 = sum(np.sin(2 * np.pi * f * t2) for f in freqs)
    envelope2 = np.exp(-t2 * 3.0) * 0.5
    strike2[offset_samples:] = (chord2 * envelope2)[:len(strike2) - offset_samples]

    audio = strike1 + strike2

    # Normalize to 0.4 peak amplitude
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio * (0.4 / peak)

    sf.write(output_path, audio.astype(np.float32), sample_rate)
    return output_path


def generate_intro_speech(
    title: str,
    voice: str,
    speed: float,
    output_path: str,
    podcast_name: str,
) -> str:
    """Speak '<podcast_name> presents: <title>' using Kokoro TTS."""
    import logging
    import warnings

    import soundfile as sf

    logging.disable(logging.WARNING)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    from mlx_audio.tts.utils import load_model

    model = load_model("mlx-community/Kokoro-82M-bf16")

    text = f"{podcast_name} presents: {title}"
    audio_segments = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for result in model.generate(text, voice=voice, speed=speed, lang_code="a"):
            audio_segments.append(np.array(result.audio))

    if not audio_segments:
        # Fallback: generate silence if TTS fails
        audio = np.zeros(int(SAMPLE_RATE * 1.0), dtype=np.float32)
    else:
        audio = np.concatenate(audio_segments)

    sf.write(output_path, audio, SAMPLE_RATE)
    return output_path


def generate_silence(duration: float, output_path: str, sample_rate: int = SAMPLE_RATE) -> str:
    """Generate a WAV file of silence."""
    import soundfile as sf

    samples = np.zeros(int(sample_rate * duration), dtype=np.float32)
    sf.write(output_path, samples, sample_rate)
    return output_path


def generate_intro(
    title: str,
    voice: str,
    speed: float,
    tmpdir: str,
    podcast_name: str,
) -> list[str]:
    """Generate full intro: jingle + spoken title + silence buffer.

    Returns list of WAV file paths in playback order.
    """
    jingle_path = os.path.join(tmpdir, "intro_jingle.wav")
    speech_path = os.path.join(tmpdir, "intro_speech.wav")
    silence_path = os.path.join(tmpdir, "intro_silence.wav")

    generate_jingle(jingle_path)
    generate_intro_speech(title, voice, speed, speech_path, podcast_name)
    generate_silence(0.8, silence_path)

    return [jingle_path, speech_path, silence_path]


def get_intro_duration(wav_files: list[str]) -> float:
    """Sum the duration of intro WAV files."""
    total = 0.0
    for path in wav_files:
        with wave.open(path, "rb") as wf:
            total += wf.getnframes() / wf.getframerate()
    return total
