"""TTS generation using MLX Audio (Kokoro)."""

import os
import numpy as np

MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
LANG_CODE = "a"  # English
SAMPLE_RATE = 24000


def generate_audio_chunks(
    chunks: list[str],
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
    tmpdir: str = "/tmp",
    model_id: str = MODEL,
) -> list[str]:
    """Generate WAV files for each text chunk.

    Returns list of WAV file paths.
    """
    from mlx_audio.tts.utils import load_model
    import soundfile as sf

    print(f"🧠 Loading model ({model_id})...")
    model = load_model(model_id)

    wav_files = []
    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        print(f"🔊 Generating audio [{i}/{total}] ({len(chunk)} chars)...")

        audio_segments = []
        for result in model.generate(chunk, voice=voice, speed=speed, lang_code=LANG_CODE):
            audio_segments.append(np.array(result.audio))

        if not audio_segments:
            print(f"  ⚠️  No audio for chunk {i}, skipping")
            continue

        audio = np.concatenate(audio_segments)
        wav_path = os.path.join(tmpdir, f"chunk_{i:04d}.wav")
        sf.write(wav_path, audio, SAMPLE_RATE)
        wav_files.append(wav_path)

    return wav_files
