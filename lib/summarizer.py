"""Ollama-based episode summarization for podcast feed descriptions.

Generates a 2-3 sentence summary via a local Ollama model.
Falls back gracefully if Ollama is unavailable — no crash, always returns a string.
Uses urllib only (no external dependencies).
"""

import json
import urllib.request
import urllib.error

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.2"
MAX_INPUT_CHARS = 6000
TIMEOUT_SECONDS = 30


def _fallback_summary(text: str) -> str:
    """Extract the first sentence as a fallback summary, truncated to 500 chars."""
    text = text.strip()
    # Find first sentence-ending punctuation
    for end in (".  ", ". ", ".\n", "! ", "!\n", "? ", "?\n"):
        idx = text.find(end)
        if idx != -1 and idx < 500:
            return text[: idx + 1]
    # No sentence boundary found — truncate at word boundary
    if len(text) <= 500:
        return text
    truncated = text[:500].rsplit(" ", 1)[0]
    return truncated + "..."


def summarize(text: str, title: str = "", model: str = DEFAULT_MODEL) -> str | None:
    """Generate a summary via Ollama. Returns None on any failure."""
    truncated = text[:MAX_INPUT_CHARS]
    prompt = (
        f"Summarize this article in 2-3 sentences for a podcast episode description. "
        f"Be concise and informative. Do not start with 'This article' or 'The article'.\n\n"
        f"Title: {title}\n\n"
        f"{truncated}"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 200,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            summary = data.get("response", "").strip()
            if summary:
                # Truncate to 4000 chars (iTunes limit)
                return summary[:4000]
            return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError):
        return None


def get_summary(text: str, title: str = "", model: str = DEFAULT_MODEL) -> str:
    """Generate a summary, falling back to first-sentence extraction on failure."""
    result = summarize(text, title, model)
    if result:
        return result
    return _fallback_summary(text)
