"""Small original instrumental cue catalog, selected by the clipper agent."""

import math
import wave
from pathlib import Path

import numpy as np

CATALOG = {
    "suspense_pulse": {
        "title": "Suspense Pulse",
        "description": "An original minor-key pulse with a soft ticking rhythm for prediction questions.",
    },
    "soft_countdown": {
        "title": "Soft Countdown",
        "description": "An original gentle major-key plucked countdown, light and playful.",
    },
    "quiet_chords": {
        "title": "Quiet Chords",
        "description": "An original low-energy chord bed that stays behind the analysis.",
    },
}


def synthesize_music(path, music_id, seconds=5, rate=48000):
    if music_id not in CATALOG:
        raise ValueError("Unknown original quiz cue")
    if not 0 < seconds <= 30:
        raise ValueError("Invalid cue duration")
    time = np.arange(round(seconds * rate), dtype=np.float64) / rate
    if music_id == "suspense_pulse":
        beat = time % 0.5
        envelope = np.exp(-beat * 11) * (1 - np.exp(-beat * 180))
        pad = (
            sum(np.sin(2 * math.pi * f * time) for f in (146.83, 174.61, 220.0, 329.63))
            / 4
        )
        notes = np.array([293.66, 329.63, 349.23, 440.0])
        melody = np.sin(
            2 * math.pi * notes[(time / 0.625).astype(int) % 4] * time
        ) * np.exp(-(time % 0.625) * 8)
        sound = (
            0.52 * np.sin(2 * math.pi * 73.416 * time) * envelope
            + 0.18 * pad
            + 0.16 * melody
        )
    elif music_id == "soft_countdown":
        notes = np.array([261.626, 329.628, 391.995, 440.0, 293.665])
        beat = time % 0.5
        sound = (
            0.58
            * np.sin(2 * math.pi * notes[(time / 0.5).astype(int) % 5] * time)
            * np.exp(-beat * 9)
        )
        sound += 0.15 * np.sin(2 * math.pi * 130.813 * time)
    else:
        sound = (
            sum(
                np.sin(2 * math.pi * f * time)
                for f in (220.0, 261.626, 329.628, 493.883)
            )
            / 4
        )
        sound *= 0.55 + 0.1 * np.sin(2 * math.pi * 0.2 * time)
    fade = np.minimum(1, time / 0.08) * np.minimum(1, (seconds - time) / 0.08)
    sound *= fade
    peak = float(np.abs(sound).max())
    sound *= 0.42 / max(peak, 1e-9)
    right = 0.96 * sound + 0.04 * np.roll(sound, round(rate * 0.008))
    stereo = np.stack([sound, right], axis=1)
    samples = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    return {
        "music_id": music_id,
        "title": CATALOG[music_id]["title"],
        "origin": "original_procedural_composition",
        "sample_rate": rate,
        "duration_seconds": seconds,
    }
