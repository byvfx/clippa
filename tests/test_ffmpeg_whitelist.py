"""Every ffmpeg/ffprobe call that reads a local file pins it to local files (#447).

`-protocol_whitelist file` before an input turns "opens an url named in a file" (a source.ts that
is really an HLS playlist, a concat list naming an address) into "reads a local file or fails".
The scan below reads the package's argv lists, so a new call can't quietly skip it."""
import ast

import pytest
from conftest import ROOT

PIN = "-protocol_whitelist"

# (module, function) -> why its input is not pinned in the argv literal. Each entry must still
# match a call (the last test), so one whose call was fixed or removed can't linger here.
ALLOWED = {
    # An Odysee stream's url on purpose, inside the Modal container: nothing local to pin.
    ("modal_app.py", "transcribe_url"): "url input",
    # src is a stock video's address or a file: a file gets `-protocol_whitelist file` through the
    # `src_opts` list (test_broll checks it), an address keeps ffmpeg's own protocols (https, and
    # HLS for a stream).
    ("broll.py", "length_of"): "address or file",
    ("broll.py", "encode"): "address or file",
}


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def unpinned(elems: list[ast.expr]) -> list[int]:
    """The line of each `-i` in one argv sequence with no `-protocol_whitelist` since the input
    before it (ffmpeg's input options are per input). A lavfi input (`-f lavfi -i color=...`)
    opens no protocol, so it needs none."""
    out, pinned, lavfi = [], False, False
    for e in elems:
        v = _text(e)
        if v == PIN:
            pinned = True
        elif v == "lavfi":
            lavfi = True
        elif v == "-i":
            if not (pinned or lavfi):
                out.append(e.lineno)
            pinned = lavfi = False
    return out


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def findings(source: str) -> list[tuple[str, int]]:
    """(function, line) of every input left unpinned in this module: an `-i` in a list or tuple
    literal, or in the arguments of a call to a function named like ffmpeg (receipts' wrapper),
    and every `["ffprobe", ...]` list (ffprobe's input is positional) without the whitelist."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, fn: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        if isinstance(node, (ast.List, ast.Tuple)):
            found.extend((fn, n) for n in unpinned(node.elts))
            if node.elts and _text(node.elts[0]) == "ffprobe" and not any(_text(e) == PIN for e in node.elts):
                found.append((fn, node.lineno))
        elif isinstance(node, ast.Call) and "ffmpeg" in _callee(node).lower():
            found.extend((fn, n) for n in unpinned(node.args))
        for child in ast.iter_child_nodes(node):
            visit(child, fn)

    visit(ast.parse(source), "<module>")
    return found


def package_findings() -> dict[tuple[str, str], list[int]]:
    out: dict[tuple[str, str], list[int]] = {}
    for path in sorted((ROOT / "clippa").rglob("*.py")):
        rel = path.relative_to(ROOT / "clippa").as_posix()
        for fn, line in findings(path.read_text(encoding="utf-8")):
            out.setdefault((rel, fn), []).append(line)
    return out


def test_every_local_ffmpeg_input_is_pinned_to_files():
    bad = {k: v for k, v in package_findings().items() if k not in ALLOWED}
    assert not bad, ("put \"-protocol_whitelist\", \"file\" before each input (or allow-list a url input "
                     "here, with its reason): " + ", ".join(f"{m}:{n} ({fn})" for (m, fn), ns in bad.items()
                                                         for n in ns))


def test_the_allow_list_has_no_stale_entries():
    found = package_findings()
    stale = [k for k in ALLOWED if k not in found]
    assert not stale, f"no unpinned input left in {stale}: drop them from ALLOWED"


@pytest.mark.parametrize("code, flagged", [
    ('["ffmpeg", "-y", "-i", str(x), str(y)]', True),
    ('["ffprobe", "-v", "error", "-show_entries", "format=duration", str(x)]', True),
    ('["ffmpeg", "-y", "-protocol_whitelist", "file", "-i", str(x), "-i", str(y), str(z)]', True),
    ('ffmpeg("-y", "-i", str(x), str(y))', True),
    ('inputs += ["-i", str(x)]', True),
    ('["ffmpeg", "-y", "-protocol_whitelist", "file", "-ss", "1", "-i", str(x), str(y)]', False),
    ('["ffmpeg", "-protocol_whitelist", "file", "-i", a, "-protocol_whitelist", "file", "-i", b, c]', False),
    ('["ffprobe", "-v", "error", "-protocol_whitelist", "file", str(x)]', False),
    ('["ffmpeg", "-f", "lavfi", "-i", "color=c=black", "-f", "null", "-"]', False),
    ('parser.add_argument("-i", "--interactive")', False),
], ids=["bare-input", "bare-ffprobe", "second-input-bare", "wrapper-call", "input-list",
        "pinned", "both-pinned", "pinned-ffprobe", "lavfi", "not-ffmpeg"])
def test_the_scan_tells_a_pinned_input_from_a_bare_one(code, flagged):
    assert bool(findings(f"def f():\n    {code}\n")) is flagged
