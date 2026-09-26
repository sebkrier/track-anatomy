// Audio engine. Everything plays from AudioBuffers started on the same clock, so stems stay
// sample-locked. Mute is done as "mix minus stem" (the stem phase-inverted against the mix),
// so only the stems you touch ever need to be downloaded and decoded.

export class Player {
  constructor() {
    this.ctx = null;
    this.buffers = new Map();     // path -> AudioBuffer
    this.pending = new Map();     // path -> Promise
    this.sources = [];
    this.playing = false;
    this.offset = 0;              // song time at `when`
    this.when = 0;
    this.loop = null;             // {t0, t1}
    this.mutes = new Set();
    this.solos = new Set();
    this.volume = 0.85;
    this.click = false;
    this.listeners = new Set();
    this._clickTimer = null;
    this._clickBus = null;         // metronome blips go through this; dropping it silences scheduled ones
    this._scheduledUntil = 0;
    this._startSeq = 0;
    this.onError = null;           // called when audio for playback fails to load
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit() { for (const fn of this.listeners) fn(this); }

  attach(A, trackId, grid) {
    this.stop();
    this.A = A;
    this.id = trackId;
    this.grid = grid;
    this.buffers.clear();
    this.pending.clear();
    this.mutes.clear();
    this.solos.clear();
    this.loop = null;
    this.offset = 0;
    this.duration = A.track.duration;
    this.elements = new Map(A.elements.map((e) => [e.id, e]));
    this.drumIds = A.elements.filter((e) => e.group === "drums").map((e) => e.id);
    // decoded audio is float32 at the context rate: for long tracks decode at 24 kHz to halve memory
    const rate = this.duration > 600 ? 24000 : undefined;
    if (this.ctx && rate !== this._rate) { this.ctx.close(); this.ctx = null; }
    this._rate = rate;
    if (!this.ctx) this.ctx = new AudioContext(rate ? { latencyHint: "interactive", sampleRate: rate } : { latencyHint: "interactive" });
    this.master = this.ctx.createGain();
    this.master.connect(this.ctx.destination);
    this._applyVolume();
    return this.ensure(A.mix.audio);
  }

  _applyVolume() {
    if (!this.master) return;
    // stems are stored with headroom (gain < 1); undo it so playback matches the original level
    this.master.gain.value = this.volume / (this.A?.track.gain || 1);
  }
  setVolume(v) {
    this.volume = v;
    this._applyVolume();
    if (this._clickBus) this._clickBus.gain.value = Math.min(1, v * 1.2);
  }

  url(path) {
    const [sub, name] = path.split("/");
    return `/api/tracks/${this.id}/file/${sub}/${encodeURIComponent(name)}`;
  }

  isLoading(path) { return this.pending.has(path) && !this.buffers.has(path); }

  ensure(path) {
    if (this.buffers.has(path)) return Promise.resolve(this.buffers.get(path));
    if (this.pending.has(path)) return this.pending.get(path);
    const id = this.id;
    const p = fetch(this.url(path))
      .then((r) => { if (!r.ok) throw new Error(`${r.status} loading ${path}`); return r.arrayBuffer(); })
      .then((ab) => this.ctx.decodeAudioData(ab))
      .then((buf) => { if (this.id === id) this.buffers.set(path, buf); this.emit(); return buf; })
      .catch((e) => { this.pending.delete(path); this.emit(); throw e; });
    this.pending.set(path, p);
    this.emit();
    return p;
  }

  /** Which buffers to play, with sign: [[path, +1|-1], ...] */
  plan() {
    const pathOf = (id) => (id === "__drums" ? this.A.drums_stem : this.elements.get(id)?.audio);
    const collapse = (set) => {
      // all drum pieces selected -> use the whole drum stem (cleaner than the sum of pieces)
      const ids = [...set];
      if (this.drumIds.length && this.drumIds.every((d) => set.has(d))) {
        return ["__drums", ...ids.filter((i) => !this.drumIds.includes(i))];
      }
      return ids;
    };
    if (this.solos.size) {
      const on = new Set([...this.solos].filter((i) => !this.mutes.has(i)));
      return collapse(on).map((i) => [pathOf(i), 1]).filter(([p]) => p);
    }
    const out = [[this.A.mix.audio, 1]];
    for (const i of collapse(this.mutes)) {
      const p = pathOf(i);
      if (p) out.push([p, -1]);
    }
    return out;
  }

  async _start(at) {
    const seq = ++this._startSeq;          // a newer start (seek, solo, loop) supersedes this one
    const plan = this.plan();
    try {
      await Promise.all(plan.map(([p]) => this.ensure(p)));
    } catch (e) {
      if (seq !== this._startSeq) return;
      this._killSources();
      this._resetClicks();
      this.playing = false;
      this.offset = at;
      this.emit();
      this.onError?.(e);
      return;
    }
    if (!this.playing || seq !== this._startSeq) return;
    this._killSources();
    this._resetClicks();
    const ctx = this.ctx;
    if (ctx.state === "suspended") await ctx.resume();
    if (!this.playing || seq !== this._startSeq) return;
    if (this.loop && (at < this.loop.t0 || at >= this.loop.t1)) at = this.loop.t0;
    at = Math.max(0, Math.min(at, this.duration - 0.01));
    const when = ctx.currentTime + 0.04;
    for (const [path, sign] of plan) {
      const src = ctx.createBufferSource();
      src.buffer = this.buffers.get(path);
      const g = ctx.createGain();
      g.gain.value = sign;
      src.connect(g).connect(this.master);
      if (this.loop) {
        src.loop = true;
        src.loopStart = this.loop.t0;
        src.loopEnd = this.loop.t1;
      }
      src.start(when, at);
      this.sources.push(src);
    }
    if (!this.loop && this.sources[0]) {
      const first = this.sources[0];
      first.onended = () => { if (this.sources[0] === first && this.playing) { this.playing = false; this.offset = 0; this.emit(); } };
    }
    this.offset = at;
    this.when = when;
    this._scheduledUntil = when;
    this.emit();
  }

  _killSources() {
    for (const s of this.sources) { try { s.onended = null; s.stop(); } catch { } }
    this.sources = [];
  }

  position() {
    if (!this.playing || !this.ctx) return this.offset;
    let t = this.offset + Math.max(0, this.ctx.currentTime - this.when);
    if (this.loop && t >= this.loop.t1) {
      const L = this.loop.t1 - this.loop.t0;
      t = this.loop.t0 + ((t - this.loop.t0) % L);
    }
    return Math.min(t, this.duration);
  }

  play(at = null) {
    if (at == null) at = this.offset;
    this.playing = true;
    this._startClock();
    return this._start(at);
  }
  pause() {
    this.offset = this.position();
    this.playing = false;
    this._startSeq++;
    this._killSources();
    this._stopClock();
    this.emit();
  }
  toggle() { this.playing ? this.pause() : this.play(); }
  stop() { this._startSeq++; this._killSources(); this._stopClock(); this.playing = false; this.offset = 0; this.emit(); }
  seek(t) {
    t = Math.max(0, Math.min(t, (this.duration || 0) - 0.01));
    if (this.playing) this._start(t); else { this.offset = t; this.emit(); }
  }
  restartIfPlaying() { if (this.playing) this._start(this.position()); else this.emit(); }

  setLoop(loop) {
    const pos = this.position();
    if (loop) {
      const d = this.duration || 0;
      loop = { t0: Math.max(0, Math.min(loop.t0, d)), t1: Math.max(0, Math.min(loop.t1, d)) };
    }
    this.loop = loop && loop.t1 - loop.t0 > 0.05 ? loop : null;
    if (this.playing) this._start(this.loop && (pos < this.loop.t0 || pos >= this.loop.t1) ? this.loop.t0 : pos);
    else this.emit();
  }

  toggleMute(ids, on) {
    for (const id of ids) on ? this.mutes.add(id) : this.mutes.delete(id);
    this._prefetch();
    this.restartIfPlaying();
  }
  toggleSolo(ids, on, exclusive = false) {
    if (exclusive) this.solos.clear();
    for (const id of ids) on ? this.solos.add(id) : this.solos.delete(id);
    this._prefetch();
    this.restartIfPlaying();
  }
  _prefetch() { for (const [p] of this.plan()) this.ensure(p).catch(() => { }); }

  // --- audition: a soft synth voice to hear a note or chord from the transcription
  audition(pitches, dur = 0.9, gain = 0.18) {
    if (!this.ctx) this.ctx = new AudioContext({ latencyHint: "interactive" });
    const ctx = this.ctx;
    if (ctx.state === "suspended") ctx.resume();
    const at = ctx.currentTime + 0.01;
    const out = ctx.createGain();
    out.gain.value = gain / Math.max(1, Math.sqrt(pitches.length));
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(3200, at);
    lp.frequency.exponentialRampToValueAtTime(900, at + dur);
    lp.connect(out).connect(ctx.destination);
    for (const p of pitches) {
      const f = 440 * 2 ** ((p - 69) / 12);
      for (const [type, det, lvl] of [["sawtooth", -6, 0.5], ["sawtooth", 6, 0.5], ["triangle", 0, 0.6]]) {
        const o = ctx.createOscillator();
        o.type = type;
        o.frequency.value = f;
        o.detune.value = det;
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, at);
        g.gain.linearRampToValueAtTime(lvl, at + 0.012);
        g.gain.setTargetAtTime(lvl * 0.6, at + 0.05, 0.15);
        g.gain.setTargetAtTime(0, at + dur * 0.8, 0.08);
        o.connect(g).connect(lp);
        o.start(at);
        o.stop(at + dur + 0.5);
      }
    }
  }

  // --- metronome on the detected grid (lets you check the grid by ear)
  setClick(on) { this.click = on; this._resetClicks(); }
  _startClock() {
    if (this._clickTimer) return;
    this._clickTimer = setInterval(() => this._scheduleClicks(), 30);
  }
  _stopClock() {
    clearInterval(this._clickTimer);
    this._clickTimer = null;
    this._resetClicks();
  }
  /** Silence blips already scheduled ahead (after a seek, pause or loop change) and start over. */
  _resetClicks() {
    if (this._clickBus) { try { this._clickBus.disconnect(); } catch { } }
    this._clickBus = null;
    this._scheduledUntil = this.ctx ? this.ctx.currentTime : 0;
  }
  _scheduleClicks() {
    if (!this.playing || !this.click || !this.grid || !this.sources.length) return;
    const ctx = this.ctx;
    if (!this._clickBus) {
      this._clickBus = ctx.createGain();
      this._clickBus.gain.value = Math.min(1, this.volume * 1.2);
      this._clickBus.connect(ctx.destination);
    }
    const horizon = ctx.currentTime + 0.12;
    let from = Math.max(this._scheduledUntil, ctx.currentTime, this.when);
    if (from >= horizon) return;
    const songAt = (ct) => {
      let t = this.offset + (ct - this.when);
      if (this.loop && t >= this.loop.t1) {
        const L = this.loop.t1 - this.loop.t0;
        t = this.loop.t0 + ((t - this.loop.t0) % L);
      }
      return t;
    };
    const s0 = songAt(from), s1 = songAt(horizon);
    const windows = s1 >= s0 ? [[s0, s1, from - s0]] :
      [[s0, this.loop.t1, from - s0], [this.loop.t0, s1, horizon - s1]];
    for (const [a, b, off] of windows) {
      let beat = Math.ceil(this.grid.beat(a) - 1e-6);
      for (; ; beat++) {
        const t = this.grid.time(beat);
        if (t >= b) break;
        if (t < a) continue;
        this._blip(t + off, beat % this.grid.meter === 0);
      }
    }
    this._scheduledUntil = horizon;
  }
  _blip(at, accent) {
    const ctx = this.ctx;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.frequency.value = accent ? 1760 : 1175;
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(accent ? 0.35 : 0.22, at + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, at + 0.05);
    o.connect(g).connect(this._clickBus);
    o.start(at);
    o.stop(at + 0.06);
  }
}
