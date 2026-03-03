"""Configurable LLM abstraction layer.

Supports Ollama (local), OpenAI, and Anthropic as backends.
Provider is configured via the [llm] section in ~/.config/a2pod/config.
Falls back to Ollama + llama3.2 if unconfigured (backward compatible).
Supports runtime switching via set_provider().
"""

import configparser
import json
import os
import re
import urllib.request
import urllib.error

_CONFIG_PATH = os.path.expanduser("~/.config/a2pod/config")

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


def _load_llm_config() -> tuple[str, dict[str, str], str]:
    """Read [llm] from config. Returns (provider, api_keys, model).

    api_keys maps provider name -> api_key for all configured providers.
    """
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH)

    provider = cfg.get("llm", "provider", fallback="ollama").strip().lower()
    model = cfg.get("llm", "model", fallback="").strip()

    # Build per-provider API key map
    api_keys = {"ollama": ""}  # ollama never needs a key
    generic_key = cfg.get("llm", "api_key", fallback="").strip()
    for p in _DEFAULT_MODELS:
        specific = cfg.get("llm", f"{p}_api_key", fallback="").strip()
        if specific:
            api_keys[p] = specific
        elif generic_key and p == provider:
            # Legacy: single api_key applies to the currently configured provider
            api_keys[p] = generic_key

    if not model:
        model = _DEFAULT_MODELS.get(provider, "llama3.2")

    return provider, api_keys, model


_provider, _api_keys, _default_model = _load_llm_config()
DEFAULT_MODEL = _default_model


# ── Public query / switch API ────────────────────────────────────────────────

def get_provider_info() -> tuple[str, str]:
    """Return (current_provider, current_model)."""
    return _provider, DEFAULT_MODEL


def get_available_providers() -> dict[str, str]:
    """Return {provider: default_model} for providers that have keys configured.

    Ollama is always available (no key needed).
    """
    return {p: _DEFAULT_MODELS[p] for p in _DEFAULT_MODELS if _api_keys.get(p, "") or p == "ollama"}


def get_ollama_models() -> list[str]:
    """Query the local Ollama server for installed model names."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError):
        return []


def set_provider(provider: str, model: str | None = None) -> tuple[str, str]:
    """Switch the active LLM provider and optionally the model at runtime.

    Invalidates cached API clients when switching provider.
    Persists the change to the config file.
    Returns (provider, model) after the switch.
    Raises ValueError if provider is unknown or has no API key.
    """
    global _provider, DEFAULT_MODEL, _openai_client, _anthropic_client

    provider = provider.strip().lower()
    if provider not in _BACKENDS:
        raise ValueError(f"Unknown provider: {provider}. Choose from: {', '.join(_BACKENDS)}")

    if provider != "ollama" and not _api_keys.get(provider):
        raise ValueError(
            f"No API key configured for {provider}.\n"
            f"Add {provider}_api_key to [llm] in ~/.config/a2pod/config"
        )

    resolved_model = model or _DEFAULT_MODELS.get(provider, "llama3.2")

    # Invalidate cached clients when switching provider
    if provider != _provider:
        _openai_client = None
        _anthropic_client = None

    _provider = provider
    DEFAULT_MODEL = resolved_model

    _save_llm_config(provider, resolved_model)
    return provider, resolved_model


def _save_llm_config(provider: str, model: str) -> None:
    """Persist current provider and model to the config file."""
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH)

    if not cfg.has_section("llm"):
        cfg.add_section("llm")
    cfg.set("llm", "provider", provider)
    cfg.set("llm", "model", model)

    with open(_CONFIG_PATH, "w") as f:
        cfg.write(f)


# ── Backends ─────────────────────────────────────────────────────────────────

def _generate_ollama(prompt: str, temperature: float, max_tokens: int, model: str, api_key: str) -> str | None:
    """Generate via local Ollama server."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
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
        with urllib.request.urlopen(req, timeout=300) as resp:
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
        raise RuntimeError(
            "OpenAI provider selected but 'openai' package is not installed.\n"
            "Run: pip3 install openai"
        )

    if not api_key:
        raise RuntimeError(
            "OpenAI provider selected but no api_key set in config.\n"
            "Add openai_api_key to [llm] section in ~/.config/a2pod/config"
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
        raise RuntimeError(
            "Anthropic provider selected but 'anthropic' package is not installed.\n"
            "Run: pip3 install anthropic"
        )

    if not api_key:
        raise RuntimeError(
            "Anthropic provider selected but no api_key set in config.\n"
            "Add anthropic_api_key to [llm] section in ~/.config/a2pod/config"
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
    import logging
    import time
    log = logging.getLogger(__name__)
    resolved_model = model or DEFAULT_MODEL
    backend = _BACKENDS.get(_provider)
    if backend is None:
        raise SystemExit(f"Unknown LLM provider: {_provider}")
    t0 = time.monotonic()
    result = backend(prompt, temperature, max_tokens, resolved_model, _api_keys.get(_provider, ""))
    elapsed = time.monotonic() - t0
    log.info("LLM %s/%s — %d chars in, %d chars out, %.1fs",
             _provider, resolved_model, len(prompt), len(result) if result else 0, elapsed)
    return result
