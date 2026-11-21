"""Approval by silence (#259 step B): `clippa review auto`, and serve's job that runs it.

An item approves itself when its show has [review] auto_approve on, nobody has acted on it,
its pre-flight checks (review_check) are clean, its score is at least its kind's floor
([review] auto_min_<kind>; 0 leaves that kind to a person) and it has been in review
auto_grace_minutes. A pre-show goes at once (auto_preshow = "now"): it is public the moment it
uploads, so nobody has seen one first. Stories and one-offs always wait for a person.

Anything a person does to an item (any action through clippa/actions.py, a decision made in
Kitsu, an edited column, a hold, a retake note) leaves it to a person for good. The approval
is a Kitsu decision like any other (actions.approve, who "auto"), its rule in the comment.

auto.json per stream, {key: {...}}:
    seen         when the job first saw it pending without a published_at (older publishes)
    out, out_at  why it waits for a person now, for good
    claimed_at   an approval was sent to Kitsu and its answer never came back (a timeout): if
                 Kitsu took it, the next pass counts it as automatic (approved_at), not a person's
    approved_at  when it approved itself; undone: what a person did to it after that
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import jsonstate
from .config import base, for_show, for_stream

STATE = "auto.json"
EVERY_S = 600          # serve's job
RECHECK_S = 3600       # findings are checked again at most this often per stream
RECHECK = "recheck.json"   # per stream, {"at": the last recheck, "gone": {...}}: serve runs each pass as a new process
WHO = "auto"           # the actions' `who` for an automatic approval
# The approval's Kitsu calls, all of them, under auto.json's lock: a person's action waits
# jsonstate.WAIT_S (60 s) for that lock. The deadline is a requests timeout, which bounds each
# connect and each wait for data, not a whole response: one that keeps trickling in is not cut
# (Kitsu's answers here are a few hundred bytes of JSON).
KITSU_S = 25
KINDS = ("short", "pick", "segment", "compilation")


def path(sdir: Path) -> Path:
    return sdir / STATE


def load(sdir: Path, strict: bool = False) -> dict[str, dict]:
    """auto.json. strict (deciding to approve): a broken file raises rather than reading as
    "nobody acted", which would hide a person's opt-out; display callers read it leniently."""
    return jsonstate.read(path(sdir), {}) if strict else jsonstate.read_or(path(sdir), {})


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def touch(sdir: Path, key: str, what: str, who: str = "") -> None:
    """A person acted on the item (actions.py calls this for every action but an automatic
    approval): it waits for a person from now on, or, approved automatically already, the
    approval was undone by hand (the audit counts those)."""
    if who == WHO:
        return
    why = f"{what} by {who}" if who else what
    with jsonstate.update(path(sdir), {}) as state:
        e = state.setdefault(key, {})
        if e.get("approved_at") and what != "approve":
            e.setdefault("undone", why)
            e.setdefault("undone_at", now_iso())
        if not e.get("out") and not (e.get("approved_at") and what == "approve"):
            e["out"], e["out_at"] = why, now_iso()   # for good, a later re-render included


def republished(sdir: Path, key: str) -> None:
    """A new revision went up (publish sets the task back to wfa): an automatic approval of the
    old one no longer stands, so the item is judged again (kept as `was_approved_at`). review.json
    says so too, since no sync may follow (a publish without cards): left "approved", the next
    pass would take it for a person's decision."""
    try:
        with jsonstate.update(path(sdir), {}) as state:
            e = state.get(key)
            if e:
                e.pop("claimed_at", None)   # a lost answer about the old revision
            if not (e and e.get("approved_at")):
                return
            e["was_approved_at"] = e.pop("approved_at")
        with jsonstate.update(sdir / "review.json", {}) as rv:
            if key in rv:
                rv[key].update(decision="pending", status="wfa")
    except (OSError, ValueError) as e:
        print(f"{sdir.name}/{key}: auto.json not updated for the new revision: {e}")


def floor_of(rc: dict, kind: str) -> float:
    """[review] auto_min_<kind> as a number; 0 (a person decides) when unset or not a number."""
    try:
        return float(rc.get(f"auto_min_{kind}") or 0)
    except (TypeError, ValueError):
        return 0.0


def enabled_anywhere(cfg: dict) -> bool:
    """Is any show (or the base config) on? serve's job runs only then."""
    root = base(cfg)
    return bool(root["review"]["auto_approve"]) or any(
        for_show(root, key)["review"]["auto_approve"] for key in root["shows"])


def stream_kind(sdir: Path) -> str:
    """"stream" (a recorded show), "preshow", "oneoff" (a pre-show grabbed with a window) or
    meta.json's own kind (a story)."""
    meta = jsonstate.read_or(sdir / "meta.json", {})
    kind = meta.get("kind") or "stream"
    return "oneoff" if kind == "preshow" and meta.get("window") else kind


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso or "")
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def score_of(st: dict, r: dict) -> float | None:
    raw = (r.get("fields") or {}).get("Score") or (st.get("fields") or {}).get("Score")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class Verdict:
    key: str
    state: str          # approved | due | waiting | person | decided | off
    why: str = ""
    at: datetime | None = None   # waiting: when it approves itself; approved: when it did
    lasting: bool = False        # a person's doing (a hold, an edit, a note): out for good once seen
    on_checks: bool = False      # it waits only on its checks: findings, or a run that failed

    def as_dict(self) -> dict:
        return {"key": self.key, "state": self.state, "why": self.why,
                "at": self.at.isoformat(timespec="seconds") if self.at else ""}


def grace_for(rc: dict, kind_of_stream: str, st: dict, r: dict) -> tuple[float | None, str]:
    """(minutes of grace, "") when this kind of item may approve itself; (None, why) when it
    waits for a person: a pre-show by auto_preshow, a stream's item by its kind's score floor."""
    if kind_of_stream == "preshow":
        if rc["auto_preshow"] == "off":
            return None, "pre-shows wait for a person ([review] auto_preshow)"
        return (0 if rc["auto_preshow"] == "now" else rc["auto_grace_minutes"]), ""
    if kind_of_stream != "stream":
        return None, f"{'one-offs' if kind_of_stream == 'oneoff' else kind_of_stream + 's'} wait for a person"
    floor = floor_of(rc, st["kind"])
    if not floor:
        return None, f"{st['kind']}s wait for a person ([review] auto_min_{st['kind']})"
    score = score_of(st, r)
    if score is None or score < floor:
        return None, f"score {score:g} under {floor:g}" if score is not None else "no score"
    return rc["auto_grace_minutes"], ""


def verdict(cfg: dict, kind_of_stream: str, key: str, st: dict, r: dict, check: dict | None,
            auto: dict, retaken: bool, now: datetime) -> Verdict:
    """One item's state. cfg: the stream's; st: its review_state.json entry, r: review.json's
    (as last synced), check: checks.json's, auto: auto.json's."""
    from .actions import is_held

    rc = cfg["review"]
    decision = r.get("decision") or "pending"
    if auto.get("approved_at"):
        return Verdict(key, "approved", "approved automatically", _when(auto["approved_at"]))
    if auto.get("claimed_at") and not auto.get("out") and decision == "approved":
        return Verdict(key, "approved", "approved automatically", _when(auto["claimed_at"]))
    if not rc["auto_approve"]:
        return Verdict(key, "off")
    if decision != "pending":
        return Verdict(key, "decided", decision.replace("_", " "))
    if auto.get("out"):
        return Verdict(key, "person", auto["out"])
    fields = r.get("fields") or {}
    if is_held(fields):
        # Clippa's own hold (other voices) can lift by itself; a person's, or their release of
        # Clippa's, differs from the proposal and also shows as edited: that one lasts
        mine = is_held(st.get("fields") or {})
        return Verdict(key, "person", "held for other voices" if mine else "held", lasting=not mine)
    if r.get("edited"):
        return Verdict(key, "person", "edited in Kitsu: " + ", ".join(r["edited"]), lasting=True)
    if retaken:
        return Verdict(key, "person", "re-cut after a note", lasting=True)
    grace, why = grace_for(rc, kind_of_stream, st, r)
    if grace is None:
        return Verdict(key, "person", why)
    if not check:
        return Verdict(key, "person", "not checked yet")
    if not check.get("sig"):
        return Verdict(key, "person", "its checks didn't run", on_checks=True)
    if check.get("findings"):
        return Verdict(key, "person", f"{len(check['findings'])} check finding(s)", on_checks=True)
    since = _when(st.get("published_at")) or _when(auto.get("seen"))
    if since is None:   # published before published_at existed: the next pass starts its grace
        return Verdict(key, "waiting", "its grace starts at the next pass")
    due = since + timedelta(minutes=grace)
    return Verdict(key, "due" if now >= due else "waiting", "", due)


def verdicts(cfg: dict, sdir: Path, now: datetime | None = None, state: dict | None = None,
             decisions: dict | None = None, checks: dict | None = None,
             strict: bool = False) -> dict[str, Verdict]:
    """Every published item of a stream. cfg: any (the stream's own is resolved here); state,
    decisions, checks: review_state.json, review.json and checks.json when already loaded;
    strict: a broken auto.json raises rather than reading as empty (to approve by it)."""
    from . import review_check

    cfg = for_stream(cfg, sdir)
    now = now or datetime.now(UTC)
    state = jsonstate.read_or(sdir / "review_state.json", {}) if state is None else state
    decisions = jsonstate.read_or(sdir / "review.json", {}) if decisions is None else decisions
    checks = review_check.load(sdir) if checks is None else checks
    auto = load(sdir, strict)
    retaken = set(jsonstate.read_or(sdir / "retakes.json", {}))
    kind = stream_kind(sdir)
    return {key: verdict(cfg, kind, key, st, decisions.get(key, {}), checks.get(key), auto.get(key, {}),
                         key in retaken, now) for key, st in state.items()}


def note(v: Verdict | dict | None, tz: str) -> str:
    """The line a card or the war room shows, "" when there's nothing to say."""
    if not v:
        return ""
    v = v.as_dict() if isinstance(v, Verdict) else v
    at = _when(v.get("at"))
    local = at.astimezone(ZoneInfo(tz)).strftime("%a %H:%M") if at else ""
    return {"approved": f"approved itself {local}".strip(),
            "due": "approves itself at the next pass unless you act",
            "waiting": (f"approves itself {local} unless you act" if local
                        else f"approves itself unless you act ({v.get('why')})"),
            "person": f"waits for you: {v.get('why')}"}.get(v.get("state"), "")


def _remember(sdir: Path, vs: dict[str, Verdict], state: dict, now: datetime) -> None:
    """Stamp what the next pass must remember: when an older publish was first seen, and what a
    person did outside Clippa (a decision, a hold, an edit or a note, in Kitsu say), so undoing
    it later (a reopen, a release) still leaves the item to them."""
    auto = load(sdir, strict=True)
    seen = [k for k, v in vs.items() if v.state in ("due", "waiting") and not state[k].get("published_at")
            and not auto.get(k, {}).get("seen")]
    acted = {k: f"{v.why} by a person" if v.state == "decided" else v.why for k, v in vs.items()
             if (v.state == "decided" or v.lasting) and not auto.get(k, {}).get("out")
             and not auto.get(k, {}).get("approved_at")}
    # an approval whose answer was lost, that Kitsu took after all: the audit counts it as ours
    landed = [k for k, v in vs.items() if v.state == "approved" and not auto.get(k, {}).get("approved_at")]
    if not seen and not acted and not landed:
        return
    with jsonstate.update(path(sdir), {}) as data:
        for k in seen:
            data.setdefault(k, {}).setdefault("seen", now_iso(now))
        for k, why in acted.items():
            e = data.setdefault(k, {})
            if not e.get("out"):
                e["out"], e["out_at"] = why, now_iso(now)
        for k in landed:
            e = data.get(k) or {}
            if e.get("claimed_at") and not e.get("out") and not e.get("approved_at"):
                e["approved_at"] = e.pop("claimed_at")


def recheck_due(sdir: Path, now: datetime) -> bool:
    """Is a recheck due (RECHECK_S since the last)? Stamps now when it is, under the file's lock,
    so two passes that overlap don't both run one."""
    with jsonstate.update(sdir / RECHECK, {}) as st:
        last = _when(st.get("at"))
        if last and (now - last).total_seconds() < RECHECK_S:
            return False
        st["at"] = now_iso(now)
    return True


def _column_trouble(sdir: Path, say):
    """review_check.run's `say` for a pass: its per-item lines dropped, a Kitsu column it
    couldn't write passed on."""
    return lambda s: say(f"  {sdir.name}: {s.strip()}") if s.startswith("  Kitsu Checks column") else None


def flagged(sdir: Path) -> list[str]:
    """A show without auto_approve: its pending items with findings or a failed run, which a
    recheck keeps current (the war room's ⚠ and the card read them)."""
    from . import review_check

    state = jsonstate.read_or(sdir / "review_state.json", {})
    decisions = jsonstate.read_or(sdir / "review.json", {})
    checks = review_check.load(sdir)
    return [k for k in state if (decisions.get(k, {}).get("decision") or "pending") == "pending"
            and (c := checks.get(k)) and (c.get("findings") or not c.get("sig"))]


def recheck(cfg: dict, sdir: Path, keys: list[str], now: datetime, say=print) -> bool:
    """Check these items again (those that wait only on their checks), at most every RECHECK_S
    per stream; True when it ran. Findings stay in checks.json until something checks the item
    again, and only a publish or a due item did: findings from older check code
    (kick-137715636's, 11-14) kept items out of the window for good. review_check.run skips an
    item whose signature is unchanged, so a finding that still holds costs no vision call. An
    item never checked waits for a person as before, and nothing runs with [review] checks off.
    What changed goes to the Kitsu Checks column too. An item that is no longer one of the
    stream's review items (review.items) can't be checked: it is left out until its checks.json
    entry changes, rather than asked for every hour."""
    from . import review_check

    if not keys or not cfg["review"]["checks"]:
        return False
    have = review_check.load(sdir)
    gone = jsonstate.read_or(sdir / RECHECK, {}).get("gone") or {}
    keys = [k for k in keys if k not in gone or gone[k] != (have.get(k) or {}).get("at", "")]
    if not keys or not recheck_due(sdir, now):
        return False
    try:
        found = review_check.run(sdir, cfg, keys=keys, kitsu=True, say=_column_trouble(sdir, say))
    except Exception as e:  # noqa: BLE001  # the due items still go; the next recheck tries again
        say(f"  {sdir.name}: checks not run again: {type(e).__name__}: {e}")
        return False
    missing = {k: (have.get(k) or {}).get("at", "") for k in keys if k not in found}
    published = set(jsonstate.read_or(sdir / "review_state.json", {}))
    with jsonstate.update(sdir / RECHECK, {}) as st:
        st["gone"] = {k: at for k, at in {**(st.get("gone") or {}), **missing}.items()
                      if (k in missing or k not in found) and k in published}
    return True


def _mirror(sdir: Path, key: str, say=print) -> None:
    """Kitsu has the automatic approval but the item couldn't be re-read: say "approved" in
    review.json, so the replan after this pass places it. Under auto.json's lock, and only while
    the approval stands (nobody has acted since: touch notes that under the same lock) and
    review.json still has the item pending; anything newer is left for the next sync."""
    try:
        with jsonstate.locked(path(sdir)):
            e = load(sdir, strict=True).get(key, {})
            if not e.get("approved_at") or e.get("out") or e.get("undone"):
                return
            with jsonstate.update(sdir / "review.json", {}) as rv:
                if (rv.get(key, {}).get("decision") or "pending") == "pending" and key in rv:
                    rv[key]["decision"] = "approved"
    except (OSError, ValueError) as e2:
        say(f"  {sdir.name}/{key}: review.json not updated either: {e2}")


def approve_due(cfg: dict, sdir: Path, keys: list[str], say=print) -> list[str]:
    """Approve the due items of one stream: Kitsu first (what a person did since the last sync
    counts), the checks again (an item re-rendered since is checked anew, a cover's vision call
    included), then each one still due. Returns the keys approved."""
    from . import actions, review, review_check

    cfg = for_stream(cfg, sdir)
    review.sync(sdir, cfg, keys=keys, quiet=True)
    review_check.run(sdir, cfg, keys=keys, kitsu=True, say=_column_trouble(sdir, say))
    now = datetime.now(UTC)
    state = jsonstate.read_or(sdir / "review_state.json", {})
    decisions = jsonstate.read_or(sdir / "review.json", {})
    vs = verdicts(cfg, sdir, now, state=state, decisions=decisions, strict=True)
    _remember(sdir, vs, state, now)   # a hold or an edit the sync just found is a person's for good
    rc = cfg["review"]
    done = []
    for key in keys:
        v = vs.get(key)
        if not v or v.state != "due":
            continue
        score = score_of(state[key], decisions.get(key, {}))
        rule = (f"no action in {rc['auto_grace_minutes']} min, checks clean"
                + (f", score {score:g}" if score is not None else ""))
        item = actions.Item(sdir, key, state[key])
        try:
            # Under auto.json's lock: a person's action notes itself there (touch) before its Kitsu
            # write, so it either landed first and is seen here, or waits and lands after this.
            # Only the decision itself: its Kitsu calls give up after KITSU_S, well inside the
            # wait a person's touch allows (and the lock's stale age), and the re-read of the
            # item happens after the lock is let go.
            with jsonstate.locked(path(sdir)):
                if load(sdir, strict=True).get(key, {}).get("out"):
                    continue
                with jsonstate.update(path(sdir), {}) as data:
                    data.setdefault(key, {})["claimed_at"] = now_iso()
                with review.kitsu_deadline(KITSU_S):
                    actions.approve(cfg, item, f"Approved automatically: {rule} ([review] auto_approve)",
                                    who=WHO, sync=False)
                with jsonstate.update(path(sdir), {}) as data:
                    e = data.setdefault(key, {})
                    e.pop("claimed_at", None)
                    e["approved_at"] = now_iso()
        except Exception as e:  # noqa: BLE001  # the rest still go, and what went still replans
            say(f"  {sdir.name}/{key}: not approved: {type(e).__name__}: {e}")
            continue
        try:
            actions.refresh(cfg, item)
        except Exception as e:  # noqa: BLE001  # approved in Kitsu; the next sync reads it
            say(f"  {sdir.name}/{key}: approved, but not re-read from Kitsu: {type(e).__name__}: {e}")
            _mirror(sdir, key, say)
        say(f"  {sdir.name}/{key}: approved ({rule})")
        done.append(key)
    return done


def run(cfg: dict, stream: str | None = None, apply: bool = True, say=print,
        now: datetime | None = None) -> dict[str, list[dict]]:
    """Every stream in review whose show has auto_approve on: approve what is due (apply), and
    {stream: [verdict dicts]} for those still open. Replans once when anything approved."""
    from . import actions

    now = now or datetime.now(UTC)
    dirs = [d for d in actions.review_dirs(cfg) if stream in (None, d.name)]
    out: dict[str, list[dict]] = {}
    approved = 0
    for sdir in dirs:
        scfg = for_stream(cfg, sdir)
        if not scfg["review"]["auto_approve"]:
            if apply:   # its ⚠ badges still go stale: keep the findings current, nothing else
                try:
                    recheck(scfg, sdir, flagged(sdir), now, say)
                except Exception as e:  # noqa: BLE001  # the next pass tries again
                    say(f"  {sdir.name}: checks not run again: {type(e).__name__}: {e}")
            continue
        try:   # one stream's trouble (a broken auto.json, Kitsu down) mustn't stop the rest
            vs = verdicts(scfg, sdir, now, strict=apply)
            if apply:
                if recheck(scfg, sdir, [k for k, v in vs.items() if v.on_checks], now, say):
                    vs = verdicts(scfg, sdir, now, strict=True)
                _remember(sdir, vs, jsonstate.read_or(sdir / "review_state.json", {}), now)
            due = [k for k, v in vs.items() if v.state == "due"]
            if due and apply:
                approved += len(approve_due(scfg, sdir, due, say))
                vs = verdicts(scfg, sdir)
        except Exception as e:  # noqa: BLE001  # the next pass tries again
            say(f"  {sdir.name}: skipped: {type(e).__name__}: {e}")
            continue
        rows = [v.as_dict() for v in vs.values() if v.state in ("due", "waiting", "person")]
        if rows:
            out[sdir.name] = rows
    if approved and apply:
        from . import schedule

        schedule.run(base(cfg), sync_first=False)   # every show's schedule, not one stream's view
    return out


def lines(found: dict[str, list[dict]], tz: str) -> list[str]:
    out = []
    for stream, rows in found.items():
        out.append(stream)
        out += [f"  {r['key']}: {note(r, tz)}" for r in rows]
    return out or ["nothing waiting on auto-approval"]


# ---------- the audit and the checkup ----------

def history(cfg: dict) -> list[dict]:
    """Every automatic approval: {stream, key, at, undone (what a person did after, or the
    decision it has now when that isn't approved, or a retake), since}."""
    from .actions import is_held

    out = []
    for f in sorted(Path(cfg["paths"]["streams"]).glob(f"*/{STATE}")):
        sdir = f.parent
        decisions = jsonstate.read_or(sdir / "review.json", {})
        retakes = jsonstate.read_or(sdir / "retakes.json", {})
        for key, e in jsonstate.read_or(f, {}).items():
            at = e.get("approved_at") or e.get("was_approved_at")   # was: a new revision since
            if not at:
                continue
            r = decisions.get(key) or {}
            dec = r.get("decision") or "approved"
            undone = e.get("undone") or ""
            if not undone and e.get("approved_at"):   # its decision now (a re-render resets it)
                undone = {"approved": "", "pending": "reopened",
                          "changes_requested": "sent back for changes"}.get(dec, dec)
                if not undone and is_held(r.get("fields") or {}):
                    undone = "held"   # a hold set in Kitsu, which leaves the decision approved
            since = _when(at) or datetime.min.replace(tzinfo=UTC)
            if not undone and any((_when(h.get("at")) or since) > since for h in retakes.get(key) or []):
                undone = "re-cut after a note"
            out.append({"stream": sdir.name, "key": key, "at": at, "undone": undone})
    return out
