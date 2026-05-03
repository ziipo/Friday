"""Unified LLM client with priority-ordered provider fallback.

Two backends, same `complete()` API:
- `anthropic` provider → Anthropic SDK against api.anthropic.com (PRD-aligned)
- `openrouter` provider → OpenAI SDK against openrouter.ai/api/v1 (fallback)

OpenRouter uses the OpenAI-shaped /chat/completions endpoint, not Anthropic's
/messages endpoint, so we route through different SDKs but expose one API.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from . import secrets
from .logging import log_event
from .paths import FRIDAY_ROOT

CONFIG_PATH = FRIDAY_ROOT / "scripts" / "config" / "llm.yaml"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    secret_name: str
    base_url: str | None
    default_model: str
    extra_headers: dict[str, str]
    backend: str  # "anthropic" | "openai"


@dataclass(frozen=True)
class LLMConfig:
    active: tuple[str, ...]                          # priority order
    providers: dict[str, ProviderConfig]
    jobs: dict[str, dict[str, Any]]

    @property
    def primary_provider(self) -> ProviderConfig:
        """The first provider in the active list (regardless of key availability).
        Used for selecting model defaults at config-load time."""
        return self.providers[self.active[0]]


@lru_cache(maxsize=1)
def load_config() -> LLMConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    providers = {
        name: ProviderConfig(
            name=name,
            secret_name=spec["secret_name"],
            base_url=spec.get("base_url"),
            default_model=spec["default_model"],
            extra_headers=dict(spec.get("extra_headers") or {}),
            backend=spec.get("backend") or ("anthropic" if name == "anthropic" else "openai"),
        )
        for name, spec in raw["providers"].items()
    }
    active_raw = raw["active"]
    active = tuple(active_raw) if isinstance(active_raw, list) else (active_raw,)
    return LLMConfig(
        active=active,
        providers=providers,
        jobs=raw.get("jobs") or {},
    )


@lru_cache(maxsize=4)
def _client_for(provider_name: str):
    cfg = load_config()
    provider = cfg.providers[provider_name]
    api_key = secrets.require(provider.secret_name)
    if provider.backend == "anthropic":
        from anthropic import Anthropic
        kwargs: dict[str, Any] = {"api_key": api_key}
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        if provider.extra_headers:
            kwargs["default_headers"] = dict(provider.extra_headers)
        return Anthropic(**kwargs)
    elif provider.backend == "openai":
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        if provider.extra_headers:
            kwargs["default_headers"] = dict(provider.extra_headers)
        return OpenAI(**kwargs)
    else:
        raise ValueError(f"unknown backend {provider.backend!r}")


def model_for(job: str | None = None) -> str:
    """Default model for a given job. Uses the *primary* provider's default;
    if fallback fires we'll re-translate the model ID at request time."""
    cfg = load_config()
    if job and (override := cfg.jobs.get(job, {}).get("model")):
        return override
    return cfg.primary_provider.default_model


def _translate_model_id(model: str, target_provider: ProviderConfig) -> str:
    """Strip or add the OpenRouter `anthropic/` prefix as needed when failing
    over between Anthropic and OpenRouter."""
    if target_provider.name == "openrouter" and not model.startswith("anthropic/"):
        return f"anthropic/{model}"
    if target_provider.name == "anthropic" and model.startswith("anthropic/"):
        return model[len("anthropic/"):]
    return model


def _to_openai_messages(*, system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    out.extend(messages)
    return out


def _is_transient(exc: BaseException) -> bool:
    """Decide whether a failure should trigger fallback to the next provider.

    Fall back on: auth errors, network errors, rate limits, 5xx server errors,
    and obvious "key missing" KeyError/RuntimeError from secrets.require.
    Don't fall back on bad-input 4xx (e.g., model-not-found, malformed body)
    because the same request will fail on the next provider too."""
    name = type(exc).__name__
    msg = str(exc).lower()
    transient_types = {
        "AuthenticationError", "PermissionDeniedError", "APIConnectionError",
        "APITimeoutError", "RateLimitError", "InternalServerError",
        "ServiceUnavailableError", "APIStatusError",
    }
    if name in transient_types:
        return True
    if isinstance(exc, RuntimeError) and "missing secret" in msg:
        return True
    # Unknown errors: be conservative — try the next provider once. The worst
    # case is one wasted call; the alternative is a hard failure when a tiny
    # transient blip would have resolved on retry.
    if name in {"BadRequestError", "NotFoundError", "UnprocessableEntityError"}:
        return False
    return True


def _anthropic_complete(cli, *, system, messages, model, max_tokens, temperature, extra) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system
    kwargs.update(extra)
    resp = cli.messages.create(**kwargs)
    return _extract_anthropic_text(resp.content)


def _openai_complete(cli, *, system, messages, model, max_tokens, temperature, extra) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _to_openai_messages(system=system, messages=messages),
        "temperature": temperature,
    }
    kwargs.update(extra)
    resp = cli.chat.completions.create(**kwargs)
    if not resp.choices:
        return ""
    return resp.choices[0].message.content or ""


def complete(
    *,
    system: str | None,
    messages: list[dict[str, Any]],
    job: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    **extra: Any,
) -> str:
    """Send a chat completion through the priority-ordered provider chain.

    Returns the assistant's text content. Raises the *last* exception only if
    every provider in `active` fails."""
    cfg = load_config()
    job_cfg = cfg.jobs.get(job, {}) if job else {}
    requested_model = model or model_for(job)
    resolved_max_tokens = max_tokens or job_cfg.get("max_tokens") or 1024

    last_exc: BaseException | None = None
    attempted: list[str] = []

    for provider_name in cfg.active:
        provider = cfg.providers[provider_name]
        # Skip providers whose key isn't in Keychain — saves a guaranteed-failure round-trip.
        if not secrets.get(provider.secret_name):
            log_event("llm", "provider.skip", provider=provider_name, reason="no_key")
            continue
        attempted.append(provider_name)
        try:
            cli = _client_for(provider_name)
        except Exception as exc:
            last_exc = exc
            log_event("llm", "provider.client_error",
                      provider=provider_name, error=type(exc).__name__, message=str(exc))
            if not _is_transient(exc):
                raise
            continue

        provider_model = _translate_model_id(requested_model, provider)
        common = dict(
            system=system,
            messages=messages,
            model=provider_model,
            max_tokens=resolved_max_tokens,
            temperature=temperature,
            extra=extra,
        )
        try:
            if provider.backend == "anthropic":
                return _anthropic_complete(cli, **common)
            elif provider.backend == "openai":
                return _openai_complete(cli, **common)
            else:
                raise ValueError(f"unknown backend {provider.backend!r}")
        except Exception as exc:
            last_exc = exc
            transient = _is_transient(exc)
            log_event("llm", "provider.request_error",
                      provider=provider_name, model=provider_model,
                      error=type(exc).__name__, message=str(exc),
                      transient=transient)
            if not transient:
                raise
            continue

    if last_exc is not None:
        raise RuntimeError(
            f"All LLM providers failed (attempted: {attempted}). Last error: "
            f"{type(last_exc).__name__}: {last_exc}"
        ) from last_exc
    raise RuntimeError(
        "No LLM provider available — none of "
        f"{list(cfg.active)} have a key in Keychain. "
        "Run scripts/setup_secrets.py."
    )


def _extract_anthropic_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        return "".join(parts)
    return str(content)


def healthcheck() -> tuple[str, str]:
    """Round-trip a short call through the active chain. Returns (provider, response).
    The provider returned is whichever one actually answered, not necessarily the primary."""
    cfg = load_config()
    available = [p for p in cfg.active if secrets.get(cfg.providers[p].secret_name)]
    if not available:
        raise RuntimeError(f"No keys in Keychain for any of {list(cfg.active)}")
    text = complete(
        system="Reply with exactly the word OK and nothing else.",
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=5,
    )
    # Best-effort: report the first available provider; the request-error log
    # will show if fallback kicked in.
    return available[0], text.strip()
