"""Language-model access for reading tasks only: extraction, mapping proposals, patch proposals.

The model reads and proposes. Every proposal is validated downstream by a schema, a span
check, a dry run or a Witness observation, and no verdict is ever produced here.

Primary: Claude through the official Anthropic SDK with schema-constrained output.
Fallback: a local open model served by Ollama (INQUEST_LOCAL_LLM_URL, INQUEST_LOCAL_LLM_MODEL),
using Ollama's JSON-schema `format` constraint.
"""
from __future__ import annotations

import json
import os
import time
from typing import TypeVar

from pydantic import BaseModel

from . import config, store

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


def _anthropic_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def status() -> dict:
    return {
        "anthropic": _anthropic_configured(),
        "model": config.LLM_MODEL,
        "local": bool(config.LOCAL_LLM_URL and config.LOCAL_LLM_MODEL),
        "local_model": config.LOCAL_LLM_MODEL or None,
    }


def available() -> bool:
    s = status()
    return s["anthropic"] or s["local"]


def _record(kind: str, provider: str, usage: dict, seconds: float) -> None:
    store.bump("llm_calls")
    store.bump(f"llm_input_tokens", int(usage.get("input_tokens", 0) or 0))
    store.bump(f"llm_output_tokens", int(usage.get("output_tokens", 0) or 0))
    log = store.kv_get("llm_log", [])
    log.append({"t": time.time(), "kind": kind, "provider": provider, "seconds": round(seconds, 2), **usage})
    store.kv_set("llm_log", log[-200:])


def _claude(system: str, user: str, schema: type[T], kind: str, max_tokens: int) -> T:
    import anthropic

    client = anthropic.Anthropic()
    t0 = time.time()
    try:
        resp = client.beta.messages.parse(
            model=config.LLM_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("the Anthropic API key was rejected") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMUnavailable("the Anthropic API key lacks permission for this model") from exc
    except anthropic.NotFoundError as exc:
        raise LLMUnavailable(f"model {config.LLM_MODEL} is not available to this key") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("rate limited by the Anthropic API; retry shortly") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable("could not reach the Anthropic API") from exc
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
             "model": resp.model}
    _record(kind, "anthropic", usage, time.time() - t0)
    if resp.stop_reason == "refusal":
        raise LLMUnavailable("the model declined this request")
    if resp.stop_reason == "max_tokens":
        raise LLMUnavailable("the response was truncated at max_tokens")
    parsed = resp.parsed_output
    if parsed is None:
        raise LLMUnavailable("the response did not match the requested schema")
    return parsed


def _ollama(system: str, user: str, schema: type[T], kind: str, max_tokens: int) -> T:
    import httpx

    t0 = time.time()
    body = {
        "model": config.LOCAL_LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema.model_json_schema(),
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0},
    }
    try:
        r = httpx.post(config.LOCAL_LLM_URL.rstrip("/") + "/api/chat", json=body, timeout=900)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"local model request failed: {exc}") from exc
    data = r.json()
    _record(kind, "local", {"input_tokens": data.get("prompt_eval_count", 0),
                            "output_tokens": data.get("eval_count", 0), "model": config.LOCAL_LLM_MODEL},
            time.time() - t0)
    try:
        return schema.model_validate(json.loads(data["message"]["content"]))
    except Exception as exc:
        raise LLMUnavailable(f"local model output did not match the schema: {exc}") from exc


def structured(system: str, user: str, schema: type[T], kind: str, max_tokens: int = 16000) -> T:
    """One schema-constrained completion. Claude first, the local model when Claude is not configured
    or unreachable."""
    errors = []
    if _anthropic_configured():
        try:
            return _claude(system, user, schema, kind, max_tokens)
        except LLMUnavailable as exc:
            errors.append(f"Claude: {exc}")
    if config.LOCAL_LLM_URL and config.LOCAL_LLM_MODEL:
        try:
            return _ollama(system, user, schema, kind, max_tokens)
        except LLMUnavailable as exc:
            errors.append(f"local: {exc}")
    if not errors:
        raise LLMUnavailable("no language model is configured: set ANTHROPIC_API_KEY in .env, or "
                             "INQUEST_LOCAL_LLM_URL and INQUEST_LOCAL_LLM_MODEL for a local Ollama model")
    raise LLMUnavailable("; ".join(errors))
