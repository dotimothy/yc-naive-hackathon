"""Deterministic email intake, posted-video catalog, and durable source-clip queue."""

import hashlib
import json
import re
import secrets
import sqlite3
import time
from html import escape
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlparse


class Mailroom:
    def __init__(self, root, public_origin):
        self.root = Path(root).resolve()
        self.folder = self.root / "data/agentmail"
        self.folder.mkdir(parents=True, exist_ok=True)
        origin = urlparse(public_origin)
        if (
            origin.scheme not in ("http", "https")
            or not origin.netloc
            or origin.path not in ("", "/")
        ):
            raise ValueError("A public app origin without a path is required")
        self.origin = public_origin.rstrip("/")
        self.db = sqlite3.connect(self.folder / "mailroom.sqlite3", timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbound(id TEXT PRIMARY KEY, sender TEXT, thread TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, message_id TEXT UNIQUE, sender TEXT,
          status TEXT, value TEXT, token TEXT UNIQUE, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,message_id TEXT,sender TEXT,body TEXT,
          status TEXT,first_attempt REAL,receipt TEXT);
        """)

        columns = {row[1] for row in self.db.execute("PRAGMA table_info(outbox)")}
        if "html" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE outbox ADD COLUMN html TEXT")

    def setting(self, key, default=None):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))

    def records(self, kind):
        path = self.root / "data/local/scoutfilm.sqlite3"
        with sqlite3.connect("file:%s?mode=ro" % path, uri=True) as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT value FROM records WHERE kind=?", (kind,))
            ]

    def catalog(self):
        games = {game["game_id"]: game for game in self.records("game")}
        jobs = {job["job_id"]: job for job in self.records("job")}
        source_file = self.root / "data/naive/publishing-sources.json"
        sources = (
            json.loads(source_file.read_text()).get("items", [])
            if source_file.exists()
            else []
        )
        credits = {source["sha256"]: source.get("credit", "") for source in sources}
        result = {}
        for path in (self.root / "data/naive").glob("publish-*.json"):
            try:
                post = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            url = post.get("url") or ""
            match = re.fullmatch(
                r"https://www\.tiktok\.com/@playlatticeai/video/(\d+)", url
            )
            job = jobs.get(post.get("job_id"))
            game = games.get(job.get("game_id")) if job else None
            if not match or post.get("status") != "posted" or not game or not job:
                continue
            if (
                post.get("source_sha256") != game["source_sha256"]
                or job.get("status") != "succeeded"
            ):
                continue
            source = Path(game.get("path", "")).resolve()
            if not source.is_file() or not source.is_relative_to(
                self.root / "data/local"
            ):
                continue
            key = "PL-" + match[1]
            body = post.get("body") or {}
            entry = dict(
                id=key,
                post_url=url,
                game_id=game["game_id"],
                job_id=job["job_id"],
                source_sha256=game["source_sha256"],
                source_path=str(source),
                title=game["title"],
                duration_ms=game["duration_ms"],
                credit=credits.get(
                    game["source_sha256"], "See the original post for source credit"
                ),
                caption=str(body.get("content", ""))[:1500],
                created=post.get("created_ms", 0),
            )
            if key not in result or entry["created"] > result[key]["created"]:
                result[key] = entry
        return sorted(result.values(), key=lambda entry: entry["created"], reverse=True)

    def outbox(self, message_id, sender, body, phase):
        key = hashlib.sha256((message_id + ":" + phase).encode()).hexdigest()
        body = (
            "Hi there,\n\n"
            + body
            + "\n\n— The PlayLattice Team\nAutonomous Sports Clipping"
        )
        safe = escape(body)
        safe = re.sub(
            r"https://[^\s<>]+",
            lambda m: '<a style="color:#bd4e0e" href="' + m[0] + '">' + m[0] + "</a>",
            safe,
        )
        html = (
            '<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#17202a;line-height:1.65">'
            '<div style="border-bottom:3px solid #ff6b1a;padding:12px 0;margin-bottom:22px">'
            '<img src="'
            + escape(self.origin, quote=True)
            + '/favicon.png" alt="PlayLattice" width="48" height="48" style="vertical-align:middle;margin-right:12px">'
            '<strong style="font-size:22px">PlayLattice</strong></div>'
            "<div>" + safe.replace("\n", "<br>") + "</div></div>"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO outbox(id,message_id,sender,body,status,first_attempt,receipt,html) VALUES(?,?,?,?,?,?,?,?)",
            (key, message_id, sender, body, "pending", None, None, html),
        )

    def handle(self, message, inbox="playlattice@agentmail.to", decision=None):
        mid = message.get("message_id")
        sender = parseaddr(message.get("from", ""))[1].lower()
        labels = set(message.get("labels", []))
        headers = {
            str(k).lower(): str(v).lower()
            for k, v in message.get("headers", {}).items()
        }
        if not mid or not sender or "@" not in sender or sender == inbox.lower():
            return "ignored"
        if "received" not in labels or labels & {
            "sent",
            "spam",
            "trash",
            "unauthenticated",
        }:
            return "ignored"
        if headers.get("auto-submitted", "no") != "no" or headers.get("precedence") in {
            "bulk",
            "list",
            "junk",
        }:
            return "ignored"
        armor = message.get("agent_armor") or {}
        if armor.get("status") == "pending":
            return "pending_scan"
        text = message.get("extracted_text") or message.get("text") or ""
        # Quoted mail must not turn a previous recommendation into a new clip order.
        text = re.split(r"(?m)^>|^On .+wrote:|^-+Original Message-+", text)[0][:10000]
        catalog = self.catalog()
        with self.db:
            if self.db.execute("SELECT 1 FROM inbound WHERE id=?", (mid,)).fetchone():
                return "duplicate"
            self.db.execute(
                "INSERT INTO inbound VALUES(?,?,?,?)",
                (mid, sender, message.get("thread_id", ""), time.time()),
            )
            if decision:
                allowed = {entry["id"] for entry in catalog}
                ids = decision.get("video_ids", [])
                if not isinstance(ids, list) or any(
                    item not in allowed for item in ids
                ):
                    decision = None
                elif decision.get("intent") == "clip":
                    selected = ids[0] if len(ids) == 1 else ""
                    source = next(
                        (entry for entry in catalog if entry["id"] == selected), None
                    )
                    start, end = decision.get("start_ms"), decision.get("end_ms")
                    track = decision.get("track_id")
                    explicit = (
                        source
                        and (selected in text or source["post_url"] in text)
                        and re.search(r"\b(?:clip|highlight|cut|trim)\b", text, re.I)
                    )
                    valid_range = (
                        type(start) is int
                        and type(end) is int
                        and source
                        and 0 <= start < end <= source["duration_ms"]
                        and end - start <= 30000
                    )
                    ranges = re.findall(
                        r"\bSTART\s*=\s*(\d+(?:\.\d+)?)\s+END\s*=\s*(\d+(?:\.\d+)?)\b",
                        text,
                        re.I,
                    )
                    if not ranges:
                        ranges = re.findall(
                            r"(?<![\w.])(?:from\s+)?(\d+(?:\.\d+)?)\s*(?:seconds?|s)?\s*(?:to|through|[-–])\s*(\d+(?:\.\d+)?)(?![\w.])",
                            text,
                            re.I,
                        )
                    explicit_times = (
                        valid_range
                        and len(ranges) == 1
                        and (
                            round(float(ranges[0][0]) * 1000),
                            round(float(ranges[0][1]) * 1000),
                        )
                        == (start, end)
                    )
                    explicit_track = track is None or (
                        isinstance(track, str)
                        and re.fullmatch(r"[-\w.~]{1,60}", track)
                        and re.search(
                            r"\bTRACK\s*[= ]\s*" + re.escape(track) + r"(?![\w.~])",
                            text,
                            re.I,
                        )
                    )
                    needs_player = re.search(
                        r"\b(?:shirt|jersey|wearing|player in|player with)\b",
                        text,
                        re.I,
                    )
                    if (
                        explicit
                        and explicit_times
                        and explicit_track
                        and (not needs_player or track is not None)
                    ):
                        text = (
                            "CLIP "
                            + selected
                            + " START=%g END=%g" % (start / 1000, end / 1000)
                            + ((" TRACK=" + track) if track else "")
                        )
                    else:
                        self.outbox(
                            mid,
                            sender,
                            "Which source video and time range would you like? Please include its PL video ID or TikTok link and start/end seconds. For a player highlight, confirm the temporary track ID in the film room.",
                            "outreach-clarify",
                        )
                        return "needs_details"
                elif decision.get("intent") == "clarify":
                    self.outbox(
                        mid,
                        sender,
                        str(
                            decision.get(
                                "reply",
                                "Please tell us which video and moment you would like.",
                            )
                        )[:1200],
                        "outreach-clarify",
                    )
                    return "needs_details"
            is_clip = bool(re.search(r"(?im)^\s*CLIP\b", text))
            ids = re.findall(r"\bPL-\d+\b", text)
            ids += [
                "PL-" + value
                for value in re.findall(r"tiktok\.com/@playlatticeai/video/(\d+)", text)
            ]
            if not is_clip:
                words = set(re.findall(r"[a-z]{3,}", text.lower())) - {
                    "recommend",
                    "videos",
                    "video",
                    "please",
                    "show",
                    "with",
                    "that",
                    "this",
                    "some",
                }
                scored = sorted(
                    catalog,
                    key=lambda c: sum(
                        word in (c["caption"] + " " + c["title"]).lower()
                        for word in words
                    ),
                    reverse=True,
                )
                if (
                    decision
                    and decision.get("intent") == "recommend"
                    and decision.get("video_ids")
                ):
                    scored = [
                        entry
                        for key in decision["video_ids"]
                        for entry in catalog
                        if entry["id"] == key
                    ]
                lines = [
                    "Thanks for reaching out! Here are a few PlayLattice videos to explore:",
                    "",
                ]
                if decision and decision.get("intent") == "recommend":
                    lines.extend([str(decision.get("reply", ""))[:1200], ""])
                for number, entry in enumerate(scored[:3], 1):
                    lines.extend(
                        [
                            f"{number}. {entry['id']} — {entry['title']}",
                            entry["post_url"],
                            "Source credit: " + entry["credit"],
                            "Original footage: %.2f seconds."
                            % (entry["duration_ms"] / 1000),
                            "",
                        ]
                    )
                if scored:
                    example = scored[0]
                    seconds = min(5, example["duration_ms"] / 1000)
                    lines.extend(
                        [
                            "See one you like? Reply with the line below to request a preview:",
                            f"CLIP {example['id']} START=0 END={seconds:g}",
                            "",
                            "Swap in your chosen video ID and start/end times. Use seconds from the ORIGINAL SOURCE video, rather than the edited TikTok. Clips can be up to 30 seconds.",
                            "Want to highlight one player? Add TRACK=<ID> after confirming their temporary track number in the film room. If you only have a jersey or clothing description, we’ll ask for that confirmation first.",
                            "",
                            "These suggestions use the captions and source titles we have on file, so they aren’t a visual match to a particular player. TikTok availability can change.",
                            "We’ll email your preview for review. Nothing gets posted to social media from this request.",
                        ]
                    )
                else:
                    lines.extend(
                        [
                            "We don’t have any published videos with available original footage to recommend just yet. Please check back later."
                        ]
                    )
                self.outbox(mid, sender, "\n".join(lines), "recommend")
                return "recommended"
            entry = (
                next((item for item in catalog if item["id"] in ids), None)
                if len(set(ids)) == 1
                else None
            )
            start = re.search(r"\bSTART\s*=\s*(\d+(?:\.\d+)?)\b", text, re.I)
            end = re.search(r"\bEND\s*=\s*(\d+(?:\.\d+)?)\b", text, re.I)
            track = re.search(r"\bTRACK\s*=\s*([a-zA-Z0-9_~.-]+)", text, re.I)
            description = re.search(r"\b(?:PLAYER|JERSEY)\s*=\s*(.+)", text, re.I)
            if (
                not entry
                or not start
                or not end
                or (
                    (
                        description
                        or re.search(
                            r"\b(?:shirt|jersey|wearing|player in|player with)\b",
                            text,
                            re.I,
                        )
                    )
                    and not track
                )
            ):
                self.outbox(
                    mid,
                    sender,
                    "Happy to help with your clip! I just need the video ID and the moment you want.\n\nReply with: CLIP PL-<video number> START=0 END=5\n\nUse times from the original footage. For a player highlight, add TRACK=<ID> after checking the player in the film room. A clothing description alone isn’t enough for us to pick the right person reliably.",
                    "clarify",
                )
                return "needs_details"
            a, b = round(float(start[1]) * 1000), round(float(end[1]) * 1000)
            if not (0 <= a < b <= entry["duration_ms"]) or b - a > 30000:
                self.outbox(
                    mid,
                    sender,
                    "That time range doesn’t fit the original video, or the clip is longer than 30 seconds. Could you send a start and end time within the source video?\n\nFor example: CLIP PL-<video number> START=1 END=5",
                    "invalid",
                )
                return "invalid_range"
            active = self.db.execute(
                "SELECT COUNT(*) FROM requests WHERE sender=? AND (status IN ('queued','rendering') OR created>?)",
                (sender, time.time() - 86400),
            ).fetchone()[0]
            if active >= 5:
                self.outbox(
                    mid,
                    sender,
                    "You’ve reached the limit of five recent or active clip requests. Please give those requests some time before sending another. Any queued previews will still arrive by email.",
                    "limited",
                )
                return "limited"
            rid = secrets.token_hex(12)
            value = dict(
                entry=entry,
                start_ms=a,
                end_ms=b,
                track_id=track[1] if track else None,
                player_description=description[1][:300] if description else None,
            )
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)",
                (
                    rid,
                    mid,
                    sender,
                    "queued",
                    json.dumps(value),
                    secrets.token_hex(24),
                    time.time(),
                    time.time(),
                ),
            )
            self.outbox(
                mid,
                sender,
                "You’re in the queue!\n\nRequest: %s\nVideo: %s\nSelected moment: %.2f–%.2f seconds in the original footage.%s\n\nWe’ll email you a preview link after the clip is rendered and checked. A track number is a temporary visual label, not a verified player identity. Nothing will be posted to social media."
                % (
                    rid,
                    entry["id"],
                    a / 1000,
                    b / 1000,
                    " Track: " + track[1] if track else "",
                ),
                "queued",
            )
            return "queued"

    def render_next(self):
        from workers.render.media import render_clip, verify_clip, sha256
        from workers.video.identity_corrections import apply_track_splits

        row = self.db.execute(
            "SELECT * FROM requests WHERE status IN ('queued','rendering') ORDER BY created LIMIT 1"
        ).fetchone()
        if not row:
            return None
        with self.db:
            self.db.execute(
                "UPDATE requests SET status='rendering',updated=? WHERE id=?",
                (time.time(), row["id"]),
            )
        value = json.loads(row["value"])
        try:
            entry = value["entry"]
            source = Path(entry["source_path"]).resolve()
            if (
                not source.is_relative_to(self.root / "data/local")
                or sha256(source) != entry["source_sha256"]
            ):
                raise ValueError("Source integrity check failed")
            overlays = []
            if value["track_id"]:
                samples = [
                    sample
                    for batch in self.records("batch")
                    if batch["job_id"] == entry["job_id"]
                    for sample in batch["items"]
                ]
                correction = next(
                    (
                        c
                        for c in self.records("identity_correction")
                        if c["job_id"] == entry["job_id"]
                    ),
                    {},
                )
                samples = apply_track_splits(samples, correction.get("splits", []))
                selected = sorted(
                    [
                        s
                        for s in samples
                        if str(s["track_id"]) == value["track_id"]
                        and value["start_ms"] <= s["timestamp_ms"] < value["end_ms"]
                    ],
                    key=lambda s: s["timestamp_ms"],
                )
                if not selected:
                    raise ValueError("No matching track evidence in that source range")
                if len(selected) > 100:
                    raise ValueError(
                        "Too many track samples; request a shorter player highlight"
                    )
                for i, sample in enumerate(selected):
                    until = min(value["end_ms"], sample["timestamp_ms"] + 250)
                    if i + 1 < len(selected):
                        until = min(until, selected[i + 1]["timestamp_ms"])
                    overlays.append(
                        dict(
                            timestamp_ms=sample["timestamp_ms"],
                            end_ms=until,
                            bbox=sample["bbox"],
                        )
                    )
            output = self.folder / "previews" / (row["id"] + ".mp4")
            render_clip(
                source, value["start_ms"], value["end_ms"], output, overlays=overlays
            )
            qa = verify_clip(source, output, value["start_ms"], value["end_ms"])
            if qa["source_sha256"] != entry["source_sha256"]:
                raise ValueError("Source changed during rendering")
            value.update(
                qa=qa, output=str(output), preview_expires=time.time() + 7 * 86400
            )
            link = self.origin + "/api/mail-previews/" + row["token"]
            with self.db:
                self.db.execute(
                    "UPDATE requests SET status='ready',value=?,updated=? WHERE id=?",
                    (json.dumps(value), time.time(), row["id"]),
                )
                self.outbox(
                    row["message_id"],
                    row["sender"],
                    "Your clip is ready to watch!\n\nOpen your preview: %s\n\nOriginal TikTok: %s\nSource credit: %s\nSelected moment: %.2f–%.2f source seconds.\n%s\n\nWe checked that the clip plays and matches the requested source range. Basketball outcomes and player identities still need your review.\n\nThe link works for seven days while the PlayLattice app is online. Want a different moment? Send a new CLIP request with the times you’d like. Nothing has been posted to social media."
                    % (
                        link,
                        entry["post_url"],
                        entry["credit"],
                        value["start_ms"] / 1000,
                        value["end_ms"] / 1000,
                        "Yellow boxes mark provisional track "
                        + value["track_id"]
                        + " where observations exist."
                        if value["track_id"]
                        else "No player-specific identity was inferred.",
                    ),
                    "ready",
                )
            return "ready"
        except Exception:
            with self.db:
                self.db.execute(
                    "UPDATE requests SET status='failed',updated=? WHERE id=?",
                    (time.time(), row["id"]),
                )
                self.outbox(
                    row["message_id"],
                    row["sender"],
                    "We couldn’t finish a checked preview for request %s. Please double-check the source times and any track ID, then send a new CLIP request. Your original footage hasn’t changed, and nothing was posted to social media."
                    % row["id"],
                    "failed",
                )
            return "failed"
