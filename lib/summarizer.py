"""Episode summarization for podcast feed descriptions.

Generates a 2-3 sentence summary via the configured LLM provider.
Falls back gracefully if the LLM is unavailable — no crash, always returns a string.
"""

from llm import generate, strip_preamble

MAX_INPUT_CHARS = 3000


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


def summarize(text: str, title: str = "", model: str | None = None) -> str | None:
    """Generate a summary via LLM. Returns None on any failure."""
    if model is None:
        from llm import DEFAULT_MODEL
        model = DEFAULT_MODEL
    truncated = text[:MAX_INPUT_CHARS]
    prompt = (
        f"Write a 2-3 sentence summary of the following text for a podcast episode description. "
        f"Output ONLY the summary sentences — no preamble, no labels, no introductory phrases. "
        f"Do not start with 'This article', 'The article', 'This episode', or 'Here is'. "
        f"Jump straight into the content.\n\n"
        f"Title: {title}\n\n"
        f"{truncated}"
    )

    summary = generate(prompt, temperature=0.3, max_tokens=200, model=model)

    if summary:
        summary = strip_preamble(summary)
    if summary:
        # Truncate to 4000 chars (iTunes limit)
        return summary[:4000]
    return None


def get_summary(text: str, title: str = "", model: str | None = None) -> str:
    """Generate a summary, falling back to first-sentence extraction on failure."""
    result = summarize(text, title, model)
    if result:
        return result
    return _fallback_summary(text)
