"""Clippa's own error: a problem it can explain in one line (a bad setting, a missing file,
a refused action, a service that said no).

Library code raises ClipperError; only `cli.main` turns it into an exit (the message on
stderr, status 1). Long-running callers (the bot, the war room, the worker) catch it like
any other exception, so one bad stream or button press can't stop them."""


def exit_code(code: int) -> int:
    """ffmpeg's failure is a negative errno, which Windows hands back unsigned (0xBEBEBE37): read
    it as the signed 32-bit value a shell prints, so a message says -1094995529, not 3199971767."""
    return code - 2**32 if code >= 2**31 else code


def did_you_mean(name: str, choices, n: int = 3, cutoff: float = 0.6) -> str:
    """"did you mean a, b?" for the choices nearest a mistyped name, or "" when none is near."""
    import difflib

    near = difflib.get_close_matches(str(name), [str(c) for c in choices], n, cutoff)
    return f"did you mean {', '.join(near)}?" if near else ""


class ClipperError(Exception):
    """`status` is the exit status cli.main uses: 1, unless a caller's contract says
    otherwise (setup's EXIT_SYNC = 3 tells the bootstrap script to install packages)."""

    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status


KEY_REJECTED = 4  # the exit status of a stage whose API key a provider refused (HTTP 401)


class KeyRejected(ClipperError):
    """A provider said the key is wrong (HTTP 401). Not worth retrying with the same key: the
    worker holds the stream until a key changes (worker.pending) instead of spending its
    retries, and DMs at once."""

    def __init__(self, message: str):
        super().__init__(message, status=KEY_REJECTED)


class OpenRouterLimit(ClipperError):
    """OpenRouter refused the call on a spending limit (`error.metadata.limit_source`).

    `openrouter_key_limit` (HTTP 402 or 403) and `openrouter_credits` (HTTP 402) are final:
    the client does not send the request again. `openrouter_in_flight_budget` is raised when
    the response has no Retry-After, or after those waits are used up. `limit_source` is the
    metadata value, so a log line can tell the cases apart. The stage stops (exit 1). This
    is not a rejected key: the key is valid, and holding the stream until the key file
    changes would not clear a credit or key limit."""

    def __init__(self, message: str, limit_source: str):
        super().__init__(message)
        self.limit_source = limit_source


def key_rejected(e: BaseException) -> bool:
    """A refused key: KeyRejected, or the Anthropic SDK's AuthenticationError from a call past
    the preflight. A handler that turns errors into findings or a retry passes these through,
    so they reach cli.main as exit KEY_REJECTED (#281)."""
    return isinstance(e, KeyRejected) or (
        type(e).__name__ == "AuthenticationError" and type(e).__module__.startswith("anthropic"))
