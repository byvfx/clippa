"""Model calls for every stage, whichever provider answers (#72).

The model string picks the provider by its prefix:
    claude-sonnet-5-5, anthropic:claude-...  the Claude API (ANTHROPIC_API_KEY)
    openai:<model>                           OpenAI (OPENAI_API_KEY)
    gemini:<model>                           Google, OpenAI-compatible endpoint (GEMINI_API_KEY)
    openrouter:<vendor>/<model>              OpenRouter, one key for many vendors (OPENROUTER_API_KEY),
                                             e.g. openrouter:openai/gpt-5-mini
    local:<model>                            any OpenAI-compatible server at [llm] local_base_url
                                             (Ollama, vLLM, LM Studio; LOCAL_LLM_API_KEY if it
                                             wants one)
Every stage keeps its own model key ([scoring] model_a, [segments] model, ...), so moving a
stage is one config line. The prompts are tuned on Claude: if another model needs different
wording, give that stage a new prompt version rather than editing one.

What differs per provider:
- effort: Claude's output_config.effort ("none" is "low" there); OpenAI-compatible servers
  get reasoning_effort (none/low/medium/high; OpenRouter's reasoning.effort), and a model
  that rejects it is asked again without it. "none" turns a reasoning model's thinking off
  (DeepSeek V4 Flash thinks by default and is cheaper and quicker without).
- structured output: native JSON schema on both; a server without json_schema gets JSON mode
  with the schema in the prompt. Answers from OpenAI-compatible servers are checked against
  the schema and sent back once with what was wrong.
- refusals: (None, meta) from json_call and ("", meta) from text_call everywhere; the
  server-side refusal fallback exists on the Claude API only.
- OpenRouter spending limits (error.metadata.limit_source): openrouter_key_limit (HTTP 402
  or 403) and openrouter_credits (HTTP 402) raise OpenRouterLimit at once. An in-flight
  budget (openrouter_in_flight_budget, HTTP 402) waits for Retry-After and tries again,
  up to the same six attempts as the other retries, then raises OpenRouterLimit.
- images: Claude content blocks ({"type": "image", "source": {"type": "base64", ...}}) are
  converted to image_url data URLs.
Prices are [llm] prices ($ per 1M input/output tokens, by model string), for cost lines only;
OpenRouter reports what each call cost, and that wins over the table. When a call omits that
cost, and for a dry-run, an openrouter: model uses OpenRouter's published list (one fetch per
process, including a failed fetch).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .errors import ClipperError, KeyRejected, OpenRouterLimit

PROVIDERS = {  # prefix -> (base URL, key env var)
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "local": ("", "LOCAL_LLM_API_KEY"),
}
EFFORT = {"none": "none", "low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def claude_effort(effort: str) -> str:
    return "low" if effort == "none" else effort
RETRY_STATUS = (408, 429, 500, 502, 503, 504, 529)
STARTED = (408, 500, 502, 504)  # errors that can come after the model began (and was billed)
ATTEMPTS = 6  # one call plus retries; an in-flight budget uses the same budget
_IN_FLIGHT = "openrouter_in_flight_budget"
MODEL_KEY = re.compile(r"(^|_)model(_[ab])?$")


def prices(cfg: dict) -> dict:
    """The price table the client uses: the built-in defaults plus [llm.prices] (which
    replaces the nested default when load() merges clipper.toml)."""
    from .config import DEFAULTS

    return {**DEFAULTS["llm"]["prices"], **(cfg.get("llm") or {}).get("prices", {})}


def price_of(prices: dict, model: str) -> tuple[float, float] | None:
    """[llm] prices entry for a model string: exact, or the most specific listed prefix, keyed
    with or without the provider ("openai:gpt-5" matches "openai:gpt-5-mini-20260601", and a
    dated claude-sonnet-5-5-20260601 costs as claude-sonnet-5-5)."""
    name = split(model)[1]
    for key in (model, name):
        if key in prices:
            return tuple(prices[key])
    best = max((k for k in prices if model.startswith(k) or name.startswith(split(k)[1])),
               key=lambda k: len(split(k)[1]), default=None)
    return tuple(prices[best]) if best else None


_OPENROUTER_PRICES: dict[str, tuple[float, float]] | None = None
_OPENROUTER_CONTEXT: dict[str, int] = {}
_OPENROUTER_MAX_OUT: dict[str, int] = {}
_OPENROUTER_FAILED_AT = 0.0  # time.monotonic() of the last failed fetch; 0 = the table is good
_OPENROUTER_LOCK = threading.Lock()
_OPENROUTER_RETRY_S = 300


def _openrouter_fresh() -> bool:
    return _OPENROUTER_PRICES is not None and not (
        _OPENROUTER_FAILED_AT and time.monotonic() - _OPENROUTER_FAILED_AT >= _OPENROUTER_RETRY_S)


def openrouter_prices() -> dict[str, tuple[float, float]]:
    """OpenRouter's published $ per 1M input/output tokens by model id (its /models list is
    public). Fetched once per process, by one thread at a time. A failed fetch is kept as {}
    for five minutes, so a dry-run of many rows waits out the timeout at most once, and then
    tried again: serve and the bot run for days, and one network blip shouldn't leave their
    prices, context sizes and output caps empty until a restart. A row that doesn't parse is
    skipped on its own; the rest of the list stands."""
    global _OPENROUTER_PRICES, _OPENROUTER_FAILED_AT
    if _openrouter_fresh():
        return _OPENROUTER_PRICES
    with _OPENROUTER_LOCK:
        if _openrouter_fresh():  # another thread fetched it while this one waited
            return _OPENROUTER_PRICES
        import requests

        try:
            data = requests.get(PROVIDERS["openrouter"][0] + "/models", timeout=15).json()["data"]
            if not isinstance(data, list):
                raise TypeError("/models data is not a list")
        except (requests.RequestException, ValueError, KeyError, TypeError):
            _OPENROUTER_PRICES = _OPENROUTER_PRICES or {}  # only None or an earlier failure gets here
            _OPENROUTER_FAILED_AT = time.monotonic()
            return _OPENROUTER_PRICES
        table, ctx, cap = {}, {}, {}
        for m in data:
            if not isinstance(m, dict):
                continue
            try:
                if m.get("pricing"):
                    table[m["id"]] = (float(m["pricing"]["prompt"]) * 1e6, float(m["pricing"]["completion"]) * 1e6)
            except (KeyError, TypeError, ValueError):
                pass
            try:
                if m.get("context_length"):
                    ctx[m["id"]] = int(m["context_length"])
            except (KeyError, TypeError, ValueError):
                pass
            try:
                if (m.get("top_provider") or {}).get("max_completion_tokens"):
                    cap[m["id"]] = int(m["top_provider"]["max_completion_tokens"])
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        _OPENROUTER_CONTEXT.update(ctx)
        _OPENROUTER_MAX_OUT.update(cap)
        _OPENROUTER_PRICES, _OPENROUTER_FAILED_AT = table, 0.0
    return _OPENROUTER_PRICES


def context_length(model: str) -> int | None:
    """How many tokens a model takes in, where it is published (OpenRouter's list); None
    when unknown (Claude, OpenAI, Gemini, local servers)."""
    if split(model)[0] != "openrouter":
        return None
    openrouter_prices()
    return _OPENROUTER_CONTEXT.get(split(model)[1])


def max_output(model: str) -> int | None:
    """The most a model writes in one answer, where it is published (OpenRouter's list)."""
    if split(model)[0] != "openrouter":
        return None
    openrouter_prices()
    return _OPENROUTER_MAX_OUT.get(split(model)[1])


def known_price(cfg: dict, model: str) -> tuple[float, float] | None:
    """[llm] prices for a model, else OpenRouter's own list for an openrouter: model."""
    p = price_of(prices(cfg), model)
    if p is None and split(model)[0] == "openrouter":
        p = openrouter_prices().get(split(model)[1])
    return p


def provider_key(prov: str, paths: dict | None = None) -> str:
    """A provider's key: its env var ($OPENROUTER_API_KEY, ...), else the file [paths]
    <prov>_key (secrets/openrouter_secret.txt, ...); "" if neither. "anthropic" is
    $ANTHROPIC_API_KEY, else [paths] anthropic_key (the claude container on the NAS keeps it
    out of Claude Code's own environment, where it would stop Remote Control)."""
    env = "ANTHROPIC_API_KEY" if prov == "anthropic" else PROVIDERS[prov][1]
    key = os.environ.get(env, "").strip()
    path = (paths or {}).get(f"{prov}_key")
    if key or not path:
        return key
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def keys_fingerprint(paths: dict | None = None) -> str:
    """A short hash of every model provider's key as this process sees it (env or file), so
    the worker can tell a stream held for a rejected key that a key has changed since. It
    says nothing about the keys themselves. Raises OSError when a key file is there but can't
    be read (a NAS blip), rather than hashing it as no key and seeming to change."""
    import hashlib

    def key(prov: str) -> str:
        env = "ANTHROPIC_API_KEY" if prov == "anthropic" else PROVIDERS[prov][1]
        path = (paths or {}).get(f"{prov}_key")
        k = os.environ.get(env, "").strip()
        if k or not path or not Path(path).exists():
            return k
        return Path(path).read_text(encoding="utf-8").strip()

    keys = "\n".join(f"{p}={key(p)}" for p in ("anthropic", *PROVIDERS))
    return hashlib.sha256(keys.encode()).hexdigest()[:12]


def split(model: str) -> tuple[str, str]:
    """("anthropic" | "openai" | "gemini" | "openrouter" | "local", the provider's own model
    name)."""
    prefix, _, name = model.partition(":")
    if name and (prefix in PROVIDERS or prefix == "anthropic"):
        return prefix, name
    return "anthropic", model


def is_llm(key: str, value: Any) -> bool:
    """A config value that names a chat model (not a TTS, Whisper or lip-sync model)."""
    return (isinstance(value, str) and bool(MODEL_KEY.search(key))
            and (value.startswith(("claude-", "anthropic:claude-")) or split(value)[0] != "anthropic"))


def configured(cfg: dict) -> dict[str, str]:
    """Every chat model the config names, as {"section.key" (or "shows.<s>.section.key"): model}."""
    from .config import _sections

    out = {f"{s}.{k}": v for s, sec in _sections(cfg) for k, v in sec.items() if is_llm(k, v)}
    for show, prof in cfg.get("shows", {}).items():
        for s, sec in prof.get("overrides", {}).items():
            out.update({f"shows.{show}.{s}.{k}": v for k, v in sec.items() if is_llm(k, v)})
    return out


def claude_model(cfg: dict) -> str:
    """A Claude model the config uses, by its API name (to check the Anthropic key against);
    "" when every stage is on another provider."""
    return next((split(m)[1] for m in configured(cfg).values() if split(m)[0] == "anthropic"), "")


def check_schema(schema: dict, v: Any, path: str = "$") -> list[str]:
    """What's wrong with `v` against the subset of JSON schema the stages use (type,
    required, properties, items, enum, anyOf)."""
    if "anyOf" in schema:
        branches = [check_schema(s, v, path) for s in schema["anyOf"]]
        return [] if any(not b for b in branches) else min(branches, key=len)
    t = schema.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    py = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
          "boolean": bool, "null": type(None)}
    if types and not any(isinstance(v, py[x]) and not (isinstance(v, bool) and x in ("integer", "number"))
                         for x in types if x in py):
        return [f"{path} should be {'/'.join(types)}"]
    if "enum" in schema and v not in schema["enum"]:
        return [f"{path} should be one of {schema['enum']}"]
    errs = []
    if isinstance(v, dict):
        errs += [f"{path}.{k} is missing" for k in schema.get("required", []) if k not in v]
        for k, sub in schema.get("properties", {}).items():
            if k in v:
                errs += check_schema(sub, v[k], f"{path}.{k}")
    if isinstance(v, list) and "items" in schema:
        for i, x in enumerate(v):
            errs += check_schema(schema["items"], x, f"{path}[{i}]")
    return errs


def parse_json(text: str) -> Any:
    """The JSON object in an answer, ignoring a code fence or words around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1] if i >= 0 and j > i else text)


def _openai_content(user: str | list[dict]) -> str | list[dict]:
    if isinstance(user, str):
        return user
    out = []
    for b in user:
        if b["type"] == "text":
            out.append({"type": "text", "text": b["text"]})
        elif b["type"] == "image":
            s = b["source"]
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{s['media_type']};base64,{s['data']}"}})
    return out


class NoSchema(Exception):
    """The server doesn't take response_format json_schema."""


def _header(headers, name: str) -> str:
    """A response header by name, whatever case the server or a fake used."""
    wanted = name.lower()
    for key, value in getattr(headers, "items", lambda: ())():
        if str(key).lower() == wanted and value is not None:
            return str(value).strip()
    return ""


def _retry_after_s(headers) -> float | None:
    """Retry-After as seconds, or None when it is missing or not a number of seconds.

    OpenRouter's in-flight budget says how long to wait. The number is kept (a 0 means
    retry at once); an HTTP-date or anything else is not a wait this client can honour.
    Capped at 120, the longest in-flight wait the budget panel reads as one
    (openrouter_budget.retry_seconds), so a stray header cannot hold a stream for hours."""
    raw = _header(headers, "retry-after")
    if not raw:
        return None
    try:
        pause = float(raw)
    except ValueError:
        return None
    if not math.isfinite(pause) or pause < 0:
        return None
    return min(120.0, pause)


def _error_body(response) -> dict | None:
    """The JSON object in an error response, or None when it isn't one. Parsed once."""
    try:
        body = response.json()
    except (ValueError, AttributeError):  # not JSON, or a stand-in response with no json()
        return None
    return body if isinstance(body, dict) else None


def _limit_source(body: dict | None) -> str:
    """error.metadata.limit_source, or "" when the body is not that shape."""
    err = body.get("error") if isinstance(body, dict) else None
    meta = err.get("metadata") if isinstance(err, dict) else None
    source = meta.get("limit_source") if isinstance(meta, dict) else None
    return source if isinstance(source, str) else ""


def _provider_message(body: dict | None, response) -> str:
    """The error message in an already-parsed body, else the body text, one short line."""
    err = body.get("error") if isinstance(body, dict) else None
    message = err.get("message") if isinstance(err, dict) else None
    text = message if isinstance(message, str) and message.strip() else getattr(response, "text", "")
    return " ".join(str(text).split())[:200]


def _raise_limit(model: str, response, source: str, detail: str, *, paths, tries: int | None = None) -> None:
    """Record the stop for the budget panel and raise. A key limit is OpenRouterKeyLimit
    (still an OpenRouterLimit) so the panel and a stage both see it."""
    from . import openrouter_budget as ob

    if paths is not None:
        ob.note_stop({"paths": paths}, status=response.status_code, code=source, model=model)
    if tries:
        message = (f"{model}: OpenRouter limit {source} (HTTP {response.status_code}) "
                   f"after {tries} tries: {detail}")
    else:
        message = (f"{model}: OpenRouter limit {source} (HTTP {response.status_code}), "
                   f"not retried: {detail}")
    if source == "openrouter_key_limit":
        raise ob.OpenRouterKeyLimit(message)
    raise OpenRouterLimit(message, source)


def _openrouter_limit_wait(model: str, response, attempt: int, paths=None,
                           tries: int = ATTEMPTS) -> float | None:
    """None when this response is not a metadata spending limit we handle. A float: sleep, then retry.

    openrouter_key_limit is final on HTTP 402 or 403, and openrouter_credits on 402: those
    stop the run (OpenRouterLimit). openrouter_in_flight_budget on 402 is retried; it is a
    moment's crowding, not a spent limit, so once the tries run out (or there is no
    Retry-After) only this call fails, with a RuntimeError, like any other HTTP error.
    Another status with those sources is left for the caller."""
    status = response.status_code
    if status not in (402, 403):
        return None
    body = _error_body(response)
    source = _limit_source(body)
    if not source:
        return None
    detail = _provider_message(body, response)
    if source == "openrouter_key_limit":
        _raise_limit(model, response, source, detail, paths=paths)
    if source == "openrouter_credits":
        if status != 402:
            return None
        _raise_limit(model, response, source, detail, paths=paths)
    if source != _IN_FLIGHT or status != 402:
        return None
    pause = _retry_after_s(response.headers)
    if pause is None:
        raise RuntimeError(f"{model}: HTTP 402 {source}, no Retry-After: {detail}")
    if attempt + 1 >= tries:
        raise RuntimeError(f"{model}: HTTP 402 {source} after {tries} tries: {detail}")
    return pause


class LLM:
    """One client for every stage; routes each call by its model string. `cost` adds up
    every call made through it (for the stages' cost lines)."""

    def __init__(self, cfg: dict | str = "", label: str = ""):
        """label: the ledger's stage for every call this client makes (serve's chat, "chat"),
        instead of the running command's."""
        from .config import DEFAULTS

        self.label = label
        if isinstance(cfg, dict):
            workspace_id, lc = cfg["scoring"]["workspace_id"], cfg.get("llm") or DEFAULTS["llm"]
            self.ledger = cfg["paths"].get("costs")  # [paths] costs: one line per call
            self.paths = cfg["paths"]
        else:
            workspace_id, lc, self.ledger, self.paths = cfg, DEFAULTS["llm"], None, {}
        # Keys not scoped to a workspace must name one on every request.
        self.workspace_id = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
        self.lc = {**DEFAULTS["llm"], **lc}
        self.prices = prices(cfg)
        self.cost = 0.0
        self._lock = threading.Lock()
        self._client = None
        self._no_effort: set[str] = set()
        self._no_schema: set[str] = set()

    @property
    def client(self):
        """The Anthropic SDK client (Claude models only)."""
        if self._client is None:
            import anthropic

            headers = {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None
            self._client = anthropic.Anthropic(api_key=provider_key("anthropic", self.paths) or None,
                                               max_retries=4, default_headers=headers)
        return self._client

    def _account(self, model: str, tokens_in: int, tokens_out: int,
                 usd: float | None = None) -> dict:
        """Add a call to the cost and the ledger. usd: what the provider says it cost
        (OpenRouter), else known_price: the [llm] prices table, then OpenRouter's list."""
        from . import costs

        if usd is None:
            pin, pout = known_price({"llm": {"prices": self.prices}}, model) or (0.0, 0.0)
            usd = (tokens_in * pin + tokens_out * pout) / 1e6
        with self._lock:
            self.cost += usd
        costs.llm_call(self.ledger, split(model)[0], model, tokens_in, tokens_out, usd, stage=self.label)
        return {"input_tokens": tokens_in, "output_tokens": tokens_out, "usd": usd}

    # ---------- the calls the stages make ----------

    def json_call(self, model: str, system: str, user: str | list[dict], schema: dict,
                  effort: str, fallback: bool = False,
                  stream: bool = False) -> tuple[dict | None, dict]:
        """Structured-output call. Returns (parsed JSON or None on refusal, meta).
        stream=True for long inputs/outputs, so the request can't hit an HTTP timeout.
        fallback: Claude's server-side refusal fallback (ignored elsewhere)."""
        if split(model)[0] != "anthropic":
            return self._compat_json(model, system, user, schema, effort, 64000 if stream else 16000)
        kwargs = {
            "model": split(model)[1], "max_tokens": 64000 if stream else 16000, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": claude_effort(effort), "format": {"type": "json_schema", "schema": schema}},
        }
        if fallback:
            # Server-side refusal fallback (Claude API only); routes by refusal category.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            api = self.client.beta.messages
        else:
            api = self.client.messages
        if stream:
            with api.stream(**kwargs) as s:
                resp = s.get_final_message()
        else:
            resp = api.create(**kwargs)
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            meta["refusal"] = getattr(resp.stop_details, "category", None)
            return None, meta
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), meta

    def text_call(self, model: str, system: str, user: str | list[dict], effort: str,
                  max_tokens: int = 4000, thinking_tokens: int | None = None,
                  timeout_s: float | None = None) -> tuple[str, dict]:
        """Plain text answer; "" on refusal. For a long call on another provider (Clippa
        codereview): thinking_tokens in place of [llm] thinking_max_tokens, and timeout_s in
        place of [llm] timeout_s, after which the call fails rather than being sent again
        (each try of a long thinking call is paid for)."""
        if split(model)[0] != "anthropic":
            data, meta = self._compat(model, self._messages(system, user), effort, max_tokens,
                                      thinking_tokens=thinking_tokens, timeout_s=timeout_s)
            if meta.get("refusal") is not None:
                return "", meta
            return (data["choices"][0]["message"].get("content") or "").strip(), meta
        resp = self.client.messages.create(
            model=split(model)[1], max_tokens=max_tokens, system=system,
            output_config={"effort": claude_effort(effort)}, messages=[{"role": "user", "content": user}])
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            return "", meta
        return "".join(b.text for b in resp.content if b.type == "text").strip(), meta

    def json_text(self, model: str, system: str, user: str, effort: str,
                  max_tokens: int = 32000) -> tuple[dict | None, dict]:
        """A JSON object without a schema (one too large for a structured-output grammar):
        the prompt describes the fields (and asks for JSON only) and the caller checks
        them. None on refusal."""
        if split(model)[0] != "anthropic":
            data, meta = self._compat(model, self._messages(system, user), effort, max_tokens,
                                      {"type": "json_object"})
            if meta.get("refusal") is not None:
                return None, meta
            return parse_json(data["choices"][0]["message"].get("content") or ""), meta
        with self.client.messages.stream(
                model=split(model)[1], max_tokens=max_tokens, system=system,
                output_config={"effort": claude_effort(effort)}, messages=[{"role": "user", "content": user}]) as st:
            resp = st.get_final_message()
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            return None, meta
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        return parse_json("".join(b.text for b in resp.content if b.type == "text")), meta

    def tool_call(self, model: str, system: str, messages: list[dict], tools: list[dict],
                  effort: str = "low", max_tokens: int = 1024) -> tuple[dict, dict]:
        """One turn in which the model may pick one of `tools` (the chat, #158): never more
        than one call, and nothing runs here: the caller checks the pick against its own
        catalog. messages: [{"role": "user"|"assistant", "content": str}], oldest first, the
        last one the user's. tools: [{"name", "description", "input_schema"}]. Returns
        ({"text": what it said, "tool": {"name", "input"} or None}, meta); a refusal is
        {"text": "", "tool": None} with meta["refusal"]."""
        if split(model)[0] != "anthropic":
            fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                     "parameters": t["input_schema"]}} for t in tools]
            msgs = [{"role": "system", "content": system}, *messages]
            data, meta = self._compat(model, msgs, effort, max_tokens, extra={"tools": fns, "tool_choice": "auto"})
            if meta.get("refusal") is not None:
                return {"text": "", "tool": None}, meta
            msg = data["choices"][0]["message"]
            tool = None
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = None
                if fn.get("name") and isinstance(args, dict):
                    tool = {"name": fn["name"], "input": args}
                    break
            return {"text": (msg.get("content") or "").strip(), "tool": tool}, meta
        resp = self.client.messages.create(
            model=split(model)[1], max_tokens=max_tokens, system=system, messages=messages,
            tools=tools, tool_choice={"type": "auto"}, output_config={"effort": claude_effort(effort)})
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            meta["refusal"] = getattr(resp.stop_details, "category", None)
            return {"text": "", "tool": None}, meta
        if resp.stop_reason == "max_tokens":   # a cut-off pick has half its inputs
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        tool = next(({"name": b.name, "input": dict(b.input or {})} for b in resp.content
                     if b.type == "tool_use"), None)
        return {"text": text, "tool": tool}, meta

    def preflight(self, model: str) -> None:
        """One tiny call so account problems (auth, workspace, credits, a wrong URL) stop the
        run with one clear line instead of 60 identical failures."""
        if split(model)[0] != "anthropic":
            try:
                self._compat(model, [{"role": "user", "content": "Say OK."}], "", 256, length_ok=True)
            except RuntimeError as e:
                raise ClipperError(f"{model} unavailable: {e}") from None
            return
        import anthropic

        try:
            self.client.messages.create(model=split(model)[1], max_tokens=16,
                                        messages=[{"role": "user", "content": "Say OK."}])
        except anthropic.AuthenticationError as e:
            raise KeyRejected(f"Claude API rejected the key (ANTHROPIC_API_KEY or [paths] anthropic_key)"
                              f" ({e.status_code}): {e.message}") from None
        except (anthropic.PermissionDeniedError, anthropic.BadRequestError) as e:
            raise ClipperError(f"Claude API unavailable ({e.status_code}): {e.message}") from None

    # ---------- Claude ----------

    def _claude_meta(self, model: str, resp) -> dict:
        return {"model": resp.model, "request_id": getattr(resp, "_request_id", None),
                "stop_reason": resp.stop_reason,
                **self._account(model, resp.usage.input_tokens, resp.usage.output_tokens)}

    # ---------- OpenAI-compatible (OpenAI, Gemini, OpenRouter, local servers) ----------

    @staticmethod
    def _messages(system: str, user: str | list[dict]) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": _openai_content(user)}]

    def _compat_json(self, model: str, system: str, user, schema: dict, effort: str,
                     max_tokens: int) -> tuple[dict | None, dict]:
        msgs = self._messages(system, user)
        rf: dict = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": False}}
        if model in self._no_schema:
            rf = self._json_mode(msgs, schema)
        for attempt in range(2):
            try:
                data, meta = self._compat(model, msgs, effort, max_tokens, rf)
            except NoSchema:
                self._no_schema.add(model)
                rf = self._json_mode(msgs, schema)
                data, meta = self._compat(model, msgs, effort, max_tokens, rf)
            if meta.get("refusal") is not None:
                return None, meta
            text = data["choices"][0]["message"].get("content") or ""
            try:
                out = parse_json(text)
                errs = check_schema(schema, out)
            except ValueError as e:
                out, errs = None, [f"not JSON ({e})"]
            if not errs:
                meta["retried"] = attempt > 0  # the first answer broke the schema
                return out, meta
            if attempt == 0:  # once more, with what was wrong
                msgs += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "That answer doesn't match the JSON schema: "
                          + "; ".join(errs[:12]) + ". Answer again with the corrected JSON only."}]
        raise RuntimeError(f"{model} answered JSON that doesn't match the schema: {'; '.join(errs[:5])}")

    @staticmethod
    def _json_mode(msgs: list[dict], schema: dict) -> dict:
        msgs[0]["content"] += ("\n\nAnswer with one JSON object that matches this JSON schema, "
                               "and nothing else:\n" + json.dumps(schema))
        return {"type": "json_object"}

    def _handle_compat_status(self, model: str, prov: str, env: str, r, body: dict, token_key: str,
                              asked: int, response_format: dict | None, attempt: int,
                              timeout_s: float | None, waits: dict | None = None) -> bool:
        """True: send this request again. False: `r` is the answer to use.

        Raises OpenRouterLimit, KeyRejected, NoSchema or RuntimeError when the call is over.
        waits: this call's own retry record ({"rid": ...}), so the budget panel closes the
        right line when the call ends."""
        # 200 OK never parses the body here: the limit check is only a 402 or 403.
        if prov == "openrouter" and r.status_code in (402, 403):
            wait = _openrouter_limit_wait(model, r, attempt, paths=self.paths)
            if wait is not None:
                self._note_wait(waits, wait, r.status_code)
                time.sleep(wait)
                return True
            # No limit_source: the budget panel's text rules (a 402 that only says
            # "in-flight" or "key limit") still apply.
            if self._openrouter_payment(r, model, attempt, timeout_s, waits):
                return True
        # A long call (timeout_s) that a gateway gave up on may be thinking still, and billed:
        # only a refusal before it started (rate limit, overloaded) is worth sending again.
        if r.status_code in RETRY_STATUS and attempt < ATTEMPTS - 1 and not (
                timeout_s and r.status_code in STARTED):
            time.sleep(min(60.0, float(r.headers.get("retry-after") or 2 ** (attempt + 1))))
            return True
        if r.status_code == 400:
            text = r.text.lower()
            if ("reasoning_effort" in body or "reasoning" in body) and "reasoning" in text:
                self._no_effort.add(model)  # not a reasoning model
                body.pop("reasoning_effort", None)
                body.pop("reasoning", None)
                body[token_key] = asked  # no thinking, so no room for it
                return True
            if body[token_key] > asked and ("max_tokens" in text or "max_completion_tokens" in text):
                body[token_key] = asked  # more than this model writes: the caller's own figure
                return True
            if (response_format or {}).get("type") == "json_schema" and (
                    "response_format" in text or "json_schema" in text):
                raise NoSchema
        # Gemini answers a bad key with a 400 ("API key not valid"), the others with a 401.
        if prov != "local" and (r.status_code == 401 or (
                r.status_code == 400 and prov == "gemini" and "api key not valid" in r.text.lower())):
            raise KeyRejected(f"{model}: {env} (or [paths] {prov}_key) was rejected "
                              f"(HTTP {r.status_code}): {r.text[:200]}")
        if not r.ok:
            raise RuntimeError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
        return False

    def _compat(self, model: str, messages: list[dict], effort: str, max_tokens: int,
                response_format: dict | None = None, length_ok: bool = False,
                thinking_tokens: int | None = None, timeout_s: float | None = None,
                extra: dict | None = None) -> tuple[dict, dict]:
        """One chat/completions request with retries. Returns (response JSON, meta); meta has
        "refusal" when the model refused. An OpenRouter spending limit is handled in
        _handle_compat_status (hard stop, or wait for Retry-After). length_ok: running out of
        max_tokens isn't an error (the preflight, where a reasoning model can spend them all
        thinking). thinking_tokens, timeout_s: this call's own (text_call); with timeout_s, a
        dropped connection (a timeout, a reset, a cut-off answer) or a gateway error (STARTED)
        is not sent again. extra: more of the request body (tool_call's tools)."""
        import requests

        prov, name = split(model)
        base, env = PROVIDERS[prov]
        base = (self.lc["local_base_url"] if prov == "local" else base).rstrip("/")
        if not base:
            raise ClipperError(f"{model}: set [llm] local_base_url in clipper.toml "
                             "(an OpenAI-compatible server, e.g. http://<host>:11434/v1)")
        key = provider_key(prov, self.paths)
        if not key and prov != "local":
            raise ClipperError(f"{model}: set the {env} environment variable "
                               f"(or put the key in [paths] {prov}_key)")
        asked = max_tokens
        if effort and effort != "none" and model not in self._no_effort:
            # Thinking is output: room for it and the answer, within what the model can write
            # (published for OpenRouter; elsewhere a 400 about it falls back to what was asked).
            cap = max_output(model)
            room = thinking_tokens or self.lc["thinking_max_tokens"]
            max_tokens = max(asked, min(room, cap or room))
        limit = "max_completion_tokens" if prov == "openai" else "max_tokens"
        body: dict[str, Any] = {"model": name, "messages": messages, limit: max_tokens, **(extra or {})}
        if response_format:
            body["response_format"] = response_format
        if effort and model not in self._no_effort:
            if prov == "openrouter":
                body["reasoning"] = {"effort": EFFORT.get(effort, "medium")}
            else:
                body["reasoning_effort"] = EFFORT.get(effort, "medium")
        if prov == "openrouter":
            body["usage"] = {"include": True}  # the call's cost in the answer
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if prov == "openrouter":
            headers["X-Title"] = "clippa"
        waits: dict = {}
        try:
            for attempt in range(ATTEMPTS):
                try:
                    r = requests.post(f"{base}/chat/completions", json=body, headers=headers,
                                      timeout=timeout_s or self.lc["timeout_s"])
                except requests.RequestException as e:
                    if attempt == ATTEMPTS - 1 or timeout_s:  # a long call may have started: each try is paid for
                        raise RuntimeError(f"{model}: {type(e).__name__}: {e}") from None
                    time.sleep(2 ** attempt)
                    continue
                if self._handle_compat_status(model, prov, env, r, body, limit, asked, response_format,
                                              attempt, timeout_s, waits):
                    continue
                break
            out = self._compat_done(model, prov, name, r, length_ok)
        except Exception:
            self._openrouter_clear(waits, ok=False)
            raise
        self._openrouter_clear(waits, ok=True)
        return out

    def _compat_done(self, model: str, prov: str, name: str, r, length_ok: bool) -> tuple[dict, dict]:
        """The successful response: cost, refusal, and a cut-off answer."""
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        meta = {"model": f"{prov}:{data.get('model') or name}", "request_id": data.get("id"),
                "stop_reason": choice.get("finish_reason"),
                **self._account(model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                cost if isinstance(cost, (int, float)) else None)}
        refusal = choice["message"].get("refusal")
        if refusal or choice.get("finish_reason") == "content_filter":
            meta["refusal"] = refusal or "content_filter"
        elif choice.get("finish_reason") == "length" and not length_ok:
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        return data, meta

    def _openrouter_payment(self, response, model: str, attempt: int, timeout_s: float | None,
                            waits: dict | None = None) -> bool:
        """A 402/403 from OpenRouter with no limit_source. True: wait and send it again. A spent
        key limit or an account out of credits raises. An in-flight refusal isn't billed, so it is
        sent again even on a long call (timeout_s); once the tries run out it is a plain HTTP error."""
        from . import openrouter_budget as ob

        header = response.headers.get("Retry-After") or response.headers.get("retry-after")
        kind = ob.classify_payment(response.status_code, response.text, header)
        if kind == "openrouter_in_flight_budget":
            wait = _retry_after_s(response.headers)  # capped at two minutes, as with limit_source
            if wait is None:
                raise RuntimeError(f"{model}: HTTP 402 {kind}, no Retry-After: {response.text[:200]}")
            if attempt >= ATTEMPTS - 1:
                raise RuntimeError(f"{model}: HTTP 402 {kind} after {ATTEMPTS} tries: {response.text[:200]}")
            self._note_wait(waits, wait, response.status_code)
            time.sleep(wait)
            return True
        if kind == "openrouter_key_limit":
            ob.note_stop({"paths": self.paths}, status=response.status_code, model=model)
            raise ob.OpenRouterKeyLimit(
                f"openrouter_key_limit: OpenRouter refused {model} (HTTP {response.status_code}). "
                "The clippa key's limit is spent.")
        if kind == "openrouter_credits":
            ob.note_stop({"paths": self.paths}, status=response.status_code, code=kind, model=model)
            raise OpenRouterLimit(
                f"openrouter_credits: OpenRouter refused {model} (HTTP {response.status_code}). "
                "The OpenRouter account is out of credits.", kind)
        return False

    def _note_wait(self, waits: dict | None, seconds: float, status: int) -> None:
        """Put this call's wait on the budget panel, one line per call however often it waits."""
        from . import openrouter_budget as ob

        rid = ob.note_retry({"paths": self.paths}, seconds=seconds, status=status,
                            rid=(waits or {}).get("rid", ""))
        if waits is not None:
            waits["rid"] = rid

    def _openrouter_clear(self, waits: dict, *, ok: bool) -> None:
        """Close this call's wait on the budget panel, if it waited. A call that never waited
        doesn't touch the panel's file."""
        if not waits.get("rid"):
            return
        from . import openrouter_budget as ob

        ob.clear_retry({"paths": self.paths}, waits["rid"], ok=ok)


# Every stage's model calls go through clippa/llm.py, which picks the provider from the
# model string; `Claude` stays the name the stages use.
Claude = LLM


def preflight(claude: LLM, model: str) -> None:
    """One tiny call so account problems stop the run with one clear line."""
    claude.preflight(model)


# ---------- budgets ----------

class OverBudget(Exception):
    pass


class Budget:
    """A spending cap across threads: a call goes ahead only if what is spent, what is in
    flight and its own estimate fit. `spent_before` is what the client had spent before this
    budget began (a caller's client shared with the swarm)."""

    def __init__(self, limit: float, claude, spent_before: float = 0.0):
        self.limit, self.claude, self.held, self.before = limit, claude, 0.0, spent_before
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        return self.claude.cost - self.before

    def reserve(self, est: float) -> None:
        with self._lock:
            if self.spent + self.held + est > self.limit:
                raise OverBudget(f"budget ${self.limit:.2f}: ${self.spent:.2f} spent, "
                                 f"next call ~${est:.3f}")
            self.held += est

    def release(self, est: float) -> None:
        with self._lock:
            self.held -= est


def estimate(cfg: dict, model: str, tokens_in: float, tokens_out: float) -> float:
    pin, pout = known_price(cfg, model) or (0.0, 0.0)
    return (tokens_in * pin + tokens_out * pout) / 1e6


def tokens(text: str) -> int:
    """A rough count for an estimate before spending: four characters a token."""
    return len(text) // 4 + 1


def estimate_lines(cfg: dict, rows: list[tuple[str, str, int, int, int]]) -> list[str]:
    """`--dry-run` lines for a stage's model calls, (label, model, calls, tokens in, tokens out)
    each, then the total. Priced with known_price: the [llm] prices table, then OpenRouter's
    public /models list for an openrouter: name. That list is fetched once per process, and a
    failed fetch is cached, so later rows do not wait on it again. A model still missing counts
    $0 and the line says so; a failed fetch does not raise."""
    out, total = [], 0.0
    for label, model, calls, tin, tout in rows:
        price = known_price(cfg, model)
        pin, pout = price or (0.0, 0.0)
        usd = (tin * pin + tout * pout) / 1e6
        total += usd
        note = "" if price else "  (no [llm] price: counted $0)"
        plural = "s" if calls != 1 else ""
        out.append(f"  {label:<9} {calls:>4} call{plural:<1}  ~{tin / 1000:>6,.0f}k in  ~{tout / 1000:>4,.0f}k out"
                   f"  ~${usd:>5.2f}  {model}{note}")
    out.append(f"  total ~${total:.2f}  (four characters a token, [llm] prices or OpenRouter's "
               "list; nothing was called)")
    return out
