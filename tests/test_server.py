"""API tests: request guards, upload validation, track lifecycle, grid-fix options.

No models are loaded: the analysis worker is never started, jobs just sit in the queue.
Needs ffmpeg/ffprobe on PATH (uploads are validated with ffprobe).
"""
import io
import json
import wave
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from track_anatomy import server, system, tracks

H = {"X-Track-Anatomy": "1"}
client = TestClient(server.app, base_url="http://127.0.0.1")


def wav_bytes(seconds: float, sr: int = 22050, freq: float = 220.0) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    y = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())
    return buf.getvalue()


def upload(name: str, data: bytes):
    return client.post("/api/tracks", headers=H, files={"file": (name, data, "application/octet-stream")})


# ------------------------------------------------------------------ guards
def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_foreign_host_is_rejected():
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1 must not reach the API
    assert client.get("/api/tracks", headers={"Host": "evil.example"}).status_code == 403


def test_state_changes_need_the_app_header():
    # a cross-site form can POST, but it can't add a custom header
    assert client.post("/api/settings", json={}).status_code == 403
    assert client.post("/api/settings", json={}, headers=H).status_code == 200


def test_cross_site_api_requests_are_refused():
    # a page on another site making the browser fetch the API (even a plain GET)
    assert client.get("/api/tracks", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/tracks", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/tracks", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    # following a link to the app itself is fine
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_pages_cannot_be_framed_by_other_sites():
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_bad_track_ids():
    assert client.get("/api/tracks/NOT_AN_ID/status").status_code in (400, 404)
    assert client.get("/api/tracks/nope-00000000/status").status_code == 404


# ------------------------------------------------------------------ upload validation
def test_upload_rejects_unknown_extension():
    assert upload("setup.exe", b"MZ" * 100).status_code == 400


def test_upload_rejects_non_audio():
    r = upload("fake.mp3", b"definitely not audio" * 200)
    assert r.status_code == 422


def test_upload_rejects_too_short():
    r = upload("short.wav", wav_bytes(3))
    assert r.status_code == 422
    assert "short" in r.json()["detail"]


# ------------------------------------------------------------------ lifecycle
def test_upload_rename_files_delete():
    r = upload("ten seconds.wav", wav_bytes(10))
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert r.json()["existing"] is False
    assert any(t["id"] == tid for t in client.get("/api/tracks").json())

    # same content again -> same id (content-addressed)
    assert upload("copy.wav", wav_bytes(10)).json()["id"] == tid

    assert client.post(f"/api/tracks/{tid}/rename", headers=H, json={"title": "Renamed"}).status_code == 200
    assert client.get(f"/api/tracks/{tid}/status").json()["meta"]["title"] == "Renamed"

    # only whitelisted files are served; no escaping the track folder
    assert client.get(f"/api/tracks/{tid}/file/root/meta.json").status_code == 404
    assert client.get(f"/api/tracks/{tid}/file/root/..%2Fmeta.json").status_code == 404
    assert client.get(f"/api/tracks/{tid}/file/stems/..%2F..%2Fsettings.json").status_code == 404
    assert client.get(f"/api/tracks/{tid}/file/secret/x.flac").status_code == 400

    assert client.delete(f"/api/tracks/{tid}", headers=H).status_code == 200
    assert client.get(f"/api/tracks/{tid}/status").status_code == 404


def test_grid_fixes_are_relative_and_bounded():
    tid = upload("grid.wav", wav_bytes(12, freq=330.0)).json()["id"]
    d = tracks.path(tid)
    (d / "cache").mkdir(exist_ok=True)
    (d / "cache" / "beats.json").write_text(json.dumps({"beats": [], "downbeats": []}))

    def fix(**opts):
        r = client.post(f"/api/tracks/{tid}/reanalyze", headers=H, json=opts)
        return r.status_code, r.json().get("options")

    assert fix(tempo_factor=2) == (200, {"tempo_factor": 2.0})
    assert fix(tempo_factor=2)[0] == 400                      # can't double twice
    assert fix(tempo_factor=0.5) == (200, {})                 # back to the detected tempo
    for _ in range(3):
        fix(downbeat_shift=1)
    assert fix(downbeat_shift=1) == (200, {})                 # 4 shifts in 4/4 = no shift
    assert fix(downbeat_shift=1) == (200, {"downbeat_shift": 1})
    assert fix(reset=True) == (200, {})
    client.delete(f"/api/tracks/{tid}", headers=H)


# ------------------------------------------------------------------ path translation (WSL)
def test_wsl_paths():
    with mock.patch.object(system, "is_wsl", return_value=True), \
         mock.patch.object(system, "is_windows", return_value=False), \
         mock.patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}):
        assert str(system.to_local(r"D:\Music\Ableton")) == "/mnt/d/Music/Ableton"
        assert str(system.to_local(r"\\wsl.localhost\Ubuntu\home\me\x")) == "/home/me/x"
        assert str(system.to_local(r"\\wsl$\Ubuntu\home\me")) == "/home/me"
        assert system.to_live(system.to_local(r"D:\Music")) == "D:/Music"
        assert system.to_display(system.Path("/mnt/d/Music")) == "D:\\Music"
        assert system.to_display(system.Path("/home/me/data")) == "\\\\wsl.localhost\\Ubuntu\\home\\me\\data"
