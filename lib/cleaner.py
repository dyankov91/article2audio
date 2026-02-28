"""Regex-based text cleaning for audio output.

Removes web artifacts that sound bad when read aloud: URLs, markdown syntax,
self-promotion CTAs, image references, code blocks, HTML tags, etc.
All regex — no external dependencies.
"""

import re


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

    # Social media handles at end of text (standalone @username lines)
    text = re.sub(r"^\s*@\w+\s*$", "", text, flags=re.MULTILINE)

    # Markdown unordered list markers (keep the text)
    text = re.sub(r"^(\s*)[*+-]\s+", r"\1", text, flags=re.MULTILINE)

    # Markdown ordered list markers (keep the text)
    text = re.sub(r"^(\s*)\d+\.\s+", r"\1", text, flags=re.MULTILINE)

    # Blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)

    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Trim trailing spaces on each line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

    return text.strip()
