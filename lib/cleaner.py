"""Text cleaning for audio output.

Two-pass cleaning: regex heuristics first (fast, deterministic), then an
optional LLM pass via Ollama to catch subtle patterns the regex missed.
"""

import json
import re
import urllib.request
import urllib.error

OLLAMA_URL = "http://localhost:11434/api/generate"
LLM_CHUNK_SIZE = 4000
LLM_TIMEOUT = 60


def clean_for_audio(text: str) -> str:
    """Clean extracted article text for natural-sounding TTS output."""

    # Remove fenced code blocks (``` ... ```)
    text = re.sub(r"```[\s\S]*?```", "", text)

    # Remove inline code backticks but keep the text
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Markdown links [text](url) → keep just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Markdown images ![alt](url) → remove entirely
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)

    # Bare URLs
    text = re.sub(r"https?://\S+", "", text)

    # Markdown headings — strip the # prefix
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Bold and italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)

    # Strikethrough
    text = re.sub(r"~~([^~]+)~~", r"\1", text)

    # Markdown horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Image references in prose
    text = re.sub(
        r"^.*\b(as shown in the (image|figure|diagram|screenshot)|"
        r"see (figure|image|screenshot|diagram)|"
        r"(screenshot|image|figure) (above|below)|"
        r"(click (the )?(image|thumbnail)|tap (the )?(image|photo)))\b.*$",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Image captions (short lines starting with "Figure" or "Image" followed by number/colon)
    text = re.sub(r"^(Figure|Image|Photo|Screenshot)\s*\d*[\s:.].*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Navigation artifacts
    text = re.sub(
        r"^.*(Table of Contents|Read more|Continue reading|Back to top|Skip to content)\s*$",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Self-promotion / CTA lines
    text = re.sub(
        r"^.*\b(follow me|subscribe to|sign up for|join (my|our) newsletter|"
        r"like and share|share this (post|article)|"
        r"follow (us|me) on|connect with (me|us)|"
        r"get (my|our) (free|weekly|daily)|"
        r"don'?t forget to (subscribe|follow|like)|"
        r"if you (liked|enjoyed) this)\b.*$",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Engagement-bait hooks ("save, bookmark, and internalise what you're about to read")
    text = re.sub(
        r"^.*\b(save.{0,20}bookmark|bookmark.{0,20}save|"
        r"what you'?re about to read|"
        r"before (we|you|I) (start|begin|dive|get into)|"
        r"(pin|save) this (post|article|thread)|"
        r"you('?re| are) going to want to (save|bookmark|read)|"
        r"drop everything and read)\b.*$",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Social media handles at end of text (standalone @username lines)
    text = re.sub(r"^\s*@\w+\s*$", "", text, flags=re.MULTILINE)

    # Markdown unordered list markers (keep the text)
    text = re.sub(r"^(\s*)[*+-]\s+", r"\1", text, flags=re.MULTILINE)

    # Markdown ordered list markers (keep the text)
    text = re.sub(r"^(\s*)\d+\.\s+", r"\1", text, flags=re.MULTILINE)

    # Blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)

    # Replace self-referential article language with audio-appropriate terms
    def _replace_preserving_case(pattern, replacement, text):
        def _repl(m):
            original = m.group(0)
            if original[0].isupper():
                return replacement[0].upper() + replacement[1:]
            return replacement
        return re.sub(pattern, _repl, text, flags=re.IGNORECASE)

    text = _replace_preserving_case(r"\bthis (blog )?post\b", "this episode", text)
    text = _replace_preserving_case(r"\bthis article\b", "this episode", text)
    text = _replace_preserving_case(r"\bthe rest of this (blog )?post\b", "the rest of this episode", text)
    text = _replace_preserving_case(r"\bthe rest of this article\b", "the rest of this episode", text)

    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Trim trailing spaces on each line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

    return text.strip()


_LLM_CLEAN_PROMPT = """\
You are a text editor preparing written content for audio narration. \
Apply ONLY these changes to the text below:

1. REMOVE any self-promotional or engagement-bait lines: "save/bookmark this", \
"what you're about to read", "if you enjoyed this", calls to action, \
newsletter plugs, social media prompts, author bios.
2. REPLACE references to written format with audio format: \
"this post" → "this episode", "this article" → "this episode", \
"this blog post" → "this episode", "reading" → "listening" (when referring \
to the content itself, not reading as a general activity), \
"readers" → "listeners", "read on" → "listen on".
3. REMOVE any remaining web artifacts: navigation links, "read more", \
share buttons text, cookie notices, comment section prompts.

CRITICAL RULES:
- Return the COMPLETE text with only the above changes applied.
- Do NOT summarize, shorten, or omit any informational content.
- Do NOT add commentary, explanations, or preamble.
- Do NOT rewrite sentences beyond the specific changes listed above.
- Preserve all paragraph breaks and structure.
- Output ONLY the cleaned text, nothing else.

Text to clean:
"""


def _llm_clean_chunk(text: str, model: str) -> str | None:
    """Send a chunk to Ollama for cleaning. Returns None on failure."""
    payload = json.dumps({
        "model": model,
        "prompt": _LLM_CLEAN_PROMPT + text,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": len(text) + 500,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("response", "").strip()
            if not result:
                return None
            # Guard against the LLM drastically shortening content
            if len(result) < len(text) * 0.5:
                return None
            return result
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def llm_clean_for_audio(text: str, model: str = "llama3.2") -> str:
    """LLM final pass to catch what regex missed. Returns input unchanged on failure."""
    paragraphs = text.split("\n\n")
    chunks = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > LLM_CHUNK_SIZE and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))

    cleaned = []
    for chunk in chunks:
        result = _llm_clean_chunk(chunk, model)
        if result is None:
            return text  # bail entirely on first failure
        cleaned.append(result)

    return "\n\n".join(cleaned)
