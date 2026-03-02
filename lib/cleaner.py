"""Text cleaning for audio output.

Two-pass cleaning: regex heuristics first (fast, deterministic), then an
optional LLM pass to catch subtle patterns the regex missed.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from num2words import num2words

from llm import generate, strip_preamble

_LLM_CHUNK_SIZES = {
    "ollama": 12000,   # local — sequential, fewer calls is better
    "openai": 3000,    # cloud — parallel, more chunks = faster
    "anthropic": 3000,
}

# Common abbreviations that TTS reads letter-by-letter → spoken forms
_ABBREVIATIONS = {
    r"\bTL;?DR\b": "too long didn't read",
    r"\bASAP\b": "as soon as possible",
    r"\bFAQ\b": "frequently asked questions",
    r"\bFYI\b": "for your information",
    r"\bIMO\b": "in my opinion",
    r"\bIMHO\b": "in my humble opinion",
    r"\bDIY\b": "do it yourself",
    r"\bFOMO\b": "fear of missing out",
    r"\bYMMV\b": "your mileage may vary",
    r"\bTBD\b": "to be determined",
    r"\bTBA\b": "to be announced",
    r"\bETA\b": "estimated time of arrival",
    r"\bAMA\b": "ask me anything",
    r"\bPSA\b": "public service announcement",
    r"\bICYMI\b": "in case you missed it",
    r"\bIIRC\b": "if I recall correctly",
    r"\bAFAIK\b": "as far as I know",
    r"\bBTW\b": "by the way",
    r"\bRSVP\b": "please respond",
}


def _int_to_words(n: int) -> str:
    """Convert an integer to words, using year form for 1800-2100."""
    if 1800 <= n <= 2100:
        return num2words(n, to="year")
    return num2words(n)


def _decimal_or_version(m: re.Match) -> str:
    """Convert a decimal number to words, but skip version numbers (e.g. 3.14.2)."""
    # If followed by another dot+digit, it's a version string — leave it alone
    after = m.string[m.end():]
    if after and after[0] == ".":
        return m.group(0)
    whole, frac = m.group(1), m.group(2)
    whole_words = num2words(int(whole))
    frac_words = " ".join(num2words(int(d)) for d in frac)
    return f"{whole_words} point {frac_words}"


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

    # Comparison operators (before HTML tag stripping which eats < and >)
    text = re.sub(r"(?<!\w)>=\s*(\d)", r"at least \1", text)
    text = re.sub(r"(?<!\w)<=\s*(\d)", r"at most \1", text)
    text = re.sub(r"(?<!\w)>\s*(\d)", r"more than \1", text)
    text = re.sub(r"(?<!\w)<\s*(\d)", r"less than \1", text)

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
        r"if you (liked|enjoyed) this|"
        r"book today|follow along|check out (my|our)|click here|learn more|"
        r"get started|try it free|start your|grab your|claim your|"
        r"download now|register now|buy now|order now|shop now|"
        r"join us|join today|sign up today|book a|schedule a|reserve your)\b.*$",
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

    # ── TTS pronunciation fixes ──────────────────────────────────────────────
    # Symbols and abbreviations that TTS reads literally or awkwardly.

    # Approximate: ~20 → around 20
    text = re.sub(r"~(\d)", r"around \1", text)

    # Abbreviations: e.g. → for example, i.e. → that is, etc.
    text = re.sub(r"\be\.g\.\s*,?\s*", "for example, ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi\.e\.\s*,?\s*", "that is, ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvs\.?\s", "versus ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bw/o\b", "without", text)
    text = re.sub(r"\bw/(?=[a-zA-Z])", "with ", text)

    # Common abbreviations → spoken forms
    for pattern, replacement in _ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Multiplier: 10x → 10 times (digit followed by x at word boundary)
    text = re.sub(r"(\d)x\b", r"\1 times", text)

    # Slash as "or" between words: AI/ML → AI or ML, true/false → true or false
    text = re.sub(r"\b([A-Za-z]+)/([A-Za-z]+)\b", r"\1 or \2", text)

    # Ampersand: R&D → R and D, Q&A → Q and A
    text = re.sub(r"&", " and ", text)

    # Arrows: -> or → or => → (remove, or replace contextually)
    text = re.sub(r"\s*[-=]>\s*", " to ", text)
    text = re.sub(r"\s*→\s*", " to ", text)

    # Hash as "number": #1 → number 1 (but not #hashtags)
    text = re.sub(r"#(\d)", r"number \1", text)

    # Money shorthand: $5M → 5 million dollars, $2B → 2 billion dollars
    text = re.sub(r"\$(\d+(?:\.\d+)?)\s*[Bb]\b", r"\1 billion dollars", text)
    text = re.sub(r"\$(\d+(?:\.\d+)?)\s*[Mm]\b", r"\1 million dollars", text)
    text = re.sub(r"\$(\d+(?:\.\d+)?)\s*[Kk]\b", r"\1 thousand dollars", text)

    # Large number shorthand (without dollar): 10K → 10 thousand
    text = re.sub(r"(\d)\s*K\b", r"\1 thousand", text)
    text = re.sub(r"(\d)\s*M\b", r"\1 million", text)
    text = re.sub(r"(\d)\s*B\b", r"\1 billion", text)

    # Plus sign between words/concepts: AI + ML → AI and ML
    text = re.sub(r"\b(\w+)\s*\+\s*(\w+)\b", r"\1 and \2", text)

    # Equals sign: = → equals (between words/numbers)
    text = re.sub(r"\s*=\s*", " equals ", text)

    # ── Number-to-words conversion ────────────────────────────────────────────

    # Dollar amounts: $42 → forty-two dollars (skip K/M/B amounts already handled)
    def _dollar_to_words(m):
        n = int(m.group(1))
        word = num2words(n)
        return f"{word} dollar{'s' if n != 1 else ''}"
    text = re.sub(r"\$(\d+)(?!\d*\s*[KkMmBb]\b)", _dollar_to_words, text)

    # Percentages: 50% → fifty percent
    def _pct_to_words(m):
        return f"{num2words(int(m.group(1)))} percent"
    text = re.sub(r"(\d+)%", _pct_to_words, text)

    # Decimals: 3.14 → three point one four (skip version numbers like 3.14.2)
    text = re.sub(r"(\d+)\.(\d+)", _decimal_or_version, text)

    # Standalone integers: 12325 → twelve thousand three hundred and twenty-five
    # Skip single digits (0-9), skip numbers already followed by
    # thousand/million/billion (already converted above)
    def _int_standalone(m):
        n = int(m.group(0))
        if n <= 9:
            return m.group(0)
        return _int_to_words(n)
    text = re.sub(
        r"(?<!\.)\b\d{2,}\b(?!\s*(?:thousand|million|billion|trillion|dollars?|percent|point|times))",
        _int_standalone, text,
    )

    # ── End TTS fixes ────────────────────────────────────────────────────────

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
You are preparing text for audio narration. A first cleaning pass has already \
removed markdown, URLs, HTML, and obvious promotional lines. Your job is to \
catch what automated cleaning missed:

1. Remove any remaining self-promotion, calls to action, newsletter plugs, \
or social media prompts that slipped through. Pay special attention to soft \
CTAs like "book today", "follow along", "check out our", "click here", \
"learn more", "get started", "try it free", "grab your", "download now", \
"join us", "sign up today", "schedule a", and similar promotional language.
2. Remove references to visual elements: "the chart below", "as shown above", \
"see the diagram", "in the screenshot". Drop the entire sentence if it only \
describes a visual.
3. Replace remaining references to written format: "this post" → "this episode", \
"this article" → "this episode", "readers" → "listeners", \
"reading" → "listening" (when referring to consuming this content).
4. Expand any remaining abbreviations that a text-to-speech engine would read \
letter-by-letter instead of as words (e.g., "TLDR" → "too long didn't read", \
"ASAP" → "as soon as possible", "FOMO" → "fear of missing out"). Only expand \
abbreviations that sound unnatural when spelled out; leave well-known acronyms \
like "AI", "API", or "CEO" as-is.
5. Fix any remaining raw numbers so they read naturally when spoken aloud. \
For example, "12325" should become "twelve thousand three hundred and \
twenty-five", and years like "2024" should read as "twenty twenty-four".
6. Smooth any awkward transitions or sentence fragments left by prior cleanup \
(e.g., dangling "Additionally," at the start of a paragraph that lost its context).

RULES:
- Return the COMPLETE text with only the above changes applied.
- Do NOT summarize, shorten, or omit informational content.
- Do NOT add commentary or preamble. Start directly with the cleaned content.
- Preserve all paragraph breaks.

Text:
"""


def _llm_clean_chunk(text: str, model: str) -> str | None:
    """Send a chunk to the LLM for cleaning. Returns None on failure."""
    result = generate(
        _LLM_CLEAN_PROMPT + text,
        temperature=0.1,
        max_tokens=len(text) + 500,
        model=model,
    )
    if not result:
        return None
    result = strip_preamble(result)
    # Guard against the LLM drastically shortening content
    if len(result) < len(text) * 0.5:
        return None
    return result


def llm_clean_for_audio(text: str, model: str | None = None,
                        on_progress: Callable[[str], None] | None = None) -> str:
    """LLM final pass to catch what regex missed. Returns input unchanged on failure."""
    if model is None:
        from llm import DEFAULT_MODEL
        model = DEFAULT_MODEL
    from llm import _provider
    chunk_size = _LLM_CHUNK_SIZES.get(_provider, 3000)
    paragraphs = text.split("\n\n")
    chunks = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))

    total = len(chunks)

    if total == 1:
        if on_progress:
            on_progress(f"Cleaning text [1/1]")
        result = _llm_clean_chunk(chunks[0], model)
        return result if result is not None else text

    # Parallelize for cloud providers; Ollama is local/serial so run sequentially.
    parallel = _provider != "ollama"

    if parallel:
        results = [None] * total
        with ThreadPoolExecutor(max_workers=total) as pool:
            futures = {pool.submit(_llm_clean_chunk, chunk, model): i
                       for i, chunk in enumerate(chunks)}
            for future in as_completed(futures):
                idx = futures[future]
                result = future.result()
                if result is None:
                    return text
                results[idx] = result
                if on_progress:
                    done = sum(1 for r in results if r is not None)
                    on_progress(f"Cleaning text [{done}/{total}]")
    else:
        results = []
        for i, chunk in enumerate(chunks):
            if on_progress:
                on_progress(f"Cleaning text [{i + 1}/{total}]")
            result = _llm_clean_chunk(chunk, model)
            if result is None:
                return text
            results.append(result)

    return "\n\n".join(results)
