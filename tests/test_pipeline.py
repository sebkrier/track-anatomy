"""Decode-step checks on awkward inputs (needs ffmpeg; no models)."""
import json
import wave

import numpy as np
import pytest

from track_anatomy.pipeline import run


def _track(tmp_path, samples: np.ndarray, sr: int = 22050):
    d = tmp_path / "t"
    d.mkdir()
    with wave.open(str(d / "source.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    (d / "meta.json").write_text(json.dumps({"id": "t", "source": "source.wav", "title": "t"}))
    return d


def test_silence_is_an_input_error(tmp_path):
    d = _track(tmp_path, np.zeros(22050 * 12))
    with pytest.raises(run.UserError, match="silent"):
        run.step_decode(d, run.Status(d))


def test_silence_is_caught_even_when_decoding_is_cached(tmp_path):
    d = _track(tmp_path, np.zeros(22050 * 12))
    (d / "stems").mkdir()
    (d / "stems" / "mix.flac").write_bytes(b"")
    meta = json.loads((d / "meta.json").read_text())
    meta.update(gain=1.0, peak=0.0, rms_db=-120.0)
    (d / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(run.UserError, match="silent"):
        run.step_decode(d, run.Status(d))


def test_too_short_is_an_input_error(tmp_path):
    t = np.arange(22050 * 3) / 22050
    d = _track(tmp_path, 0.3 * np.sin(2 * np.pi * 220 * t))
    with pytest.raises(run.UserError, match="long"):
        run.step_decode(d, run.Status(d))


def test_normal_audio_decodes_with_headroom(tmp_path):
    t = np.arange(22050 * 10) / 22050
    d = _track(tmp_path, 0.9 * np.sin(2 * np.pi * 220 * t))
    assert run.step_decode(d, run.Status(d)) is False           # not cached: it did the work
    meta = json.loads((d / "meta.json").read_text())
    assert abs(meta["duration"] - 10.0) < 0.05
    assert meta["gain"] <= 1.0 and meta["peak"] * meta["gain"] <= 0.51
    assert (d / "stems" / "mix.flac").exists()
    assert run.step_decode(d, run.Status(d)) is True            # second time: cached


# ------------------------------------------------------------------ section naming / splitting
from track_anatomy.pipeline import structure as ST  # noqa: E402


def _sec(raw, e=0.5, kick=0.0, voc=0.0, bass=0.5, **kw):
    return {"raw": raw, "energy": e, "energy_db": -20.0, "kick": kick, "drums": kick, "vocals": voc,
            "bass": bass, "busy": 0.9, **kw}


def test_staged_intro_until_the_song_starts():
    assert ST._name(_sec("intro", e=0.3, cont=True), False, False, True, started=False) == "Intro"
    # the model's "intro" label running on after the song proper has begun
    assert ST._name(_sec("intro", e=0.3, cont=True), False, False, True, started=True) == "Breakdown"
    assert ST._name(_sec("intro", e=0.3, cont=True), False, False, False, started=True, has_drums=False) == "Interlude"
    assert ST._name(_sec("intro", e=0.9, kick=0.9, cont=True), False, False, True, started=True) == "Drop"


def test_quiet_sections_are_breakdowns_only_when_the_track_has_drums():
    assert ST._name(_sec("inst", e=0.3), False, False, False, has_drums=True) == "Breakdown"
    assert ST._name(_sec("inst", e=0.3), False, False, False, has_drums=False) == "Instrumental"


def test_quiet_but_busy_is_not_silence():
    s = _sec("verse", e=0.02, voc=0.9)
    assert ST._name(s, False, False, False) != "Silence"


def test_long_section_splits_where_the_drums_come_in():
    n = 48
    act = {"pad": np.ones(n), "kick": np.r_[np.zeros(24), np.ones(24)], "hh": np.r_[np.zeros(24), np.ones(24)]}
    secs = [{"b0": 0, "b1": n, "raw": "intro", "phrases": [[0, n]]}]
    out = ST._split_long(secs, act, np.full(n, -20.0))
    assert [(s["b0"], s["b1"]) for s in out] == [(0, 24), (24, 48)]
    assert out[1].get("cont")
