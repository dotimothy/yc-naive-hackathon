"""AgentMail polling worker. Run as a module from the repository root."""

import argparse
import fcntl
import json
import os
import signal
import sys
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from backend.app.mailroom import Mailroom

ROOT = Path(__file__).resolve().parents[2]


class AgentMail:
    def __init__(self, key, inbox):
        self.inbox = inbox
        self.client = httpx.Client(
            base_url="https://api.agentmail.to/v0",
            headers={"Authorization": "Bearer " + key},
            timeout=30,
            follow_redirects=False,
        )
        self.path = "/inboxes/" + quote(inbox, safe="") + "/messages"

    def get(self, path, **kwargs):
        response = self.client.get(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def messages(self, after):
        after = (
            datetime.fromisoformat(after.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        token = None
        for _ in range(10):
            params = {
                "labels": "received",
                "after": after,
                "ascending": "true",
                "limit": 100,
            }
            if token:
                params["page_token"] = token
            page = self.get(self.path, params=params)
            for message in page.get("messages", []):
                yield self.get(self.path + "/" + quote(message["message_id"], safe=""))
            token = page.get("next_page_token")
            if not token:
                break

    def reply(self, row):
        # Same saved payload and key on retry; never reply-all or honor an arbitrary Reply-To.
        response = self.client.post(
            self.path + "/" + quote(row["message_id"], safe="") + "/reply",
            json={
                "text": row["body"],
                "to": [row["sender"]],
                "reply_all": False,
                **({"html": row["html"]} if row["html"] else {}),
            },
            headers={"Idempotency-Key": "playlattice-" + row["id"]},
        )
        response.raise_for_status()
        return response.json()


def flush(room, mail):
    for row in room.db.execute(
        "SELECT * FROM outbox WHERE status IN ('pending','sending') ORDER BY rowid LIMIT 20"
    ).fetchall():
        first = row["first_attempt"] or time.time()
        # Provider send idempotency is bounded. An uncertain old attempt needs inspection.
        if row["first_attempt"] and time.time() - first > 23 * 3600:
            with room.db:
                room.db.execute(
                    "UPDATE outbox SET status='needs_reconciliation' WHERE id=?",
                    (row["id"],),
                )
            continue
        with room.db:
            room.db.execute(
                "UPDATE outbox SET status='sending',first_attempt=? WHERE id=?",
                (first, row["id"]),
            )
        receipt = mail.reply(row)
        with room.db:
            room.db.execute(
                "UPDATE outbox SET status='sent',receipt=? WHERE id=?",
                (json.dumps(receipt), row["id"]),
            )


def report_status(room, state, error_code=None):
    path = room.folder / "status.json"
    try:
        previous = json.loads(path.read_text()) if path.exists() else {}
    except (ValueError, OSError):
        previous = {}
    now = datetime.now(timezone.utc).isoformat()
    value = {"pid": os.getpid(), "state": state, "updated_at": now,
             "last_success_at": now if state == "idle" else previous.get("last_success_at"),
             "error_code": error_code}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def outreach_decision(room, message):
    if not (ROOT / "data/naive/outreach-agent.json").exists():
        return None
    if room.db.execute(
        "SELECT 1 FROM inbound WHERE id=?", (message.get("message_id"),)
    ).fetchone():
        return None
    labels = set(message.get("labels", []))
    headers = {
        str(k).lower(): str(v).lower() for k, v in message.get("headers", {}).items()
    }
    if (
        "received" not in labels
        or labels & {"sent", "spam", "trash", "unauthenticated"}
        or headers.get("auto-submitted", "no") != "no"
        or (message.get("agent_armor") or {}).get("status") == "pending"
    ):
        return None
    catalog = [
        {
            key: entry[key]
            for key in ("id", "title", "post_url", "duration_ms", "credit", "caption")
        }
        for entry in room.catalog()
    ]
    payload = {
        "message_key": message["message_id"],
        "text": (message.get("extracted_text") or message.get("text") or "")[:6000],
        "catalog": catalog,
    }
    # The Naive process receives no AgentMail credential, sender address, or local source path.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AGENTMAIL_")
    }
    try:
        result = subprocess.run(
            [
                str(ROOT / "integrations/naive/node_modules/.bin/tsx"),
                str(ROOT / "integrations/naive/src/outreach-run.ts"),
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=ROOT / "integrations/naive",
            env=env,
            timeout=150,
        )
        if result.returncode:
            raise ValueError("Outreach unavailable")
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        print(
            json.dumps({"event": "outreach_unavailable", "fallback": "local_intake"}),
            flush=True,
        )
        return None


def tick(room, mail):
    after = room.setting("scan_after")
    for message in mail.messages(after):
        result = room.handle(
            message, mail.inbox, decision=outreach_decision(room, message)
        )
        if result == "pending_scan":
            break
        created = message.get("created_at")
        if created:
            # Overlap one second: durable inbound IDs deduplicate repeated delivery.
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00")) - timedelta(
                seconds=1
            )
            if parsed > datetime.fromisoformat(
                room.setting("scan_after").replace("Z", "+00:00")
            ):
                room.set_setting(
                    "scan_after", parsed.isoformat().replace("+00:00", "Z")
                )
    flush(room, mail)
    result = room.render_next()
    flush(room, mail)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Show only public catalog fields; no email is read or sent",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable the user-authorized email reply and clipping worker",
    )
    args = parser.parse_args()
    origin = os.getenv(
        "PLAYLATTICE_PUBLIC_ORIGIN",
        "https://playlattice-film-room.doublename.chatgpt.site",
    )
    room = Mailroom(ROOT, origin)
    if args.catalog:
        print(
            json.dumps(
                [
                    {
                        key: entry[key]
                        for key in ("id", "title", "post_url", "duration_ms", "credit")
                    }
                    for entry in room.catalog()
                ],
                indent=2,
            )
        )
        return
    if not args.live:
        parser.error(
            "Use --catalog to inspect, or --live to process new inbound mail and send replies."
        )
    key = os.getenv("AGENTMAIL_API_KEY")
    if not key:
        parser.error(
            "Set AGENTMAIL_API_KEY in the worker environment; never put it in Git or chat."
        )
    inbox = os.getenv("AGENTMAIL_INBOX", "playlattice@agentmail.to")
    lock = (room.folder / "worker.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("AgentMail worker already running")
    mail = AgentMail(key, inbox)
    # Verify the selected inbox before initializing the intake watermark.
    mail.get("/inboxes/" + quote(inbox, safe=""))
    if not room.setting("scan_after"):
        room.set_setting(
            "scan_after", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    print(json.dumps({"event": "agentmail_worker_ready", "inbox": inbox}), flush=True)
    reload_requested = False

    def request_reload(signum, frame):
        nonlocal reload_requested
        reload_requested = True

    signal.signal(signal.SIGHUP, request_reload)
    while True:
        if reload_requested:
            os.execv(
                sys.executable,
                [sys.executable, "-m", "integrations.agentmail.worker", *sys.argv[1:]],
            )
        try:
            report_status(room, "polling")
            result = tick(room, mail)
            report_status(room, "idle")
            if result:
                print(
                    json.dumps({"event": "clip_request", "status": result}), flush=True
                )
        except httpx.HTTPStatusError as error:
            report_status(room, "error", error.response.status_code)
            print(
                json.dumps(
                    {
                        "event": "agentmail_http_error",
                        "status": error.response.status_code,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            report_status(room, "error")
            print(
                json.dumps(
                    {"event": "agentmail_worker_error", "type": type(error).__name__}
                ),
                flush=True,
            )
        if args.once:
            report_status(room, "stopped")
            break
        time.sleep(30)


if __name__ == "__main__":
    main()
