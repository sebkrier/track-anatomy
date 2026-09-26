"""Turn measurements into Ableton starting points.

Rules of thumb only: each tip carries the evidence it came from so the producer can
judge it. Device names refer to Ableton Live 12 Suite stock devices.
"""


def _fmt_hz(f: float) -> str:
    return f"{f / 1000:.1f} kHz" if f >= 1000 else f"{f:.0f} Hz"


def _env_line(env: dict, default_release: str = "") -> str | None:
    parts = []
    a = env.get("attack_ms")
    if a is not None:
        parts.append(f"A {max(1, round(a)):.0f} ms")
    s = env.get("sustain_label")
    if s == "sustained":
        parts.append("S high")
    elif s == "decaying":
        # only the level while the note is held is measured, not the decay time: give a range
        sd = env.get("sustain_db")
        parts.append("D 200-500 ms, " + (f"S {sd:.0f} dB" if sd is not None else "S mid"))
    elif s:
        parts.append("D 150-250 ms, S 0")
    t = env.get("tail_s")
    if t is not None:
        parts.append(f"R/tail ~{t:.1f} s")
    elif default_release:
        parts.append(default_release)
    return "Amp envelope: " + ", ".join(parts) if parts else None


def _fx_lines(f: dict, is_bass: bool = False) -> list[str]:
    out = []
    pc = f.get("pumping")
    if pc and pc.get("detected"):
        src = "the kick" if pc.get("kick_locked", 0) >= 0.5 else "a ghost kick / LFO"
        out.append(f"Sidechain pump ~{pc['depth_db']:.0f} dB, recovers in ~{pc['release_ms']:.0f} ms: Compressor "
                   f"sidechained from {src} (ratio 4:1+, fast attack, release ~{pc['release_ms']:.0f} ms), "
                   f"or Shaper / Auto Pan (Shape) synced to 1/4.")
    w = f.get("width", {})
    if w.get("label") == "very wide" and not is_bass:
        out.append(f"Very wide ({w['side_mid_db']} dB side/mid): unison with stereo spread, Chorus-Ensemble, or a Haas-style delay.")
    elif w.get("label") == "wide" and not is_bass:
        out.append("Wide stereo image: Chorus-Ensemble or unison spread.")
    t = f.get("envelope", {}).get("tail_s")
    if t and t >= 1.0 and not is_bass:
        out.append(f"Long tail (~{t:.1f} s after note-off): Reverb (Hall) decay ~{t:.1f} s, or long release.")
    elif t and t >= 0.5 and not is_bass:
        out.append(f"Short tail (~{t:.1f} s): small Room/Plate reverb or medium release.")
    for m in f.get("filter_moves", []):
        verb = "opens" if m["direction"] == "opens" else "closes"
        out.append(f"Filter {verb} over bars {m['bar0'] + 1}-{m['bar1'] + 1} (~{m['octaves']:.1f} octaves): "
                   f"automate the low-pass cutoff (Auto Filter or the synth's filter).")
    ec = f.get("echo")
    if ec and ec.get("detected"):
        fb = ec.get("feedback", 0)
        out.append(f"Tempo-synced delay: repeats at {ec['label']} (~{ec['ms']:.0f} ms). Echo or Delay synced to "
                   f"{ec['label']}, feedback ~{round(fb * 100 / 5) * 5:.0f}%" + (" (ping-pong if it moves in stereo)." if not is_bass else "."))
    wet = f.get("wet")
    if wet and wet.get("wet_db") is not None and wet["wet_db"] > -12:
        out.append(f"Reverb/echo wash is strong ({wet['wet_db']:.0f} dB vs dry): send ~{_send(wet['wet_db'])} to a reverb return.")
    return out


def _send(wet_db: float) -> str:
    return "40-60%" if wet_db > -6 else "20-35%"


def melodic(role: str, kind: str, f: dict) -> dict:
    """role: pad/stab/lead/arp/low/bass/vocals/keys/guitar."""
    tim = f.get("timbre", {})
    env = f.get("envelope", {})
    ns = f.get("notes", {})
    wave = tim.get("wave")
    mv = tim.get("movement_label", "")
    reach = tim.get("reach_hz")
    device, osc, extra = None, [], []

    if kind == "vocals":
        device = "Audio track (use the vocal stem) or Simpler/Sampler for chops"
        osc.append("The MIDI is the vocal melody: useful to double with a synth or to check the topline.")
    elif kind == "piano":
        device = "Keys: Grand Piano / Electric (Suite) or a piano instrument"
    elif kind == "guitar":
        device = "Guitar: record/sample, or Electric (plucked) / Wavetable pluck as a sketch"
    elif role in ("bass", "low"):
        sus = env.get("sustain_db")
        glide = ns.get("glide_fraction", 0)
        if wave and wave.startswith("Sine") and ((sus is not None and sus < -8) or glide >= 0.1):
            device = "808: Drum Sampler / Simpler with an 808 sample (or Operator sine + pitch envelope)"
            osc.append("Sine-like, decaying bass" + (" with pitch glides" if glide >= 0.1 else "") +
                       ": an 808. Play it mono/legato with Glide on so slides work; tune the sample to the key.")
        elif wave and wave.startswith("Sine"):
            device = "Operator (sine) or Drift (sine / shape low)"
            osc.append("Pure sub: single sine oscillator, keep it mono.")
        elif "strong movement" in mv:
            device = "Wavetable (two detuned saws: Reese-style)"
            osc.append("Detuned unison saws (Wavetable Unison 'Classic', 2-4 voices, amount 20-40%).")
        else:
            device = "Analog / Drift / Wavetable"
            if wave:
                osc.append(f"Oscillator: {wave.split(' (')[0].lower()}.")
    elif role == "pad":
        device = "Wavetable (warm saw or strings table)" if "strong" in mv or (wave or "").startswith("Saw") else "Wavetable / Drift pad"
        osc.append("Chords held for bars: slow attack, long release, lots of voices.")
        if "strong movement" in mv:
            osc.append("Detuned unison (Wavetable Unison 'Shimmer'/'Classic', 4-8 voices).")
    elif role == "stab":
        device = "Wavetable / Analog (chord stab)"
        osc.append("Short chords: fast attack, short decay, little sustain.")
    elif role == "riff":
        device = "Wavetable (polyphonic, the hook)"
        cf = f.get("chord_fraction")
        osc.append("One instrument playing the hook: single notes and chords"
                   + (f" ({round(cf * 100)}% of notes in chords)" if cf is not None else "") + ". Keep it polyphonic.")
        if "strong movement" in mv:
            osc.append("Supersaw-style: unison 6-8 voices, detune 25-40%.")
    elif role == "arp":
        device = "Wavetable or Drift pluck + Arpeggiator (or program the notes)"
        osc.append("Fast repeating single notes: pluck envelope (fast attack, short decay).")
    else:
        device = "Wavetable / Analog lead"
        if "strong movement" in mv:
            osc.append("Supersaw-style: unison 6-8 voices, detune 25-40%.")

    if wave and kind not in ("vocals",) and role not in ("bass", "low"):
        osc.append(f"Timbre looks {wave.lower()}.")
    if tim.get("noisy"):
        osc.append("Noisy/breathy component: add a noise oscillator or use a sampled source.")
    if reach and kind not in ("vocals",):
        cut = reach * 1.1
        osc.append(f"Harmonics reach ~{_fmt_hz(reach)}: start a low-pass (12-24 dB) around {_fmt_hz(cut)}.")
    if ns.get("glide_fraction", 0) >= 0.15:
        osc.append(f"Pitch glides on {ns['glide_fraction'] * 100:.0f}% of notes: enable Glide/Portamento (legato).")
    if ns.get("mono") and role in ("lead", "bass", "low", "arp"):
        extra.append("Monophonic line: set the synth to mono / 1 voice (legato if notes overlap).")
    e = _env_line(env, "")
    if e and kind != "vocals":
        osc.append(e)

    fx = _fx_lines(f, is_bass=role in ("bass", "low"))
    if role in ("bass", "low"):
        lw = f.get("low_width_db")
        if lw is not None and lw > -20:
            fx.append("Low end is not mono: use Utility (Bass Mono below ~120 Hz) unless the width is intentional.")
        else:
            fx.append("Keep the sub mono (Utility > Bass Mono).")
    return {"device": device, "tips": osc + extra, "fx": fx}


def drum(piece: str, ch: dict, pattern_note: str = "") -> dict:
    tips, device = [], "Drum Rack"
    if piece == "kick":
        style = ch.get("style", "")
        if "808" in style:
            device = "Drum Rack: 808 kick (DS Kick or a long 808 sample, pitched)"
        elif "punchy" in style:
            device = "Drum Rack: 909-style kick (DS Kick / Kit-Core 909)"
        else:
            device = "Drum Rack: short, tight kick sample"
        if ch.get("pitch_note"):
            tips.append(f"Body pitch ~{ch['pitch_hz']:.0f} Hz ({ch['pitch_note']}): tune the kick to this.")
        if ch.get("start_hz") and ch.get("pitch_hz") and ch["start_hz"] > ch["pitch_hz"] * 1.6:
            tips.append(f"Pitch drops from ~{ch['start_hz']:.0f} Hz: pitch envelope for punch.")
        if ch.get("decay_ms"):
            tips.append(f"Decay ~{ch['decay_ms']:.0f} ms to -30 dB.")
        if ch.get("click", 0) > 0.08:
            tips.append("Clicky transient: layer a click or use a kick with a strong top.")
    elif piece == "snare":
        if ch.get("clap_like"):
            device = "Drum Rack: clap (or clap + snare layer)"
            tips.append("Several bursts per hit: a clap, or a clap layered with the snare.")
        else:
            device = "Drum Rack: snare (DS Snare / sample)"
        if ch.get("tone_hz"):
            tips.append(f"Body tone ~{ch['tone_hz']:.0f} Hz; noise share {ch.get('noise_ratio', 0) * 100:.0f}%.")
        if ch.get("decay_ms"):
            tips.append(f"Decay ~{ch['decay_ms']:.0f} ms (includes reverb).")
    elif piece == "hh":
        device = "Drum Rack: closed + open hats (DS HH / samples)"
        if ch.get("open_ratio") is not None:
            tips.append(f"{ch['open_ratio'] * 100:.0f}% of hits ring open: put open hats on A#1 and choke them with the closed hat.")
        if ch.get("decay_ms"):
            tips.append(f"Closed-hat decay ~{ch['decay_ms']:.0f} ms.")
    elif piece in ("ride", "crash"):
        device = f"Drum Rack: {piece} sample"
        if ch.get("decay_ms"):
            tips.append(f"Rings ~{ch['decay_ms']:.0f} ms.")
    elif piece == "toms":
        device = "Drum Rack: toms (DS Tom) or a percussion loop"
    if pattern_note:
        tips.append(pattern_note)
    return {"device": device, "tips": tips, "fx": []}
