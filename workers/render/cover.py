"""Social cover cards use only actual source frames and provisional track labels."""

import hashlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def render_cover(source, plan, quizzes, output):
    from workers.render.portrait import font, focus_sample

    cap = cv2.VideoCapture(str(source))
    best = None
    for segment in plan["segments"]:
        lo, hi = segment["source_start_ms"], segment["source_end_ms"]
        for fraction in (0.25, 0.5, 0.75):
            timestamp = round(lo + (hi - lo) * fraction)
            sample = focus_sample(segment["samples"], timestamp)
            if not sample:
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp)
            ok, frame = cap.read()
            if not ok:
                continue
            fh, fw = frame.shape[:2]
            x, y, w, h = sample["bbox"]
            left, top = max(0, int(x * fw)), max(0, int(y * fh))
            right, bottom = min(fw, int((x + w) * fw)), min(fh, int((y + h) * fh))
            roi = frame[top:bottom, left:right]
            if not roi.size:
                continue
            sharpness = cv2.Laplacian(
                cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), cv2.CV_64F
            ).var()
            score = float(sharpness) * min(1, h / 0.4)
            if best is None or score > best[0]:
                best = (score, frame, sample, segment, timestamp)
    cap.release()
    if best is None:
        raise ValueError("No source-grounded cover frame")
    _, frame, sample, segment, timestamp = best
    fh, fw = frame.shape[:2]
    x, y, w, h = sample["bbox"]
    crop_height = min(fh, max(round(fh * 0.5), round(h * fh * 2.4)))
    crop_width = min(fw, round(crop_height * 9 / 16))
    cx = (x + w / 2) * fw
    left = min(fw - crop_width, max(0, round(cx - crop_width / 2)))
    top = min(fh - crop_height, max(0, round(y * fh - crop_height * 0.12)))
    picture = ImageOps.fit(
        Image.fromarray(
            cv2.cvtColor(
                frame[top : top + crop_height, left : left + crop_width],
                cv2.COLOR_BGR2RGB,
            )
        ),
        (720, 1280),
        method=Image.Resampling.LANCZOS,
    )
    overlay = np.zeros((1280, 720, 4), dtype=np.uint8)
    for y in range(1280):
        overlay[y, :, 3] = round(max(0, min(235, (y - 470) / 3)))
    picture = Image.alpha_composite(
        picture.convert("RGBA"), Image.fromarray(overlay)
    ).convert("RGB")
    d = ImageDraw.Draw(picture)
    d.rounded_rectangle(
        (32, 32, 288, 84), radius=13, fill="#0b1420", outline="#748593", width=1
    )
    d.text((47, 46), "PLAYLATTICE", font=font(27, True), fill="white")
    d.rounded_rectangle((32, 711, 319, 752), radius=8, fill="#ff8845")
    d.text((45, 721), "READ THE NEXT MOVE", font=font(21, True), fill="#17110d")
    d.text((31, 781), "CAN YOU", font=font(78, True), fill="white")
    d.text((31, 872), "READ THE", font=font(78, True), fill="white")
    d.text((31, 963), "NEXT MOVE?", font=font(78, True), fill="#ff9758")
    d.line((34, 1080, 636, 1080), fill="#9aa9b7", width=2)
    d.text(
        (34, 1102),
        f"{len(plan['segments'])} PLAYER REPLAYS",
        font=font(29, True),
        fill="white",
    )
    d.text(
        (34, 1145), f"{len(quizzes)} PREDICTION QUIZZES", font=font(25), fill="#bdd5e2"
    )
    d.text(
        (34, 1215),
        "Actual footage. Provisional player labels.",
        font=font(17),
        fill="#b3c1d1",
    )
    output = Path(output)
    picture.save(output, quality=92, optimize=True)
    return dict(
        path=str(output),
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        width=720,
        height=1280,
        source_timestamp_ms=timestamp,
        track_id=segment["track_id"],
        selection="Sharpest valid tracked source frame among bounded candidate samples",
        altered_content="Crop, contrast gradient and text overlay only",
    )
