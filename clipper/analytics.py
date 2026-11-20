"""YouTube performance of uploaded videos, fed back into scoring.

Usage:
    clipper analytics             refresh analytics.json for every uploaded schedule entry
    clipper analytics --sign-in   once: grant read-only YouTube Analytics (adds 7-day views,
                                  average view percentage, watch time, subscribers gained)
    clipper analytics --list      print what's in analytics.json, best first
    clipper analytics --report [--month 2026-10] [--refresh]
                                  the month: channel totals, where views came from, 7-day
                                  numbers of its uploads by show/kind/category/day/hour, best
                                  and weakest, and what predicts views (lift.py) ->
                                  reports/analytics/<month>.md + .csv (#42)
    clipper analytics --lift [--show KEY] [--kind segment] [--json]
                                  what predicts views, every upload so far (lift.py, #258)

Views, likes and comments come from the YouTube Data API with the upload sign-in. Once a
video is 7 days old, its first-7-days numbers come from the YouTube Analytics API (needs
--sign-in once) and stay fixed. analytics.json sits next to schedule.json.

Scoring (Pass B) gets a short "how past uploads did" block per show once at least
[analytics] min_videos of that show's uploads have 7-day numbers: the best and worst by
7-day views, with their category and score, as a hint about what this audience watches.
`clipper serve` refreshes it every [analytics] auto_hours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import jsonstate
from .config import base, for_stream
from .errors import ClipperError
from .lift import D7_DAYS, median
from .states import split_key

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
# Google answers a 500 backendError now and then that works a second later (2026-11-20: one
# video's 7-day query failed the whole run). Every call retries that itself: googleapiclient's
# num_retries backs off on 5xx, 429, rate-limit 403s and dropped connections (about a minute
# at most). A video whose query still fails is skipped and tried again next run; GIVE_UP runs
# in a row and the run fails, so serve DMs about it.
RETRIES = 5
GIVE_UP = 3
RATE_LIMITS = {"rateLimitExceeded", "userRateLimitExceeded"}  # 403s, not quotaExceeded


def transient(e: Exception) -> bool:
    """An error a later run will likely not see: a 5xx, a 429 or a rate-limit 403 from
    Google, or the network (a timeout, a dropped connection, DNS)."""
    from googleapiclient.errors import HttpError
    from httplib2 import HttpLib2Error

    from .youtube import RETRY_STATUS

    if isinstance(e, HttpError):
        if e.resp.status in RETRY_STATUS | {429}:
            return True
        reasons = {d.get("reason") for d in e.error_details if isinstance(d, dict)} \
            if isinstance(e.error_details, list) else set()
        return e.resp.status == 403 and bool(reasons & RATE_LIMITS)
    return isinstance(e, (OSError, HttpLib2Error))  # TimeoutError, ConnectionError are OSErrors


def path_of(cfg: dict) -> Path:
    return Path(cfg["paths"]["schedule"]).with_name("analytics.json")


def load(cfg: dict) -> dict:
    return jsonstate.read(path_of(cfg), {})


def credentials(cfg: dict, sign_in: bool = False):
    from google.oauth2.credentials import Credentials

    from . import youtube

    paths = cfg["paths"]
    if sign_in:  # the upload scopes plus analytics, so one token serves both
        scopes = youtube.SCOPES
        youtube.SCOPES = [*scopes, ANALYTICS_SCOPE]
        try:
            return youtube.credentials(paths, sign_in=True)
        finally:
            youtube.SCOPES = scopes
    creds = youtube.credentials(paths)
    granted = jsonstate.load(Path(paths["youtube_token"])).get("scopes") or []
    if ANALYTICS_SCOPE in granted:
        creds = Credentials.from_authorized_user_file(str(paths["youtube_token"]))
    return creds, ANALYTICS_SCOPE in granted


def entry_details(cfg: dict, e: dict) -> dict:
    sdir = cfg["paths"]["streams"] / e["stream"]
    key = split_key(e["key"])[1]
    fields = (jsonstate.read(sdir / "review.json", {}).get(key) or {}).get("fields") or {}
    sc = for_stream(cfg, sdir) if sdir.exists() else cfg
    return {"show": sc["show"]["key"], "show_name": sc["meta"]["show_name"],
            "category": fields.get("Category") or "", "score": fields.get("Score") or "",
            "topic": fields.get("Topic") or ""}


def run(cfg: dict, sign_in: bool = False) -> dict:
    from googleapiclient.discovery import build

    cfg = base(cfg)
    if sign_in:
        credentials(cfg, sign_in=True)
        print("signed in with YouTube Analytics access")
    creds, has_analytics = credentials(cfg)
    sched = jsonstate.read(Path(cfg["paths"]["schedule"]), {})
    up = [e for e in sched.get("entries", []) if e.get("status") == "uploaded" and e.get("video_id")]
    data = load(cfg)
    if not up:
        print("no uploaded videos yet")
        return data
    from .youtube import service

    yt = service(cfg["paths"], creds=creds)  # counted against the day's Data API quota
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False) if has_analytics else None
    now = datetime.now(UTC)
    by_id = {e["video_id"]: e for e in up}
    p = path_of(cfg)
    try:
        refresh_counts(cfg, data, by_id, yt, now)
        skipped = fill_d7(data, by_id, ya, now) if ya else {}
        if ya:  # channel-wide totals and traffic sources, for the report and the war room
            try:
                fetch_channel(cfg, ya, now)
            except Exception as e:  # noqa: BLE001  # the per-video numbers still count
                print(f"  channel totals: {short(e)} (the last ones stay; next run tries again)")
    finally:  # what came in before an error still counts
        jsonstate.write(p, data)
    d7 = sum(1 for r in data.values() if r.get("d7"))
    print(f"{len(data)} videos in {p.name} ({d7} with 7-day numbers"
          + ("" if has_analytics else "; run `clipper analytics --sign-in` for those") + ")")
    if skipped:
        print(f"  7-day numbers of {len(skipped)} video(s) skipped after retries "
              f"({next(iter(skipped.values()))}); next run tries again")
    stuck = {vid: err for vid, err in skipped.items() if data[vid].get("d7_errors", 0) >= GIVE_UP}
    if stuck:
        vid, err = next(iter(stuck.items()))
        raise ClipperError(f"7-day numbers of {len(stuck)} video(s) failed {GIVE_UP}+ runs in a row "
                           f"(first: {vid}, {err})")
    return data


def short(e: Exception) -> str:
    """One line for an error: an HttpError's status and reason, not its whole URL."""
    from googleapiclient.errors import HttpError

    if isinstance(e, HttpError):
        return f"HttpError {e.resp.status} {getattr(e, 'reason', '') or ''}".strip()
    return f"{type(e).__name__}: {str(e)[:160]}"


def refresh_counts(cfg: dict, data: dict, by_id: dict, yt, now: datetime) -> None:
    """Views, likes and comments of every uploaded video from the Data API, into data; a
    video YouTube no longer lists is marked missing."""
    ids = list(by_id)
    for i in range(0, len(ids), 50):
        res = yt.videos().list(part="statistics,snippet",
                               id=",".join(ids[i:i + 50])).execute(num_retries=RETRIES)
        for v in res.get("items", []):
            e = by_id[v["id"]]
            st = v.get("statistics", {})
            rec = data.setdefault(v["id"], {})
            rec.update({"key": e["key"], "stream": e["stream"], "kind": e["kind"],
                        "title": v["snippet"]["title"], "publish_at": e["publish_at"],
                        "url": e.get("url") or f"https://youtu.be/{v['id']}",
                        "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                        "comments": int(st.get("commentCount", 0)),
                        "checked_at": now.isoformat(timespec="seconds"), **entry_details(cfg, e)})
    gone = [vid for vid in ids if vid in data and data[vid].get("checked_at") != now.isoformat(timespec="seconds")]
    for vid in gone:
        data[vid]["missing"] = True  # deleted or private on YouTube


def fill_d7(data: dict, by_id: dict, ya, now: datetime) -> dict[str, str]:
    """First-7-days numbers from the Analytics API for each uploaded video a week old that
    has none yet. One whose query still fails with a passing error after the retries is skipped:
    its d7_errors counts the runs in a row, and the next run asks again. Returns those
    skipped, video id -> the error; any other error raises. Once one has used up its retries
    (Google is down, likely), the rest get one try each rather than a minute of backoff each,
    until one answers: then the retries are back."""
    skipped, retries = {}, RETRIES
    for vid, rec in data.items():
        if rec.get("d7") or vid not in by_id:
            continue
        pub = datetime.fromisoformat(rec["publish_at"]).astimezone(UTC).date()
        if (now.date() - pub).days < D7_DAYS:  # Analytics lags ~2 days; wait for a full week
            continue
        try:
            r = ya.reports().query(
                ids="channel==MINE", startDate=pub.isoformat(),
                endDate=(pub + timedelta(days=6)).isoformat(), filters=f"video=={vid}",
                metrics="views,estimatedMinutesWatched,averageViewDuration,"
                        "averageViewPercentage,subscribersGained,likes").execute(num_retries=retries)
        except Exception as e:
            if not transient(e):
                raise
            rec["d7_errors"] = rec.get("d7_errors", 0) + 1
            skipped[vid], retries = short(e), 0
            continue
        rec.pop("d7_errors", None)
        retries = RETRIES  # Google answers: one video's lasting 500 doesn't cost the next its retries
        if r.get("rows"):
            cols = [h["name"] for h in r["columnHeaders"]]
            rec["d7"] = dict(zip(cols, r["rows"][0]))
    return skipped


def show(cfg: dict) -> None:
    data = load(base(cfg))
    rows = sorted(data.values(), key=lambda r: -((r.get("d7") or {}).get("views") or r.get("views", 0)))
    for r in rows:
        d7 = r.get("d7") or {}
        print(f"{(d7.get('views') or r['views']):>7} views{' (7 d)' if d7 else '      '}"
              f"  {d7.get('averageViewPercentage', 0):5.1f}%  {r['kind']:<11} {r.get('show', ''):<6} "
              f"{r['title'][:70]}")
    if not rows:
        print("analytics.json is empty; run clipper analytics")


def feedback_block(cfg: dict, show_key: str) -> str:
    """For Pass B: best and worst of this show's uploads by 7-day views, or "" when there
    are fewer than [analytics] min_videos with 7-day numbers."""
    ac = cfg["analytics"]
    rows = [r for r in load(base(cfg)).values()
            if r.get("d7") and not r.get("missing") and r.get("show") == show_key
            and r["kind"] in ("clip", "short")]
    if len(rows) < ac["min_videos"]:
        return ""
    rows.sort(key=lambda r: -r["d7"].get("views", 0))
    n = ac["examples"]
    pick = rows[:n] + [r for r in rows[-n:] if r not in rows[:n]]

    def line(r: dict) -> str:
        d = r["d7"]
        return (f"- {d.get('views', 0)} views, {d.get('averageViewPercentage', 0):.0f}% watched "
                f"({r['kind']}, {r.get('category') or '?'}, score {r.get('score') or '?'}): {r['title']}")

    return ("<past_performance>\nHow this show's recent uploads did on YouTube in their first "
            "7 days (best first, then the weakest). A hint about what this audience watches, "
            "not a rule: still judge each candidate on its own.\n"
            + "\n".join(line(r) for r in pick) + "\n</past_performance>\n\n")


# ---------- channel totals and the monthly report (#42) ----------
# Channel-wide numbers come from the Analytics API by day (views, watch time, subscribers)
# and by traffic source per month; they sit in analytics_channel.json beside analytics.json.

CHANNEL_DAYS = 120
TRAFFIC = {"SHORTS": "Shorts feed", "YT_SEARCH": "YouTube search", "SUBSCRIBER": "Subscriptions",
           "RELATED_VIDEO": "Suggested videos", "BROWSE": "Browse (home)", "EXT_URL": "External",
           "NO_LINK_OTHER": "Direct / unknown", "PLAYLIST": "Playlists", "YT_CHANNEL": "Channel page",
           "NOTIFICATION": "Notifications", "YT_OTHER_PAGE": "Other YouTube pages",
           "END_SCREEN": "End screens", "HASHTAGS": "Hashtag pages", "SOUND_PAGE": "Sound pages"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def channel_path(cfg: dict) -> Path:
    return path_of(cfg).with_name("analytics_channel.json")


def fetch_channel(cfg: dict, ya, now: datetime) -> dict:
    """Daily channel totals for the last CHANNEL_DAYS days, and traffic sources for this
    month and the two before it."""
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=CHANNEL_DAYS)
    r = ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                           dimensions="day", sort="day",
                           metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost,likes"
                           ).execute(num_retries=RETRIES)
    cols = [h["name"] for h in r.get("columnHeaders", [])]
    daily = [dict(zip(cols, row, strict=False)) for row in r.get("rows") or []]
    traffic = {}
    first = end.replace(day=1)
    for _ in range(3):
        last = min(end, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        t = ya.reports().query(ids="channel==MINE", startDate=first.isoformat(), endDate=last.isoformat(),
                               dimensions="insightTrafficSourceType", sort="-views",
                               metrics="views,estimatedMinutesWatched").execute(num_retries=RETRIES)
        traffic[first.strftime("%Y-%m")] = [{"source": row[0], "views": row[1], "minutes": row[2]}
                                            for row in t.get("rows") or []]
        first = (first - timedelta(days=1)).replace(day=1)
    out = {"fetched_at": now.isoformat(timespec="seconds"), "daily": daily, "traffic": traffic}
    jsonstate.write(channel_path(cfg), out)
    return out


def _group(rows: list[dict], key) -> list[dict]:
    """Per group of videos with 7-day numbers: count, median and total 7-day views, mean %
    watched; biggest median first."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(key(r) or "(none)", []).append(r)
    out = [{"key": k, "n": len(g), "median_views": round(median([r["d7"]["views"] for r in g]), 1),
            "views": sum(r["d7"]["views"] for r in g),
            "pct": round(sum(r["d7"].get("averageViewPercentage", 0) for r in g) / len(g), 1)}
           for k, g in groups.items()]
    return sorted(out, key=lambda x: (-x["median_views"], -x["n"]))


def report(cfg: dict, month: str = "", lift: bool = False) -> dict:
    """One month: channel totals (and the month before), uploads by kind, 7-day performance
    of that month's uploads by show, kind, category, weekday and hour, best and weakest,
    traffic sources and the daily numbers. lift: also what predicts views over every upload
    so far (lift.py; it reads each stream's signals, so the war room's panel leaves it out)."""
    from zoneinfo import ZoneInfo

    cfg = base(cfg)
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    month = month or datetime.now(tz).strftime("%Y-%m")
    prev = (datetime.strptime(month + "-01", "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m")
    ch = jsonstate.read(channel_path(cfg), {}) or {}
    daily = ch.get("daily") or []

    def totals(m: str) -> dict:
        ds = [d for d in daily if d["day"].startswith(m)]
        return {"days": len(ds), "views": sum(d["views"] for d in ds),
                "watch_hours": round(sum(d["estimatedMinutesWatched"] for d in ds) / 60, 1),
                "subs": sum(d["subscribersGained"] - d["subscribersLost"] for d in ds),
                "likes": sum(d["likes"] for d in ds)}

    vids = [r for r in load(cfg).values() if not r.get("missing")]
    for r in vids:
        r["local"] = datetime.fromisoformat(r["publish_at"]).astimezone(tz)
    now = datetime.now(tz)
    in_month = [r for r in vids if r["local"].strftime("%Y-%m") == month]
    mine = [r for r in in_month if r["local"] <= now]  # out already, not just uploaded with a later publishAt
    d7 = [r for r in mine if r.get("d7")]
    by_kind: dict[str, int] = {}
    for r in mine:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    def row(r: dict) -> dict:
        d = r.get("d7") or {}
        return {"title": r["title"], "url": r["url"], "kind": r["kind"],
                "show": r.get("show_name") or r.get("show", ""),
                "published": r["local"].strftime("%Y-%m-%d %H:%M"), "views": r["views"],
                "d7_views": d.get("views"), "d7_pct": d.get("averageViewPercentage"),
                "d7_minutes": d.get("estimatedMinutesWatched"), "d7_subs": d.get("subscribersGained"),
                "category": r.get("category", ""), "score": r.get("score", ""), "topic": r.get("topic", "")}

    ranked = sorted(d7, key=lambda r: -r["d7"]["views"])
    return {
        "month": month, "prev_month": prev, "fetched_at": ch.get("fetched_at", ""),
        "totals": totals(month), "prev_totals": totals(prev),
        "uploads": len(mine), "uploads_by_kind": by_kind, "with_d7": len(d7),
        "still_scheduled": len(in_month) - len(mine),
        "by_show": _group(d7, lambda r: r.get("show_name") or r.get("show")),
        "by_kind": _group(d7, lambda r: r["kind"]),
        "by_category": _group(d7, lambda r: r.get("category")),
        "by_weekday": sorted(_group(d7, lambda r: WEEKDAYS[r["local"].weekday()]),
                             key=lambda g: WEEKDAYS.index(g["key"])),
        "by_hour": sorted(_group(d7, lambda r: f"{r['local'].hour:02d}:00"), key=lambda g: g["key"]),
        "best": [row(r) for r in ranked[:8]],
        "worst": [row(r) for r in ranked[-5:][::-1]] if len(ranked) > 8 else [],
        "traffic": [{**t, "label": TRAFFIC.get(t["source"], t["source"].replace("_", " ").title())}
                    for t in (ch.get("traffic") or {}).get(month, [])],
        "daily": [{"day": d["day"], "views": d["views"], "minutes": d["estimatedMinutesWatched"],
                   "subs": d["subscribersGained"] - d["subscribersLost"]}
                  for d in daily if d["day"].startswith(month)],
        "videos": [row(r) for r in sorted(mine, key=lambda r: r["local"])],
        "lift": lift_table(cfg) if lift else [],
    }


def lift_table(cfg: dict) -> list[dict]:
    """The report's last section: lift.table over every upload so far, not only the month's
    (a month is too few to rank anything by)."""
    from . import lift

    return lift.table(lift.rows(cfg))


def report_markdown(rep: dict) -> str:
    t, p = rep["totals"], rep["prev_totals"]

    def delta(a: float, b: float) -> str:
        return f" ({'+' if a >= b else ''}{(a - b) / b * 100:.0f}% vs {rep['prev_month']})" if b > 0 else ""

    def table(title: str, groups: list[dict]) -> list[str]:
        if not groups:
            return []
        return [f"### {title}", "", "| | videos | median 7-day views | 7-day views | avg % watched |",
                "|---|---:|---:|---:|---:|",
                *(f"| {g['key']} | {g['n']} | {g['median_views']:g} | {g['views']:,} | {g['pct']:.0f}% |"
                  for g in groups), ""]

    def vids(rows: list[dict]) -> list[str]:
        return [f"- **{r['d7_views']:,}** views, {r['d7_pct'] or 0:.0f}% watched · {r['kind']} · {r['show']} · "
                f"[{r['title']}]({r['url']})" for r in rows]

    kinds = ", ".join(f"{n} {k}" for k, n in sorted(rep["uploads_by_kind"].items()))
    lines = [f"# Lolcow Cinema analytics: {rep['month']}", "",
             (f"Channel numbers through the last day YouTube has (fetched {rep['fetched_at'][:16].replace('T', ' ')} UTC; "
             "the Analytics API runs about two days behind)."), "",
             f"- **Views:** {t['views']:,}{delta(t['views'], p['views'])}",
             f"- **Watch time:** {t['watch_hours']:,} h{delta(t['watch_hours'], p['watch_hours'])}",
             f"- **Subscribers:** {t['subs']:+,}{delta(t['subs'], p['subs'])}",
             f"- **Published:** {rep['uploads']} ({kinds}), {rep['with_d7']} old enough for 7-day numbers"
             + (f"; {rep['still_scheduled']} more uploaded and set to go out later this month"
                if rep["still_scheduled"] else ""), ""]
    if rep["traffic"]:
        total = sum(x["views"] for x in rep["traffic"]) or 1
        lines += ["## Where views came from", "",
                  *(f"- {x['label']}: {x['views']:,} ({x['views'] / total * 100:.0f}%)" for x in rep["traffic"][:8]), ""]
    if rep["with_d7"]:
        lines += ["## First 7 days of this month's uploads", "",
                  *table("By show", rep["by_show"]), *table("By kind", rep["by_kind"]),
                  *table("By category", rep["by_category"]), *table("By weekday published", rep["by_weekday"]),
                  *table("By hour published", rep["by_hour"]),
                  "### Best", "", *vids(rep["best"]), ""]
        if rep["worst"]:
            lines += ["### Weakest", "", *vids(rep["worst"]), ""]
    if rep.get("lift"):
        from . import lift

        lines += ["## What predicts views (every upload so far)", "",
                  "Each ranker against 7-day views: the Score column, the chat heat `clipper schedule` "
                  "ranks segments by, and the topic's earlier uploads (`clipper analytics --lift`).", "",
                  lift.markdown(rep["lift"]), ""]
    lines += ["Medians of fewer than 3 videos are anecdotes, not trends. Every upload is in the .csv beside this file.", ""]
    return "\n".join(lines)


def write_report(cfg: dict, rep: dict) -> Path:
    """reports/analytics/<month>.md and .csv (every upload of the month, one row each)."""
    import csv

    out = Path(base(cfg)["_dir"]) / "reports" / "analytics"
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"{rep['month']}.md"
    jsonstate.write_text(md, report_markdown(rep))
    with open(out / f"{rep['month']}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rep["videos"][0]) if rep["videos"] else ["title"])
        w.writeheader()
        w.writerows(rep["videos"])
    return md
