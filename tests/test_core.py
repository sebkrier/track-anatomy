"""Core unit tests (no models needed):  .venv/bin/python -m unittest discover -s tests"""
import unittest

import numpy as np

from track_anatomy.config import SR
from track_anatomy.pipeline import drums as DR
from track_anatomy.pipeline import grid as G
from track_anatomy.pipeline import harmony as H
from track_anatomy.pipeline import roles as R
from track_anatomy.pipeline import sound as S


class GridTests(unittest.TestCase):
    def test_steady_fit_survives_a_spurious_beat(self):
        T = 60 / 137.0
        beats = list(np.arange(400) * T + 0.1)
        beats.insert(300, beats[299] + 0.12)            # tracker adds a stray beat
        jitter = np.random.default_rng(1).normal(0, 0.008, len(beats))
        g = G.build(np.array(beats) + jitter, beats[::4], 400 * T, 0.1)
        self.assertTrue(g.steady)
        self.assertAlmostEqual(g.bpm, 137.0, delta=0.05)

    def test_off_beat_stretch_does_not_break_a_steady_grid(self):
        # tracker locks onto the off-beats for a 40-beat breakdown: still one tempo
        T = 60 / 144.0
        t = np.arange(500) * T + 0.2
        t[200:240] += T / 2
        g = G.build(t + np.random.default_rng(2).normal(0, 0.006, len(t)), t[::4], 500 * T, 0.2)
        self.assertTrue(g.steady)
        self.assertAlmostEqual(g.bpm, 144.0, delta=0.01)

    def test_whole_number_tempo_is_snapped_but_real_offsets_kept(self):
        rng = np.random.default_rng(3)
        for bpm, want in ((135.98, 136.0), (127.86, 127.86)):
            T = 60 / bpm
            t = np.arange(700) * T + 0.05
            g = G.build(t + rng.normal(0, 0.005, len(t)), t[::4], 700 * T, 0.05)
            self.assertTrue(g.steady)
            self.assertAlmostEqual(g.bpm, want, delta=0.02)

    def test_drifting_tempo_follows_the_beats_and_reports_the_average(self):
        # tempo glides 120 -> 126 BPM: not steady; average ~123, not a frame-quantised median
        periods = 60 / np.linspace(120, 126, 400)
        t = np.concatenate([[0.1], 0.1 + np.cumsum(periods)])
        t_q = np.round(t / 0.02) * 0.02                  # beat_this reports 20 ms frames
        g = G.build(t_q, t_q[::4], float(t[-1]) + 1, 0.1)
        self.assertFalse(g.steady)
        self.assertAlmostEqual(g.bpm, 123.0, delta=0.4)

    def test_downbeats_every_two_beats_give_four_four(self):
        T = 60 / 130.0
        t = np.arange(300) * T
        g = G.build(t, t[::2], 300 * T, 0.0)
        self.assertEqual(g.meter, 4)

    def test_double_time_stretch_is_repaired(self):
        T = 0.64
        base = np.arange(0, 150, T)
        mids = [(a + c) / 2 for a, c in zip(base[:-1], base[1:]) if 60 <= a < 90]   # tracker doubles here
        fixed, n = G.fix_octave_errors(np.sort(np.concatenate([base, mids])))
        self.assertGreater(n, 30)
        self.assertLess(np.max(np.abs(np.diff(fixed) - T)), 0.1)

    def test_beat_time_roundtrip(self):
        T = 0.5
        g = G.Grid(np.arange(-8, 200) * T, 8, 4, 120.0, True, 1.0)
        for t in (0.0, 1.234, 50.0, 99.9, -1.0, 150.0):
            self.assertAlmostEqual(float(g.time(g.beat(t))), t, places=6)


class HarmonyTests(unittest.TestCase):
    def test_parse_labels(self):
        self.assertEqual(H.parse_label("Eb:min7/b3"), {"root": 3, "quality": "min7", "bass": 6})
        self.assertIsNone(H.parse_label("N"))
        self.assertEqual(H.parse_label("C")["quality"], "maj")

    def test_key_from_scale_profile(self):
        # A natural minor weighted towards the tonic triad
        c = np.zeros(12)
        for pc, w in ((9, 5), (0, 4), (4, 4), (2, 2), (5, 2), (7, 2), (11, 1.5)):
            c[pc] = w
        k = H.detect_key(c)
        self.assertEqual((k["tonic"], k["mode"]), ("A", "minor"))

    def test_roman_numerals_in_minor(self):
        key = {"tonic_pc": 8, "mode": "minor"}          # G# minor
        self.assertEqual(H.roman({"root": 4, "quality": "maj", "bass": 4}, key), "VI")
        self.assertEqual(H.roman({"root": 3, "quality": "min", "bass": 3}, key), "v")


class DrumTests(unittest.TestCase):
    def test_bar_clustering_groups_variations(self):
        base = frozenset({("kick:36", s) for s in (0, 4, 8, 12)} | {("snare:38", s) for s in (4, 12)})
        var = base | {("kick:36", 14)}
        other = frozenset({("snare:38", s) for s in range(16)})
        sigs = [base] * 10 + [var] * 3 + [other] * 4 + [None] * 2
        centers = DR._cluster_bars(sigs)
        self.assertEqual(len(centers[0]["bars"]), 13)   # base + its variation
        self.assertEqual(centers[0]["sig"], base)
        self.assertEqual(len(centers[1]["bars"]), 4)

    def test_subdivision_choice(self):
        straight = np.array([i * 0.25 for i in range(200)]) + 0.005
        triplet = np.array([i / 3 for i in range(200)]) + 0.005
        self.assertEqual(DR.choose_subdivision(straight), 4)
        self.assertIn(DR.choose_subdivision(triplet), (3, 6))


class RoleTests(unittest.TestCase):
    def _n(self, bs, be, p):
        return {"s": bs / 2, "e": be / 2, "bs": bs, "be": be, "p": p, "a": 0.6}

    def test_pad_vs_arp(self):
        notes = []
        for bar in range(8):
            for p in (57, 60, 64):                                    # held A minor chord
                notes.append(self._n(bar * 4, bar * 4 + 4, p))
            for i in range(16):                                       # 16th arpeggio above it
                notes.append(self._n(bar * 4 + i * 0.25 + 0.01, bar * 4 + i * 0.25 + 0.2, [69, 72, 76, 81][i % 4]))
        roles = R.split(notes, "other")
        self.assertIn("pad", roles)
        self.assertIn("arp", roles)
        self.assertTrue(all(n["p"] < 65 for n in roles["pad"]))


class EchoTests(unittest.TestCase):
    def test_dotted_eighth_delay_is_found(self):
        rng = np.random.default_rng(0)
        T = 0.5
        g = G.Grid(np.arange(140) * T, 0, 4, 120.0, True, 1.0)
        y = np.zeros(int(140 * T * SR))
        notes = []
        t = np.arange(int(0.25 * SR)) / SR
        for b in range(128):
            for s in (0.0, 0.5):
                if rng.random() < 0.25:
                    continue
                t0 = (b + s) * T
                x = np.sign(np.sin(2 * np.pi * 220 * 2 ** (rng.integers(0, 12) / 12) * t)) * np.exp(-t / 0.06) * 0.3
                for k, amp in ((0, 1.0), (1, 0.5), (2, 0.25)):
                    i = int((t0 + k * 0.75 * T) * SR)
                    if i + len(x) < len(y):
                        y[i:i + len(x)] += x * amp
                notes.append({"s": t0, "e": t0 + 0.2, "p": 60, "a": 0.8})
        res = S.echo(y.astype(np.float32), notes, g)
        self.assertTrue(res["detected"])
        self.assertEqual(res["label"], "dotted 1/8")


class AlsTests(unittest.TestCase):
    def test_live_set_is_consistent(self):
        import gzip
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path

        import soundfile as sf

        from track_anatomy.pipeline import als
        if not als.available():
            self.skipTest("Ableton template not available")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stems").mkdir()
            sf.write(str(tmp / "stems" / "bass.flac"), np.zeros((SR * 8, 2), np.float32), SR, subtype="PCM_24")
            beats = [round(-2.0 + 0.5 * i, 4) for i in range(24)]
            A = {"track": {"title": "Test", "gain": 0.5},
                 "global": {"bpm": 120.0, "meter": 4, "n_bars": 4, "origin_time": 0.0, "steady": True},
                 "grid": {"beats": beats, "first_beat": -4},
                 "sections": [{"b0": 0, "b1": 2, "label": "Intro"}, {"b0": 2, "b1": 4, "label": "Drop"}],
                 "chords": [], "elements": [{"id": "bass", "name": "Bass", "group": "bass", "audio": "stems/bass.flac",
                                             "notes": [[0, 1, 36, 100, 0, 0.5], [1, 2, 38, 90, 0.5, 1.0]]}]}
            out = tmp / "proj"
            out.mkdir()
            p = als.build(A, tmp, out, "C:/x/proj")
            root = ET.fromstring(gzip.decompress(p.read_bytes()))
            ids = [int(e.get("Id")) for e in root.iter()
                   if (e.tag in als.POINTEE_TAGS or e.tag.startswith("ControllerTargets.")) and e.get("Id")]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertGreater(int(root.find("LiveSet/NextPointeeId").get("Value")), max(ids))
            self.assertEqual(root.find("LiveSet/MainTrack/DeviceChain/Mixer/Tempo/Manual").get("Value"), "120")
            self.assertEqual(len(root.find("LiveSet/Locators/Locators")), 2)
            notes = root.findall("LiveSet/Tracks//MidiClip//MidiNoteEvent")   # (the Groove Pool has its own)
            self.assertEqual(len(notes), 2)
            self.assertTrue((out / "Samples" / "Imported" / "bass.wav").exists())


if __name__ == "__main__":
    unittest.main()
