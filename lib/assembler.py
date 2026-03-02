"""Audio assembly — concatenate WAV chunks into M4B audiobook."""

import os
import subprocess
import wave

from errors import PipelineError


def concat_to_m4b(wav_files: list[str], output_path: str, title: str) -> None:
    """Concatenate WAV chunks into a single M4B audiobook.

    Uses ffmpeg to concat WAVs and encode as AAC in M4B container.
    """
    if not wav_files:
        raise PipelineError("No audio chunks to combine.")

    tmpdir = os.path.dirname(wav_files[0])

    # Create ffmpeg concat list
    list_path = os.path.join(tmpdir, "filelist.txt")
    with open(list_path, "w") as f:
        for wav in wav_files:
            f.write(f"file '{wav}'\n")

    # Concat WAVs → single WAV
    combined_wav = os.path.join(tmpdir, "combined.wav")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", combined_wav,
        ],
        capture_output=True, check=True,
    )

    # Convert to M4B (AAC in M4B container) with metadata
    # movflags +faststart puts the moov atom at the start for better streaming/compatibility
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", combined_wav,
            "-c:a", "aac", "-b:a", "128k",
            "-metadata", f"title={title}",
            "-metadata", "artist=A2Pod",
            "-metadata", "genre=Audiobook",
            "-movflags", "+faststart",
            "-f", "ipod", output_path,
        ],
        capture_output=True, check=True,
    )


def build_transcript_vtt(
    chunks: list[str], wav_files: list[str], output_path: str,
    intro_offset: float = 0.0,
) -> str:
    """Build a WebVTT transcript from text chunks and their corresponding WAV files.

    Reads each WAV's duration to produce cumulative timestamps.
    intro_offset shifts all timestamps forward to account for episode intro.
    Returns output_path.
    """
    cues = []
    offset = intro_offset
    for chunk_text, wav_path in zip(chunks, wav_files):
        with wave.open(wav_path, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        start = offset
        end = offset + duration
        cues.append((start, end, chunk_text))
        offset = end

    def _fmt(seconds: float) -> str:
        h = int(seconds) // 3600
        m = (int(seconds) % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n")
        for start, end, text in cues:
            f.write(f"\n{_fmt(start)} --> {_fmt(end)}\n{text}\n")

    return output_path
