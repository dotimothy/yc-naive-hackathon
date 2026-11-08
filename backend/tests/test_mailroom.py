import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.mailroom import Mailroom
from backend.app import mail_preview_routes
from integrations.agentmail.worker import flush


@pytest.fixture
def room(tmp_path):
    local = tmp_path / "data/local"
    local.mkdir(parents=True)
    source = local / "source.mp4"
    source.write_bytes(b"fixture")
    db = sqlite3.connect(local / "scoutfilm.sqlite3")
    db.execute("CREATE TABLE records(kind TEXT,id TEXT,value TEXT)")
    game = dict(
        game_id="game",
        title="Indoor basketball.mp4",
        source_sha256="source-hash",
        path=str(source),
        duration_ms=10000,
    )
    job = dict(job_id="job", game_id="game", status="succeeded")
    for kind, value in [("game", game), ("job", job)]:
        db.execute("INSERT INTO records VALUES(?,?,?)", (kind, kind, json.dumps(value)))
    db.commit()
    folder = tmp_path / "data/naive"
    folder.mkdir()
    (folder / "publish-one.json").write_text(
        json.dumps(
            dict(
                status="posted",
                url="https://www.tiktok.com/@playlatticeai/video/123456",
                source_sha256="source-hash",
                job_id="job",
                created_ms=1,
                body={"content": "Indoor court portrait quiz"},
            )
        )
    )
    yield Mailroom(tmp_path, "https://app.example")
    db.close()


def message(mid="message", text="Recommend indoor clips", **kwargs):
    return dict(
        message_id=mid,
        thread_id="thread",
        labels=["received"],
        **{"from": "Viewer <viewer@example.com>"},
        extracted_text=text,
        **kwargs,
    )


def test_catalog_is_source_backed_and_recommendations_have_real_links(room):
    assert len(room.catalog()) == 1
    assert room.handle(message()) == "recommended"
    body = room.db.execute("SELECT body FROM outbox").fetchone()[0]
    assert "https://www.tiktok.com/@playlatticeai/video/123456" in body
    assert "PL-123456" in body and "ORIGINAL SOURCE" in body
    assert room.handle(message()) == "duplicate"
    assert room.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_clip_queue_is_idempotent_and_sender_is_bound_to_from(room):
    assert (
        room.handle(
            message(
                text="CLIP PL-123456 START=1 END=4 TRACK=7",
                reply_to=["other@example.com"],
            )
        )
        == "queued"
    )
    assert (
        room.handle(message(text="CLIP PL-123456 START=1 END=4 TRACK=7")) == "duplicate"
    )
    row = room.db.execute("SELECT * FROM requests").fetchone()
    assert row["status"] == "queued" and row["sender"] == "viewer@example.com"
    value = json.loads(row["value"])
    assert value["start_ms"] == 1000 and value["track_id"] == "7"


@pytest.mark.parametrize(
    "text,status",
    [
        ("CLIP PL-123456 START=0 END=11", "invalid_range"),
        ("CLIP PL-123456 START=4 END=1", "invalid_range"),
        ("CLIP PL-123456 START=0 END=4 PLAYER=white shirt", "needs_details"),
        ("CLIP PL-123456 START=0 END=4 the player in a white shirt", "needs_details"),
        ("CLIP PL-999999 START=0 END=4", "needs_details"),
        ("CLIP PL-123456 PL-999999 START=0 END=4", "needs_details"),
    ],
)
def test_ambiguous_or_invalid_orders_never_render(room, text, status):
    assert room.handle(message(text=text)) == status
    assert room.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_mail_loop_spam_and_quoted_commands_are_not_orders(room):
    m = message()
    m["labels"] = ["sent"]
    assert room.handle(m) == "ignored"
    m = message(headers={"Auto-Submitted": "auto-replied"})
    assert room.handle(m) == "ignored"
    m = message()
    m["labels"] = ["received", "unauthenticated"]
    assert room.handle(m) == "ignored"
    assert (
        room.handle(message(text="Thanks\n> CLIP PL-123456 START=1 END=4"))
        == "recommended"
    )
    assert room.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_preview_only_serves_registered_ready_unexpired_files(room, monkeypatch):
    room.handle(message(text="CLIP PL-123456 START=1 END=4"))
    row = room.db.execute("SELECT * FROM requests").fetchone()
    monkeypatch.setattr(mail_preview_routes, "ROOT", room.root)
    app = FastAPI()
    app.include_router(mail_preview_routes.router)
    client = TestClient(app)
    assert client.get("/api/mail-previews/" + row["token"]).status_code == 404
    value = json.loads(row["value"])
    value["preview_expires"] = 10**12
    path = room.folder / "previews" / (row["id"] + ".mp4")
    path.parent.mkdir()
    path.write_bytes(b"preview")
    with room.db:
        room.db.execute(
            "UPDATE requests SET status='ready',value=?", (json.dumps(value),)
        )
    response = client.get("/api/mail-previews/" + row["token"])
    assert (
        response.content == b"preview"
        and response.headers["cache-control"] == "private, no-store"
    )
    value["preview_expires"] = 0
    with room.db:
        room.db.execute("UPDATE requests SET value=?", (json.dumps(value),))
    assert client.get("/api/mail-previews/" + row["token"]).status_code == 410
    assert client.get("/api/mail-previews/not-a-token").status_code == 404


def test_uncertain_reply_retries_saved_payload_and_receipts_prevent_duplicates(room):
    room.handle(message())
    calls = []

    class Mail:
        def reply(self, row):
            calls.append((row["id"], row["body"]))
            if len(calls) == 1:
                raise TimeoutError()
            return {"message_id": "receipt"}

    with pytest.raises(TimeoutError):
        flush(room, Mail())
    flush(room, Mail())
    flush(room, Mail())
    assert len(calls) == 2 and calls[0] == calls[1]
    assert room.db.execute("SELECT status FROM outbox").fetchone()[0] == "sent"


def test_expired_uncertain_send_needs_reconciliation_not_another_email(room):
    room.handle(message())
    with room.db:
        room.db.execute("UPDATE outbox SET status='sending',first_attempt=1")

    class Mail:
        def reply(self, row):
            raise AssertionError("must not send")

    flush(room, Mail())
    assert (
        room.db.execute("SELECT status FROM outbox").fetchone()[0]
        == "needs_reconciliation"
    )


def test_changed_source_fails_without_claiming_preview_is_ready(room):
    room.handle(message(text="CLIP PL-123456 START=1 END=4"))
    assert room.render_next() == "failed"
    assert room.db.execute("SELECT status FROM requests").fetchone()[0] == "failed"
    assert not list((room.folder / "previews").glob("*.mp4"))


def test_agentmail_poll_normalizes_utc_to_provider_required_z_timestamp():
    from integrations.agentmail.worker import AgentMail
    from urllib.parse import parse_qs
    import httpx

    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"messages": []})

    mail = AgentMail("test-key", "playlattice@agentmail.to")
    mail.client.close()
    mail.client = httpx.Client(
        base_url="https://api.agentmail.to/v0", transport=httpx.MockTransport(respond)
    )
    assert list(mail.messages("2026-11-08T13:49:28.651271+00:00")) == []
    query = parse_qs(seen[0].url.query.decode())
    assert query["after"] == ["2026-11-08T13:49:28.651271Z"]
    mail.client.close()


def test_customer_reply_has_greeting_real_example_and_branded_html(room):
    room.handle(message())
    row = room.db.execute("SELECT * FROM outbox").fetchone()
    assert row["body"].startswith("Hi there,\n\nThanks for reaching out!")
    assert "CLIP PL-123456 START=0 END=5" in row["body"]
    assert "The PlayLattice Team" in row["body"]
    assert "https://app.example/favicon.png" in row["html"]
    assert 'alt="PlayLattice"' in row["html"]
    with room.db:
        room.outbox(
            "escape-check", "viewer@example.com", "<script>alert(1)</script>", "test"
        )
    assert (
        "<script>"
        not in room.db.execute(
            "SELECT html FROM outbox WHERE message_id='escape-check'"
        ).fetchone()[0]
    )


def test_outreach_can_translate_natural_source_request_but_not_invent_consent(room):
    choice = dict(
        intent="clip",
        reply="I can prepare that moment.",
        video_ids=["PL-123456"],
        start_ms=1000,
        end_ms=4000,
        track_id="7",
    )
    assert (
        room.handle(
            message(text="Please highlight track 7 in PL-123456 from 1 to 4 seconds."),
            decision=choice,
        )
        == "queued"
    )
    assert (
        room.handle(message(mid="invented", text="Recommend a video."), decision=choice)
        == "needs_details"
    )
    assert room.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_outreach_cannot_choose_a_player_from_clothing_without_a_track(room):
    choice = dict(
        intent="clip",
        reply="Here is the player.",
        video_ids=["PL-123456"],
        start_ms=1000,
        end_ms=4000,
        track_id=None,
    )
    assert (
        room.handle(
            message(
                text="Highlight the player in the white shirt in PL-123456 from 1 to 4 seconds."
            ),
            decision=choice,
        )
        == "needs_details"
    )
    assert room.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_outreach_cannot_reuse_a_track_number_as_an_unprovided_end_time(room):
    choice = dict(
        intent="clip",
        reply="I can prepare it.",
        video_ids=["PL-123456"],
        start_ms=1000,
        end_ms=7000,
        track_id="7",
    )
    assert (
        room.handle(
            message(text="Please clip track 7 in PL-123456, starting at 1 second."),
            decision=choice,
        )
        == "needs_details"
    )
    assert room.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
