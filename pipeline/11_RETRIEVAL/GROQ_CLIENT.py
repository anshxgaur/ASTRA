"""
Groq LLM wrapper for the Phase-4 retrieval engine.

Thin, lazy client over the `groq` SDK. Reads GROQ_API_KEY / GROQ_MODEL from
the environment (loaded by HYBRID_RETRIEVER._load_env() — project root .env
and pipeline/.env). If the key is missing or the SDK isn't installed, every
call returns None so callers can fall back to the mock synthesizer.
"""
from __future__ import annotations

import os

DEFAULT_MODEL = "openai/gpt-oss-120b"

# Fallbacks tried in order when the configured model 404s (catalog churn,
# account-tier limits, etc.). The first model that answers is cached for the
# rest of the process.
FALLBACK_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "groq/compound",
]

_client = None
_working_model = None


def _get_client():
    global _client
    if _client is None:
        from groq import Groq
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


def is_available() -> bool:
    """True when a real Groq call is possible (SDK + key present)."""
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key in ("your_key_here", "gsk_...", ""):
        return False
    try:
        import groq  # noqa: F401
        return True
    except ImportError:
        return False


def chat(messages: list[dict], *, temperature: float = 0.1,
         max_tokens: int = 1024) -> str | None:
    """
    Run a chat completion against Groq and return the text reply.
    Returns None (no exception raised) when Groq is unavailable.

    Model selection: use GROQ_MODEL if set, else DEFAULT_MODEL; if that model
    is unavailable (404) fall back through FALLBACK_MODELS and cache the first
    one that works so the session self-heals after catalog changes.
    """
    global _working_model
    if not is_available():
        return None
    client = _get_client()
    candidates = [os.getenv("GROQ_MODEL", DEFAULT_MODEL)] + FALLBACK_MODELS
    candidates = list(dict.fromkeys(candidates))  # dedupe, keep order
    if _working_model:
        candidates = [_working_model] + [m for m in candidates if m != _working_model]

    last_error = None
    for model in candidates:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            _working_model = model
            return response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001 — retrieval must never crash on LLM errors
            last_error = exc
            if getattr(exc, "status_code", None) != 404:
                break  # not a model-availability problem; don't churn fallbacks
    print(f"[groq] LLM call failed ({type(last_error).__name__}): {last_error}")
    return None
