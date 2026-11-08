"""Portrait player replays and evidence-grounded prediction quizzes; no inference."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import subprocess
import textwrap
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROFILE = "player-portrait-quiz-v1"
WIDTH, HEIGHT, FPS = 720, 1280, 30
QUIZ_HOLD_SECONDS = 5
VIEW_Y, VIEW_H = 130, 620


def valid_box(box):
    return (
        isinstance(box, (list, tuple))
        and len(box) == 4
        and all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)
        and box[2] > 0
        and box[3] > 0
        and box[0] < 1
        and box[1] < 1
        and box[0] + box[2] > 0
        and box[1] + box[3] > 0
    )


def nearest(samples, timestamp, tolerance=400):
    if not samples:
        return None
    sample = min(samples, key=lambda s: abs(s["timestamp_ms"] - timestamp))
    return sample if abs(sample["timestamp_ms"] - timestamp) <= tolerance else None


def focus_sample(samples, timestamp):
    before = next(
        (s for s in reversed(samples) if s["timestamp_ms"] <= timestamp), None
    )
    after = next((s for s in samples if s["timestamp_ms"] >= timestamp), None)
    if (
        before
        and after
        and 0 < after["timestamp_ms"] - before["timestamp_ms"] <= 550
        and before.get("shot_id") == after.get("shot_id")
    ):
        fraction = (timestamp - before["timestamp_ms"]) / (
            after["timestamp_ms"] - before["timestamp_ms"]
        )
        return dict(
            before,
            bbox=[
                a + (b - a) * fraction for a, b in zip(before["bbox"], after["bbox"])
            ],
            interpolated_focus=True,
        )
    return nearest(samples, timestamp, 175)


def arms(sample):
    points = {
        p["name"]: p
        for p in sample.get("keypoints", [])
        if p.get("confidence", 0) >= 0.6 and p.get("visibility", 1)
    }
    return sum(
        1
        for side in ("left", "right")
        if side + "_wrist" in points
        and side + "_shoulder" in points
        and points[side + "_wrist"]["y"]
        < points[side + "_shoulder"]["y"] - 0.1 * sample["bbox"][3]
    )


def movement(a, b):
    return b["bbox"][0] + b["bbox"][2] / 2 - a["bbox"][0] - a["bbox"][2] / 2


def observation(samples, timestamp):
    current = nearest(samples, timestamp, 175)
    if not current:
        return "Track temporarily obscured", "Context view; no player attribution"
    old = nearest(samples, timestamp - 650)
    raised = arms(current)
    if old and current["timestamp_ms"] > old["timestamp_ms"]:
        dx = movement(old, current)
        headline = (
            "Moves right in the court view"
            if dx > 0.012
            else "Moves left in the court view"
            if dx < -0.012
            else "Holds a similar screen position"
        )
        detail = f"Measured horizontal shift: {abs(dx) * 100:.1f}% of frame"
    else:
        headline, detail = (
            "Tracking the highlighted player",
            "Motion observations; identity unverified",
        )
    if raised:
        detail = "Both arms raised" if raised == 2 else "One arm raised"
    return headline, detail


def build_plan(snapshot, max_players=6):
    """One best candidate per fragment, bounded to the selected play and camera shot."""
    job, selected = snapshot["job_id"], snapshot["selected"]
    tracks = {}
    for sample in snapshot["tracks"]:
        if (
            valid_box(sample.get("bbox"))
            and sample.get("confidence", 0) >= 0.5
            and sample.get("role_status") != "likely_non_player"
        ):
            tracks.setdefault(str(sample["track_id"]), []).append(sample)
    for values in tracks.values():
        values.sort(key=lambda x: x["timestamp_ms"])
    shots = {str(s["shot_id"]): s for s in snapshot["shots"]}
    best = {}
    for candidate in snapshot["candidates"]:
        tid, sid = str(candidate.get("track_id", "")), str(candidate.get("shot_id", ""))
        if (
            not str(candidate.get("candidate_id", "")).startswith(job + "-")
            or sid != str(selected["shot_id"])
            or sid not in shots
        ):
            continue
        all_values = [s for s in tracks.get(tid, []) if str(s.get("shot_id")) == sid]
        runs = []
        for sample in all_values:
            if not runs or sample["timestamp_ms"] - runs[-1][-1]["timestamp_ms"] > 800:
                runs.append([])
            runs[-1].append(sample)
        shot = shots[sid]
        eligible = []
        for run in runs:
            low = max(
                shot["start_ms"], selected["source_start_ms"], run[0]["timestamp_ms"]
            )
            high = min(
                shot["end_ms"], selected["source_end_ms"], run[-1]["timestamp_ms"] + 250
            )
            overlap = min(high, candidate.get("action_end_ms", high)) - max(
                low, candidate.get("action_start_ms", low)
            )
            if len(run) >= 5 and high - low >= 1800 and overlap > 0:
                eligible.append((overlap, run, low, high))
        if not eligible:
            continue
        _, values, low, high = max(eligible, key=lambda item: item[0])
        if high - low < 1800:
            continue
        start = max(low, min(candidate.get("action_start_ms", low) - 500, high - 2800))
        end = min(high, start + 3500)
        evidence = [s for s in values if start <= s["timestamp_ms"] <= end]
        if (
            len(evidence) < 5
            or max(
                b["timestamp_ms"] - a["timestamp_ms"]
                for a, b in zip(evidence, evidence[1:])
            )
            > 800
        ):
            continue
        item = dict(
            track_id=tid,
            label="Track " + tid,
            shot_id=sid,
            source_start_ms=round(start),
            source_end_ms=round(end),
            candidate_id=candidate["candidate_id"],
            score=candidate.get("score", 0),
            samples=values,
        )
        if tid not in best or item["score"] > best[tid]["score"]:
            best[tid] = item
    segments = sorted(
        sorted(best.values(), key=lambda s: -s["score"])[:max_players],
        key=lambda s: (s["source_start_ms"], -s["score"]),
    )
    for index, segment in enumerate(segments):
        segment["display_label"] = "Player " + chr(65 + index)
    quizzes = []
    for segment in segments:
        values = segment["samples"]
        # Later evidence supplies the answer; it is never inferred from the prompt alone.
        for timestamp in range(
            segment["source_start_ms"] + 450, segment["source_end_ms"] - 1200, 250
        ):
            a, b = (
                nearest(values, timestamp, 170),
                nearest(values, timestamp + 1000, 170),
            )
            span = [
                s
                for s in values
                if a
                and b
                and a["timestamp_ms"] <= s["timestamp_ms"] <= b["timestamp_ms"]
            ]
            if (
                not a
                or not b
                or len(span) < 3
                or max(
                    (
                        y["timestamp_ms"] - x["timestamp_ms"]
                        for x, y in zip(span, span[1:])
                    ),
                    default=999,
                )
                > 550
            ):
                continue
            dx = movement(a, b)
            if 0.012 < abs(dx) < 0.025:
                continue  # Ambiguous movement: do not force a quiz answer.
            offsets = [movement(a, s) for s in span]
            if (
                (dx >= 0.025 and min(offsets) < -0.012)
                or (dx <= -0.025 and max(offsets) > 0.012)
                or (abs(dx) <= 0.012 and max(abs(v) for v in offsets) > 0.012)
            ):
                continue
            correct = "A" if dx <= -0.025 else "B" if dx >= 0.025 else "C"
            quiz_id = hashlib.sha256(
                f"{job}:{segment['track_id']}:{timestamp}".encode()
            ).hexdigest()[:20]
            quizzes.append(
                dict(
                    candidate_id=quiz_id,
                    track_id=segment["track_id"],
                    shot_id=segment["shot_id"],
                    freeze_ms=timestamp,
                    reveal_ms=timestamp + 1000,
                    question="In the full-court view, which way will the highlighted player shift next?",
                    options=[
                        dict(id="A", text="Left on screen"),
                        dict(id="B", text="Right on screen"),
                        dict(id="C", text="Little sideways movement"),
                    ],
                    correct_option_id=correct,
                    explanation=f"The tracked center shifts {abs(dx) * 100:.1f}% of frame width over the next second.",
                    evidence=dict(
                        start_ms=a["timestamp_ms"],
                        end_ms=b["timestamp_ms"],
                        horizontal_shift=dx,
                    ),
                )
            )
            break
    return dict(
        profile=PROFILE,
        presentation_style="broadcast-quiz-v2",
        job_id=job,
        source_sha256=snapshot["source_sha256"],
        attribution=snapshot.get("attribution", "TrackID3x3, CC BY 4.0"),
        segments=segments,
        quiz_candidates=quizzes,
        warnings=[
            "Track fragments are provisional identities",
            "Play-by-play describes measured image motion, not verified basketball outcomes",
        ],
        music_configured=False,
    )


def crop_rect(frame_width, frame_height, bbox, previous=None):
    x, y, w, h = bbox
    x1, y1 = max(0, x * frame_width), max(0, y * frame_height)
    x2, y2 = (
        min(frame_width, (x + w) * frame_width),
        min(frame_height, (y + h) * frame_height),
    )
    ratio = WIDTH / VIEW_H
    maximum = min(frame_height, frame_width / ratio)
    if y2 - y1 > maximum * 0.85 or x2 - x1 > maximum * ratio * 0.85:
        return (0, 0, frame_width, frame_height), None
    ch = min(
        maximum, max(frame_height * 0.52, (y2 - y1) * 1.7, (x2 - x1) * 1.4 / ratio)
    )
    cw = min(frame_width, ch * ratio)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if previous:
        cx = 0.8 * previous[0] + 0.2 * cx
        cy = 0.8 * previous[1] + 0.2 * cy
        ch = 0.88 * previous[2] + 0.12 * ch
        ch = max(ch, (y2 - y1) * 1.25, (x2 - x1) * 1.25 / ratio)
        ch = min(ch, maximum)
        cw = ch * ratio
    # Keep the observed body inside the crop even while the camera eases.
    cx = min(max(cx, x2 - cw / 2 + 8), x1 + cw / 2 - 8)
    cy = min(max(cy, y2 - ch / 2 + 8), y1 + ch / 2 - 8)
    cx, cy = (
        min(max(cx, cw / 2), frame_width - cw / 2),
        min(max(cy, ch / 2), frame_height - ch / 2),
    )
    return (
        round(cx - cw / 2),
        round(cy - ch / 2),
        round(cx + cw / 2),
        round(cy + ch / 2),
    ), (cx, cy, ch)


@lru_cache(maxsize=24)
def font(size, bold=False):
    paths = [
        f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    raise RuntimeError("A readable TrueType font is required for captions")


def text(draw, xy, message, size=26, color="white", bold=False, width=46):
    y = xy[1]
    for line in textwrap.wrap(str(message), width=width)[:4]:
        draw.text((xy[0], y), line, font=font(size, bold), fill=color)
        y += size + 8
    return y


def draw_frame(
    frame,
    segment,
    timestamp,
    previous,
    quiz=None,
    freeze=False,
    replay_number=1,
    replay_count=1,
    countdown=None,
    attribution="TrackID3x3, CC BY 4.0",
):
    fh, fw = frame.shape[:2]
    sample = focus_sample(segment["samples"], timestamp)
    if sample:
        rect, previous = crop_rect(fw, fh, sample["bbox"], previous)
        left, t, r, b = rect
        scale = min(WIDTH / (r - left), VIEW_H / (b - t))
        fitted = cv2.resize(
            frame[t:b, left:r],
            (max(1, round((r - left) * scale)), max(1, round((b - t) * scale))),
        )
        ox, oy = (WIDTH - fitted.shape[1]) // 2, (VIEW_H - fitted.shape[0]) // 2
        view = np.zeros((VIEW_H, WIDTH, 3), dtype=np.uint8)
        view[oy : oy + fitted.shape[0], ox : ox + fitted.shape[1]] = fitted
        x, y, w, h = sample["bbox"]
        box = (
            round((x * fw - left) * scale + ox),
            round((y * fh - t) * scale + oy),
            round(((x + w) * fw - left) * scale + ox),
            round(((y + h) * fh - t) * scale + oy),
        )
        cv2.rectangle(
            view,
            (max(0, box[0]), max(0, box[1])),
            (min(WIDTH - 1, box[2]), min(VIEW_H - 1, box[3])),
            (75, 230, 190),
            3,
        )
    else:
        view = np.zeros((VIEW_H, WIDTH, 3), dtype=np.uint8)
        scale = min(WIDTH / fw, VIEW_H / fh)
        fit = cv2.resize(frame, (max(1, round(fw * scale)), max(1, round(fh * scale))))
        y, x = (VIEW_H - fit.shape[0]) // 2, (WIDTH - fit.shape[1]) // 2
        view[y : y + fit.shape[0], x : x + fit.shape[1]] = fit
    canvas = np.full((HEIGHT, WIDTH, 3), (24, 18, 12), dtype=np.uint8)
    canvas[VIEW_Y : VIEW_Y + VIEW_H] = view
    # Always preserve a full-court inset so a zoom does not erase tactical context.
    inset_scale = min(224 / fw, 150 / fh)
    iw, ih = max(1, round(fw * inset_scale)), max(1, round(fh * inset_scale))
    inset = cv2.resize(frame, (iw, ih))
    if sample:
        x, y, w, h = sample["bbox"]
        cv2.rectangle(
            inset,
            (int(x * iw), int(y * ih)),
            (int((x + w) * iw), int((y + h) * ih)),
            (75, 230, 190),
            2,
        )
    ix, iy = WIDTH - iw - 24, VIEW_Y + 20
    canvas[iy : iy + ih, ix : ix + iw] = inset
    image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(image)
    # Broadcast-style hierarchy: brand, player, film, then one clear insight.
    d.rectangle((0, 0, WIDTH, 125), fill="#0b111a")
    d.rounded_rectangle((30, 17, 78, 54), radius=9, fill="#ff7834")
    d.text((39, 24), "PL", font=font(22, True), fill="#18110b")
    text(d, (91, 20), "PLAYLATTICE", 24, bold=True)
    d.text((480, 26), "READ THE GAME", font=font(14, True), fill="#899bb2")
    label = segment.get("display_label", segment["label"])
    d.rounded_rectangle((30, 69, 205, 105), radius=8, fill="#192737", outline="#344457")
    d.text((43, 75), label, font=font(22, True), fill="white")
    d.text(
        (228, 78),
        f"REPLAY {replay_number:02}/{replay_count:02}",
        font=font(17, True),
        fill="#9eafc2",
    )
    d.text((480, 78), f"SOURCE {timestamp / 1000:.1f}s", font=font(17), fill="#9eafc2")
    span = max(1, segment["source_end_ms"] - segment["source_start_ms"])
    progress = min(1, max(0, (timestamp - segment["source_start_ms"]) / span))
    d.rectangle((30, 116, 640, 119), fill="#263548")
    d.rectangle((30, 116, 30 + round(610 * progress), 119), fill="#ff883f")
    d.rectangle((0, VIEW_Y, WIDTH - 1, VIEW_Y + VIEW_H), outline="#34465a", width=2)
    d.rectangle((ix - 2, iy - 2, ix + iw + 1, iy + ih + 1), outline="#d4e1ed", width=2)
    d.rectangle((ix, iy, ix + min(iw, 126), iy + 19), fill="#0b111a")
    d.text((ix + 7, iy + 3), "COURT VIEW", font=font(12, True), fill="#dce7ee")
    panel_y = VIEW_Y + VIEW_H + 20
    d.rounded_rectangle(
        (16, panel_y, 660, 1212), radius=20, fill="#101b29", outline="#293c52", width=2
    )
    if segment.get("story_beats"):
        from workers.render.story import story_beat

        beat = story_beat(segment, timestamp)
        text(d, (34, 800), beat["title"], 22, color="#ff9758", bold=True)
        text(d, (34, 853), beat["headline"], 36, bold=True, width=30)
        text(d, (34, 990), beat["caption"], 25, color="#bfd0e1", width=42)
    elif quiz and freeze:
        text(d, (34, 792), "WHAT HAPPENS NEXT?", 29, color="#ff9758", bold=True)
        short_question = (
            "Which Way Will This Player Shift?"
            if "which way" in quiz["question"].lower()
            else quiz["question"].replace("track", "player")
        )
        text(d, (34, 839), short_question, 30, width=33, bold=True)
        text(
            d,
            (34, 917),
            "Screen direction in the court view",
            18,
            color="#9fb3c9",
            width=55,
        )
        for index, option in enumerate(quiz["options"]):
            top = 953 + index * 66
            d.rounded_rectangle(
                (32, top, 640, top + 56),
                radius=12,
                fill="#1b2c40",
                outline="#425875",
                width=2,
            )
            d.rounded_rectangle((43, top + 9, 82, top + 47), radius=8, fill="#ff8845")
            d.text((55, top + 14), option["id"], font=font(24, True), fill="#15100b")
            text(d, (99, top + 13), option["text"], 25, bold=True, width=45)
        remaining = max(
            0,
            min(
                QUIZ_HOLD_SECONDS,
                countdown if countdown is not None else QUIZ_HOLD_SECONDS,
            ),
        )
        d.ellipse((582, 788, 638, 844), fill="#1b2c40", outline="#435875", width=3)
        d.arc(
            (582, 788, 638, 844),
            -90,
            -90 + 360 * remaining / QUIZ_HOLD_SECONDS,
            fill="#ff9758",
            width=5,
        )
        d.text(
            (610, 815), str(remaining), font=font(29, True), fill="white", anchor="mm"
        )
        for tick in range(QUIZ_HOLD_SECONDS):
            left = 34 + tick * 121
            d.rounded_rectangle(
                (left, 1164, left + 109, 1170),
                radius=3,
                fill="#ff9758" if tick < remaining else "#2b3c51",
            )
        text(
            d,
            (34, 1181),
            "PICK A, B OR C BEFORE PLAY RESUMES",
            15,
            color="#b6c6d8",
            width=65,
        )
    elif quiz and timestamp >= quiz["reveal_ms"]:
        answer = next(
            o["text"] for o in quiz["options"] if o["id"] == quiz["correct_option_id"]
        )
        text(d, (34, 799), "THE NEXT MOVE", 22, color="#a4e984", bold=True)
        text(
            d, (34, 849), "ANSWER: " + answer, 34, color="#d9ffc4", bold=True, width=31
        )
        y = text(d, (34, 950), quiz["explanation"], 26, width=40)
        d.rounded_rectangle(
            (32, 1094, 640, 1170), radius=13, fill="#1e392c", outline="#6fb080", width=2
        )
        d.line((50, 1132, 62, 1144, 84, 1119), fill="#bdff9e", width=5)
        text(
            d,
            (103, 1117),
            "Evidence, Then The Reveal",
            26,
            color="#d9ffc4",
            bold=True,
            width=38,
        )
    else:
        headline, detail = observation(segment["samples"], timestamp)
        text(d, (34, 800), "PLAYER SPOTLIGHT", 22, color="#ff9758", bold=True)
        text(d, (34, 853), headline, 40, bold=True, width=27)
        text(d, (34, 975), detail, 27, color="#bfd0e1", width=40)
        d.rectangle((34, 1092, 38, 1161), fill="#ff8845")
        text(
            d,
            (55, 1095),
            "Tracked movement. Watch the next decision.",
            22,
            color="#9fb3c9",
            width=43,
        )
    text(
        d,
        (30, 1231),
        "Provisional player labels  |  " + attribution,
        14,
        color="#94a7bb",
        width=90,
    )
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), previous


def render_portrait(
    source, plan, quizzes, output, music=None, should_cancel=lambda: False
):
    if not plan["segments"]:
        raise ValueError("No sufficiently observed highlighted player tracks")
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or fps <= 0:
        raise ValueError("Cannot decode source")
    active = {q["track_id"]: q for q in quizzes}
    timeline = []
    for number, segment in enumerate(plan["segments"]):
        quiz = active.get(segment["track_id"])
        timestamps = [
            segment["source_start_ms"] + i * 1000 / FPS
            for i in range(
                math.ceil(
                    (segment["source_end_ms"] - segment["source_start_ms"]) * FPS / 1000
                )
            )
        ]
        frozen = False
        for timestamp in timestamps:
            if quiz and not frozen and timestamp >= quiz["freeze_ms"]:
                for tick in range(FPS * QUIZ_HOLD_SECONDS):
                    timeline.append(
                        (
                            number,
                            float(quiz["freeze_ms"]),
                            True,
                            math.ceil(QUIZ_HOLD_SECONDS - tick / FPS),
                        )
                    )
                frozen = True
            timeline.append((number, timestamp, False, None))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.mp4")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "2",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    previous = None
    last_segment = -1
    last_index = -1
    last_frame = None
    try:
        for number, timestamp, freeze, countdown in timeline:
            if should_cancel():
                raise RuntimeError("Portrait rendering cancelled")
            target = round(timestamp * fps / 1000)
            if number != last_segment or target < last_index:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                last_index = target - 1
                previous = None
            while last_index < target:
                ok, last_frame = cap.read()
                if not ok:
                    raise ValueError("Source ended before requested track replay")
                last_index += 1
            if last_frame is None:
                raise ValueError("Missing source frame")
            canvas, previous = draw_frame(
                last_frame,
                plan["segments"][number],
                timestamp,
                previous,
                active.get(plan["segments"][number]["track_id"]),
                freeze,
                number + 1,
                len(plan["segments"]),
                countdown,
                plan.get("attribution", "Source attribution in caption"),
            )
            process.stdin.write(canvas.tobytes())
            last_segment = number
        process.stdin.close()
        error = process.stderr.read()
        status = process.wait(timeout=120)
        if status:
            raise RuntimeError(
                "Portrait encoder failed: " + error.decode(errors="replace")[-600:]
            )
        temporary.replace(output)
    except BaseException:
        process.kill()
        process.wait()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        cap.release()
    # Audio is assembled separately so inserted quiz pauses do not leak future gameplay audio.
    audio_parts = []
    cursor = 0
    music_cues = []
    for segment in plan["segments"]:
        quiz = active.get(segment["track_id"])
        duration = (segment["source_end_ms"] - segment["source_start_ms"]) / 1000
        if quiz:
            before = (quiz["freeze_ms"] - segment["source_start_ms"]) / 1000
            audio_parts.extend(
                [
                    dict(source_start_ms=segment["source_start_ms"], duration=before),
                    dict(silence=True, duration=QUIZ_HOLD_SECONDS),
                    dict(source_start_ms=quiz["freeze_ms"], duration=duration - before),
                ]
            )
            music_cues.append(
                dict(
                    start_seconds=cursor + before,
                    duration_seconds=QUIZ_HOLD_SECONDS,
                    candidate_id=quiz["candidate_id"],
                    track_id=quiz["track_id"],
                )
            )
            cursor += duration + QUIZ_HOLD_SECONDS
        else:
            audio_parts.append(
                dict(source_start_ms=segment["source_start_ms"], duration=duration)
            )
            cursor += duration
    audio_receipt = add_audio(source, output, audio_parts, music, music_cues)
    result = dict(
        profile=plan.get("profile", PROFILE),
        presentation_style=plan.get("presentation_style", "broadcast-quiz-v2"),
        stories=[
            dict(
                track_id=s["track_id"],
                source_start_ms=s["source_start_ms"],
                source_end_ms=s["source_end_ms"],
                beats=s["story_beats"],
                evidence=s["story_evidence"],
            )
            for s in plan["segments"]
            if s.get("story_beats")
        ],
        width=WIDTH,
        height=HEIGHT,
        fps=FPS,
        frame_count=len(timeline),
        duration_ms=round(len(timeline) * 1000 / FPS),
        captions_burned_in=True,
        track_count=len(plan["segments"]),
        quiz_count=len(quizzes),
        music_cues=music_cues,
        **audio_receipt,
    )
    return result


def add_audio(source, video, parts, music, cues):
    metadata = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    has_audio = any(s["codec_type"] == "audio" for s in metadata["streams"])
    filters = []
    labels = []
    real_parts = sum(not part.get("silence") for part in parts) if has_audio else 0
    if real_parts:
        filters.append(
            f"[1:a]asplit={real_parts}"
            + "".join(f"[source{i}]" for i in range(real_parts))
        )
    source_index = 0
    for i, part in enumerate(parts):
        label = f"a{i}"
        if has_audio and not part.get("silence"):
            filters.append(
                f"[source{source_index}]atrim=start={part['source_start_ms'] / 1000:.6f}:duration={part['duration']:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[{label}]"
            )
            source_index += 1
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={part['duration']:.6f}[{label}]"
            )
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"concat=n={len(parts)}:v=0:a=1[bed]")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(video),
        "-i",
        str(source),
    ]
    audio = "[bed]"
    if music and cues:
        if not Path(music).is_file():
            raise ValueError("Quiz music asset is missing")
        command += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(
            f"[2:a]asplit={len(cues)}" + "".join(f"[cue{i}]" for i in range(len(cues)))
        )
        for i, cue in enumerate(cues):
            delay = round(cue["start_seconds"] * 1000)
            hold = cue["duration_seconds"]
            filters.append(
                f"[cue{i}]atrim=duration={hold},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,volume=0.32,afade=t=in:d=0.15,afade=t=out:st={hold - 0.35}:d=0.35,adelay={delay}|{delay}[music{i}]"
            )
        filters.append(
            "[bed]"
            + "".join(f"[music{i}]" for i in range(len(cues)))
            + f"amix=inputs={len(cues) + 1}:duration=first:normalize=0[mixed]"
        )
        audio = "[mixed]"
    target = Path(video).with_suffix(".audio.mp4")
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v:0",
        "-map",
        audio,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    subprocess.run(command, capture_output=True, timeout=120, check=True)
    target.replace(video)
    return dict(
        original_audio_preserved=has_audio,
        quiz_music_configured=bool(music),
        quiz_music_missing=bool(cues and not music),
    )
