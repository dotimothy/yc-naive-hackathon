"""Original countdown cues remain deterministic, bounded and exportable."""

import wave

import numpy as np
import pytest

from workers.render.quiz_music import CATALOG, synthesize_music


def test_original_cues_are_distinct_deterministic_stereo_audio(tmp_path):
    signals = set()
    for music_id in CATALOG:
        path = tmp_path / f"{music_id}.wav"
        receipt = synthesize_music(path, music_id)
        original = path.read_bytes()
        synthesize_music(path, music_id)
        assert path.read_bytes() == original
        with wave.open(str(path)) as audio:
            assert (audio.getnchannels(), audio.getframerate(), audio.getnframes()) == (
                2,
                48000,
                240000,
            )
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        assert 1000 < np.abs(samples).max() < 15000
        assert receipt["origin"] == "original_procedural_composition"
        signals.add(original)
    assert len(signals) == len(CATALOG)


def test_unknown_music_and_invalid_duration_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        synthesize_music(tmp_path / "bad.wav", "unknown")
    with pytest.raises(ValueError):
        synthesize_music(tmp_path / "bad.wav", "suspense_pulse", seconds=0)
