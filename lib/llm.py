"""Configurable LLM abstraction layer.

Supports Ollama (local), OpenAI, and Anthropic as backends.
Provider is configured via the [llm] section in ~/.config/a2pod/config.
Falls back to Ollama + llama3.2 if unconfigured (backward compatible).
"""

import configparser
import json
import os
import re
import urllib.request
import urllib.error

_DEFAULT_MODELS = {
    "ollama": "llama3.2",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-20250414",
}

OLLAMA_URL = "http://localhost:11434/api/generate"

_PREAMBLE_RE = re.compile(
    r"^here(?:'s|\s+is)\s+(?:a\s+|the\s+)?"
    r"(?:podcast\s+)?(?:episode\s+)?(?:description\s+)?"
    r"(?:cleaned?\s*|edited\s*|updated\s*|revised\s*|modified\s*|final\s*)?"
    r"(?:version\s+(?:of\s+)?(?:the\s+)?)?"
    r"(?:summary|text)[^.:\n]*[:.—\-]\s*\n*",
    re.IGNORECASE,
)


def strip_preamble(text: str) -> str:
    """Strip common LLM preambles like 'Here is the summary:' or 'Here's the cleaned text:'."""
    return _PREAMBLE_RE.sub("", text, count=1).strip()


def _load_llm_config() -> tuple[str, str, str]:
    """Read [llm] from config. Returns (provider, api_key, model)."""
    config_path = os.path.expanduser("~/.config/a2pod/config")
    cfg = configparser.ConfigParser()
    cfg.read(config_path)

    provider = cfg.get("llm", "provider", fallback="ollama").strip().lower()
    api_key = cfg.get("llm", "api_key", fallback="").strip()
    model = cfg.get("llm", "model", fallback="").strip()

    if not model:
        model = _DEFAULT_MODELS.get(provider, "llama3.2")

    return provider, api_key, model


_provider, _api_key, _default_model = _load_llm_config()
DEFAULT_MODEL = _default_model


def _generate_ollama(prompt: str, temperature: float, max_tokens: int, model: str, api_key: str) -> str | None:
    """Generate via local Ollama server."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("response", "").strip()
            return result if result else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError):
        return None


_openai_client = None


def _generate_openai(prompt: str, temperature: float, max_tokens: int, model: str, api_key: str) -> str | None:
    """Generate via OpenAI chat completions API."""
    global _openai_client
    try:
        import openai
    except ImportError:
        raise SystemExit(
            "OpenAI provider selected but 'openai' package is not installed.\n"
            "Run: pip3 install openai"
        )

    if not api_key:
        raise SystemExit(
            "OpenAI provider selected but no api_key set in config.\n"
            "Add api_key to [llm] section in ~/.config/a2pod/config"
        )

    if _openai_client is None:
        _openai_client = openai.OpenAI(api_key=api_key)

    try:
        response = _openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result = response.choices[0].message.content.strip()
        return result if result else None
    except (openai.APIConnectionError, openai.RateLimitError, openai.APIStatusError):
        return None


_anthropic_client = None


def _generate_anthropic(prompt: str, temperature: float, max_tokens: int, model: str, api_key: str) -> str | None:
    """Generate via Anthropic messages API."""
    global _anthropic_client
    try:
        import anthropic
    except ImportError:
        raise SystemExit(
            "Anthropic provider selected but 'anthropic' package is not installed.\n"
            "Run: pip3 install anthropic"
        )

    if not api_key:
        raise SystemExit(
            "Anthropic provider selected but no api_key set in config.\n"
            "Add api_key to [llm] section in ~/.config/a2pod/config"
        )

    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=api_key)

    try:
        response = _anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        result = response.content[0].text.strip()
        return result if result else None
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError):
        return None


_BACKENDS = {
    "ollama": _generate_ollama,
    "openai": _generate_openai,
    "anthropic": _generate_anthropic,
}


def generate(
    prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 200,
    model: str | None = None,
) -> str | None:
    """Generate text using the configured LLM provider.

    Returns the generated text, or None on failure.
    """
    resolved_model = model or DEFAULT_MODEL
    backend = _BACKENDS.get(_provider)
    if backend is None:
        raise SystemExit(f"Unknown LLM provider: {_provider}")
    return backend(prompt, temperature, max_tokens, resolved_model, _api_key)
