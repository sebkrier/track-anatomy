// Arrangement view: ruler, sections, chords, FX events, and one lane per element.
import { h, $, fitCanvas, groupColor, hexToRgba, b64bytes, noteName, tooltip, esc, GROUPS, roundRect, cssVar } from "./util.js";

const TOP = [
  { id: "ruler", label: "Bar", h: 22 },
  { id: "sections", label: "Sections", h: 28 },
  { id: "chords", label: "Chords", h: 24 },
  { id: "fx", label: "FX / transitions", h: 20 },
];
const GROUP_H = 22;
const LANE_H = 42;
const FX_ICON = { riser: "↗", downlifter: "↘", impact: "✱", snare_roll: "≋", silence: "◌" };

export class Timeline {
  constructor(app) {
    this.app = app;
    this.ppb = 8;           // px per beat
    this.start = 0;         // beat at left edge
    this.hover = null;
    this.dragLoop = null;
    this.follow = true;
    this.peaks = new Map();
  }

  mount(root) {
    const A = this.app.A;
    this.A = A;
    this.grid = this.app.grid;
    this.totalBeats = A.global.n_bars * A.global.meter;
    for (const e of A.elements) this.peaks.set(e.id, b64bytes(e.peaks));
    this.mixPeaks = b64bytes(A.mix.peaks);

    this.topCv = h("canvas");
    this.topOv = h("canvas");
    this.laneCv = h("canvas");
    this.laneOv = h("canvas");
    this.ovCv = h("canvas");
    this.heads = h("div", { class: "lane-heads" });
    this.scroll = h("div", { class: "arr-scroll" },
      h("div", { class: "arr-body" }, this.heads, this.lanesWrap = h("div", { class: "lanes-canvas-wrap" }, this.laneCv, this.laneOv)));
    this.topWrap = h("div", { class: "arr-canvas-wrap", style: { height: `${TOP.reduce((a, r) => a + r.h, 0)}px` } }, this.topCv, this.topOv);
    this.root = h("div", { class: "arrangement" },
      h("div", {},
        h("div", { class: "arr-top" },
          h("div", { class: "arr-top-heads" }, TOP.map((r) => h("div", { style: { "--h": `${r.h}px` } }, r.label))),
          this.topWrap)),
      h("div", { style: { display: "grid", gridTemplateRows: "1fr auto", minHeight: 0 } },
        this.scroll,
        h("div", { class: "overview", style: { display: "grid", gridTemplateColumns: "var(--head-w, 208px) 1fr" } },
          h("div", { class: "muted", style: { fontSize: "11px", padding: "5px 12px", borderRight: "1px solid var(--border)" } }, "Overview"),
          this.ovWrap = h("div", { style: { position: "relative" } }, this.ovCv))));
    root.replaceChildren(this.root);
    this.buildRows();
    this.bind();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.root);
    requestAnimationFrame(() => { this.resize(); this.fit(); });
    this.loop();
  }

  destroy() { this.ro?.disconnect(); cancelAnimationFrame(this.raf); this.unsub?.(); this.dead = true; tooltip.hide(); }

  // ------------------------------------------------------------------ rows / heads
  buildRows() {
    const A = this.A, P = this.app.player;
    this.rows = [];
    this.heads.replaceChildren();
    let y = 0;
    const byGroup = {};
    for (const e of A.elements) (byGroup[e.group] ||= []).push(e);
    for (const g of Object.keys(GROUPS)) {
      const els = byGroup[g];
      if (!els) continue;
      const ids = els.map((e) => e.id);
      const color = groupColor(g);
      this.rows.push({ type: "group", group: g, y, h: GROUP_H, ids });
      const gm = h("button", { class: "ms m", title: `Mute all ${GROUPS[g].label}` }, "M");
      const gs = h("button", { class: "ms s", title: `Solo ${GROUPS[g].label}` }, "S");
      gm.onclick = (ev) => { ev.stopPropagation(); const on = !ids.every((i) => P.mutes.has(i)); P.toggleMute(ids, on); };
      gs.onclick = (ev) => { ev.stopPropagation(); const on = !ids.every((i) => P.solos.has(i)); P.toggleSolo(ids, on, !ev.shiftKey && on && !ev.ctrlKey); };
      const head = h("div", { class: "lane-head group", style: { height: `${GROUP_H}px` } },
        h("span", { class: "swatch", style: { background: color } }),
        h("span", { class: "nm" }, GROUPS[g].label), gm, gs);
      head._ms = { m: gm, s: gs, ids };
      this.heads.append(head);
      y += GROUP_H;
      for (const e of els) {
        const m = h("button", { class: "ms m", title: "Mute (M)" }, "M");
        const s = h("button", { class: "ms s", title: "Solo (S) · Shift+click to add to solo" }, "S");
        m.onclick = (ev) => { ev.stopPropagation(); P.toggleMute([e.id], !P.mutes.has(e.id)); };
        s.onclick = (ev) => { ev.stopPropagation(); const on = !P.solos.has(e.id); P.toggleSolo([e.id], on, on && !ev.shiftKey); };
        const n = e.notes?.length ?? e.hits?.length ?? 0;
        const sub = e.hits ? `${n} hits` : e.notes?.length ? `${n} notes` : "audio only";
        const head = h("div", { class: "lane-head", style: { height: `${LANE_H}px` }, title: e.name },
          h("span", { class: "bar", style: { background: color } }),
          h("span", { class: "nm" }, e.name, h("small", {}, sub)), m, s);
        head._ms = { m, s, ids: [e.id], audio: e.audio };
        head.onclick = () => this.app.select(e.id);
        this.heads.append(head);
        this.rows.push({ type: "lane", e, y, h: LANE_H, color });
        y += LANE_H;
      }
    }
    this.lanesH = y;
    this.syncHeads();
  }

  syncHeads() {
    const P = this.app.player;
    for (const head of this.heads.children) {
      const ms = head._ms;
      if (!ms) continue;
      const allM = ms.ids.every((i) => P.mutes.has(i));
      const allS = ms.ids.every((i) => P.solos.has(i));
      ms.m.classList.toggle("on", allM);
      ms.s.classList.toggle("on", allS);
      const loading = ms.audio && P.isLoading(ms.audio);
      ms.s.classList.toggle("loading", !!loading);
      ms.m.classList.toggle("loading", !!loading);
      head.classList.toggle("sel", ms.ids.length === 1 && this.app.sel === ms.ids[0]);
    }
  }

  // ------------------------------------------------------------------ geometry
  resize() {
    if (this.dead) return;
    const w = this.topWrap.clientWidth;
    if (!w) return;
    this.W = w;
    this.topH = TOP.reduce((a, r) => a + r.h, 0);
    this.topCtx = fitCanvas(this.topCv, w, this.topH);
    this.topOvCtx = fitCanvas(this.topOv, w, this.topH);
    this.lanesWrap.style.height = `${this.lanesH}px`;
    this.laneCtx = fitCanvas(this.laneCv, w, this.lanesH);
    this.laneOvCtx = fitCanvas(this.laneOv, w, this.lanesH);
    this.ovW = this.ovWrap.clientWidth;
    this.ovCtx = fitCanvas(this.ovCv, this.ovW, 25);
    this.clampView();
    this.draw();
  }

  fit() {
    this.ppb = this.W / (this.totalBeats + 2);
    this.start = -1;
    this.draw();
  }
  zoom(factor, anchorX = this.W / 2) {
    const b = this.xToBeat(anchorX);
    const minPpb = this.W / (this.totalBeats + 8);
    this.ppb = Math.min(160, Math.max(minPpb, this.ppb * factor));
    this.start = b - anchorX / this.ppb;
    this.clampView();
    this.draw();
  }
  clampView() {
    const visible = this.W / this.ppb;
    const max = this.totalBeats + 4 - visible;
    this.start = Math.max(-4, Math.min(this.start, Math.max(-4, max)));
  }
  x(beat) { return (beat - this.start) * this.ppb; }
  xToBeat(x) { return this.start + x / this.ppb; }
  get endBeat() { return this.start + this.W / this.ppb; }
  timeX(t) { return this.x(this.grid.beat(t)); }

  // ------------------------------------------------------------------ drawing
  draw() {
    if (!this.topCtx) return;
    this.drawTop();
    this.drawLanes();
    this.drawOverview();
  }

  barStep() {
    const pxBar = this.ppb * this.grid.meter;
    for (const s of [1, 2, 4, 8, 16, 32]) if (pxBar * s >= 34) return s;
    return 64;
  }

  drawTop() {
    const c = this.topCtx, W = this.W, A = this.A, m = this.grid.meter;
    c.clearRect(0, 0, W, this.topH);
    c.fillStyle = cssVar("--surface");
    c.fillRect(0, 0, W, this.topH);
    let y = 0;
    // ruler
    const step = this.barStep();
    c.font = "11px system-ui, sans-serif";
    c.textBaseline = "middle";
    const b0 = Math.floor(this.start / m), b1 = Math.ceil(this.endBeat / m);
    for (let bar = Math.max(0, b0); bar <= b1; bar++) {
      const x = Math.round(this.x(bar * m)) + 0.5;
      if (bar % step === 0) {
        c.fillStyle = cssVar("--axis");
        c.fillRect(x, 10, 1, 12);
        c.fillStyle = cssVar("--text-2");
        c.fillText(String(bar + 1), x + 4, 12);
      } else if (this.ppb * m >= 6) {
        c.fillStyle = cssVar("--grid");
        c.fillRect(x, 16, 1, 6);
      }
    }
    y += TOP[0].h;
    // sections
    const secH = TOP[1].h;
    A.sections.forEach((s, i) => {
      const x0 = this.x(s.b0 * m), x1 = this.x(s.b1 * m);
      if (x1 < 0 || x0 > W) return;
      const hov = this.hover?.kind === "section" && this.hover.i === i;
      const shade = 0.10 + 0.22 * (s.energy ?? 0.5);
      c.fillStyle = `rgba(195, 194, 183, ${hov ? shade + 0.1 : shade})`;
      roundRect(c, x0 + 1, y + 3, Math.max(1, x1 - x0 - 2), secH - 6, 4);
      c.fill();
      // energy bar along the bottom
      c.fillStyle = "rgba(255,255,255,0.55)";
      c.fillRect(x0 + 3, y + secH - 6, Math.max(0, (x1 - x0 - 6) * (s.energy ?? 0)), 2);
      c.save();
      c.beginPath();
      c.rect(x0 + 2, y, Math.max(0, x1 - x0 - 4), secH);
      c.clip();
      c.fillStyle = cssVar("--text");
      c.font = "600 12px system-ui, sans-serif";
      c.fillText(s.label, x0 + 7, y + secH / 2 - 1);
      const lw = c.measureText(s.label).width;
      c.font = "11px system-ui, sans-serif";
      c.fillStyle = cssVar("--text-2");
      c.fillText(`${s.bars} bars`, x0 + 13 + lw, y + secH / 2 - 1);
      c.restore();
    });
    y += secH;
    // chords
    const chH = TOP[2].h;
    c.font = "12px system-ui, sans-serif";
    for (const ch of A.chords) {
      const x0 = this.x(ch.b0), x1 = this.x(ch.b1);
      if (x1 < 0 || x0 > W || ch.root == null) continue;
      c.fillStyle = cssVar("--surface-3");
      roundRect(c, x0 + 1, y + 3, Math.max(1, x1 - x0 - 2), chH - 6, 3);
      c.fill();
      if (x1 - x0 > 22) {
        c.save();
        c.beginPath(); c.rect(x0 + 2, y, x1 - x0 - 4, chH); c.clip();
        c.fillStyle = cssVar("--text");
        c.fillText(ch.symbol, x0 + 6, y + chH / 2);
        c.restore();
      }
    }
    y += chH;
    // fx events
    const fxH = TOP[3].h;
    c.font = "12px system-ui, sans-serif";
    for (const ev of A.fx_events) {
      const x0 = this.x(ev.bar0 * m), x1 = this.x((ev.bar1 === ev.bar0 ? ev.bar0 + 0.25 : ev.bar1) * m);
      if (x1 < 0 || x0 > W) continue;
      c.fillStyle = hexToRgba(cssVar("--g-fx"), 0.35);
      if (ev.bar1 > ev.bar0) {
        // ramp shape for risers, flat for others
        c.beginPath();
        if (ev.type === "riser" || ev.type === "snare_roll") {
          c.moveTo(x0, y + fxH - 3); c.lineTo(x1, y + 3); c.lineTo(x1, y + fxH - 3);
        } else if (ev.type === "downlifter") {
          c.moveTo(x0, y + 3); c.lineTo(x1, y + fxH - 3); c.lineTo(x0, y + fxH - 3);
        } else {
          c.rect(x0, y + 4, x1 - x0, fxH - 8);
        }
        c.closePath(); c.fill();
      }
      c.fillStyle = cssVar("--text-2");
      c.fillText(FX_ICON[ev.type] || "•", (ev.bar1 > ev.bar0 ? x1 : x0) - 4, y + fxH / 2);
    }
    // bottom axis line
    c.fillStyle = cssVar("--axis");
    c.fillRect(0, this.topH - 1, W, 1);
  }

  drawLanes() {
    const c = this.laneCtx, W = this.W, A = this.A, m = this.grid.meter;
    c.clearRect(0, 0, W, this.lanesH);
    c.fillStyle = cssVar("--surface");
    c.fillRect(0, 0, W, this.lanesH);
    // section alternation + bar lines
    A.sections.forEach((s, i) => {
      if (i % 2) {
        c.fillStyle = "rgba(255,255,255,0.018)";
        c.fillRect(this.x(s.b0 * m), 0, (s.b1 - s.b0) * m * this.ppb, this.lanesH);
      }
    });
    const step = this.barStep();
    const b0 = Math.max(0, Math.floor(this.start / m)), b1 = Math.ceil(this.endBeat / m);
    for (let bar = b0; bar <= b1; bar++) {
      if (bar % step) continue;
      c.fillStyle = cssVar("--grid");
      c.fillRect(Math.round(this.x(bar * m)), 0, 1, this.lanesH);
    }
    c.fillStyle = cssVar("--axis");
    for (const s of A.sections) c.fillRect(Math.round(this.x(s.b0 * m)), 0, 1, this.lanesH);

    for (const r of this.rows) {
      if (r.type === "group") {
        c.fillStyle = cssVar("--surface-2");
        c.fillRect(0, r.y, W, r.h);
        c.fillStyle = cssVar("--grid");
        c.fillRect(0, r.y + r.h - 1, W, 1);
        continue;
      }
      this.drawLane(c, r);
    }
  }

  drawLane(c, r) {
    const e = r.e, W = this.W, m = this.grid.meter, y = r.y, H = r.h;
    const sel = this.app.sel === e.id;
    if (sel) { c.fillStyle = "rgba(57,135,229,0.07)"; c.fillRect(0, y, W, H); }
    // inactive bars
    c.fillStyle = "rgba(0,0,0,0.28)";
    e.activity.forEach((a, bar) => {
      if (a >= 0.15) return;
      const x0 = this.x(bar * m), x1 = this.x((bar + 1) * m);
      if (x1 < 0 || x0 > W) return;
      c.fillRect(x0, y, x1 - x0, H - 1);
    });
    // waveform
    const pk = this.peaks.get(e.id);
    const per = 50;
    const mid = y + H / 2;
    c.fillStyle = hexToRgba(r.color, e.notes?.length || e.hits?.length ? 0.22 : 0.55);
    for (let x = 0; x < W; x++) {
      const t0 = this.grid.time(this.xToBeat(x)), t1 = this.grid.time(this.xToBeat(x + 1));
      let i0 = Math.max(0, Math.floor(t0 * per)), i1 = Math.min(pk.length, Math.ceil(t1 * per));
      if (i1 <= i0) i1 = i0 + 1;
      let v = 0;
      for (let i = i0; i < i1 && i < pk.length; i++) if (pk[i] > v) v = pk[i];
      if (!v) continue;
      const hh = (v / 255) * (H - 6) / 2;
      c.fillRect(x, mid - hh, 1, hh * 2);
    }
    // notes
    if (e.notes?.length) {
      if (e._lo == null) {
        let lo = 127, hi = 0;
        for (const n of e.notes) { if (n[2] < lo) lo = n[2]; if (n[2] > hi) hi = n[2]; }
        e._lo = lo; e._hi = Math.max(hi, lo + 11);
      }
      const span = e._hi - e._lo + 1;
      const nh = Math.max(1.5, Math.min(5, (H - 8) / span));
      c.fillStyle = r.color;
      for (const n of e.notes) {
        if (n[1] < this.start || n[0] > this.endBeat) continue;
        const x0 = this.x(n[0]), w = Math.max(1.5, (n[1] - n[0]) * this.ppb - 0.5);
        const yy = y + H - 4 - ((n[2] - e._lo + 1) / span) * (H - 8);
        c.globalAlpha = 0.45 + 0.55 * (n[3] / 127);
        c.fillRect(x0, yy, w, nh);
      }
      c.globalAlpha = 1;
    }
    if (e.hits?.length) {
      c.fillStyle = r.color;
      const tw = Math.max(1.5, Math.min(4, this.ppb * 0.12));
      for (const hit of e.hits) {
        if (hit[0] < this.start - 1 || hit[0] > this.endBeat) continue;
        const x0 = this.x(hit[0]);
        const hh = 6 + (hit[1] / 127) * (H - 12);
        const open = hit[2] === 46;
        c.globalAlpha = open ? 0.95 : 0.85;
        c.fillRect(x0, y + H - 3 - hh, tw, hh);
        if (open) { c.beginPath(); c.arc(x0 + tw / 2, y + H - 5 - hh, 2.2, 0, 7); c.fill(); }
      }
      c.globalAlpha = 1;
    }
    c.fillStyle = cssVar("--grid");
    c.fillRect(0, y + H - 1, W, 1);
  }

  drawOverview() {
    const c = this.ovCtx, W = this.ovW, H = 25, A = this.A, m = this.grid.meter;
    if (!c) return;
    c.clearRect(0, 0, W, H);
    const tb = this.totalBeats;
    const bx = (b) => (b / tb) * W;
    A.sections.forEach((s, i) => {
      c.fillStyle = `rgba(195,194,183,${0.06 + 0.16 * (s.energy ?? 0.5)})`;
      c.fillRect(bx(s.b0 * m), 0, bx((s.b1 - s.b0) * m), H);
    });
    const pk = this.mixPeaks;
    c.fillStyle = "rgba(195,194,183,0.55)";
    for (let x = 0; x < W; x++) {
      const t0 = this.grid.time((x / W) * tb), t1 = this.grid.time(((x + 1) / W) * tb);
      let v = 0;
      for (let i = Math.floor(t0 * 50); i < Math.ceil(t1 * 50) && i < pk.length; i++) if (i >= 0 && pk[i] > v) v = pk[i];
      const hh = (v / 255) * (H - 6) / 2;
      c.fillRect(x, H / 2 - hh, 1, hh * 2);
    }
    // viewport
    const v0 = bx(Math.max(0, this.start)), v1 = bx(Math.min(tb, this.endBeat));
    c.strokeStyle = cssVar("--accent");
    c.lineWidth = 1.5;
    c.strokeRect(v0 + 0.75, 0.75, Math.max(4, v1 - v0 - 1.5), H - 1.5);
  }

  // overlay: playhead, loop, hover; every frame
  loop() {
    if (this.dead) return;
    this.raf = requestAnimationFrame(() => this.loop());
    const P = this.app.player;
    const t = P.position();
    const beat = this.grid.beat(t);
    if (P.playing && this.follow && this.W) {
      const x = this.x(beat);
      if (x > this.W * 0.88 || x < 0) {
        this.start = beat - (this.W * 0.1) / this.ppb;
        this.clampView();
        this.draw();
      }
    }
    const key = `${t.toFixed(3)}|${this.start}|${this.ppb}|${JSON.stringify(P.loop)}|${JSON.stringify(this.dragLoop)}|${this.hoverX}`;
    if (key === this._lastKey) return;
    this._lastKey = key;
    this.drawOverlay(beat);
    this.app.onTick?.(t, beat);
  }

  drawOverlay(beat) {
    const P = this.app.player;
    for (const [c, H, isTop] of [[this.topOvCtx, this.topH, true], [this.laneOvCtx, this.lanesH, false]]) {
      if (!c) continue;
      c.clearRect(0, 0, this.W, H);
      const lp = this.dragLoop || (P.loop && { b0: this.grid.beat(P.loop.t0), b1: this.grid.beat(P.loop.t1) });
      if (lp) {
        const x0 = this.x(Math.min(lp.b0, lp.b1)), x1 = this.x(Math.max(lp.b0, lp.b1));
        c.fillStyle = "rgba(57,135,229,0.10)";
        c.fillRect(x0, 0, x1 - x0, H);
        if (isTop) {
          c.fillStyle = "rgba(57,135,229,0.85)";
          c.fillRect(x0, 0, x1 - x0, 4);
        }
        c.fillStyle = "rgba(57,135,229,0.6)";
        c.fillRect(Math.round(x0), 0, 1, H);
        c.fillRect(Math.round(x1), 0, 1, H);
      }
      if (this.hoverX != null) {
        c.fillStyle = "rgba(255,255,255,0.18)";
        c.fillRect(Math.round(this.hoverX), 0, 1, H);
      }
      const x = Math.round(this.x(beat)) + 0.5;
      if (x >= 0 && x <= this.W) {
        c.fillStyle = "#ffffff";
        c.fillRect(x - 0.5, 0, 1.5, H);
        if (isTop) {
          c.beginPath(); c.moveTo(x - 5, 0); c.lineTo(x + 5, 0); c.lineTo(x, 7); c.closePath(); c.fill();
        }
      }
    }
    // overview playhead
    const c = this.ovCtx;
    if (c) {
      this.drawOverview();
      const px = (beat / this.totalBeats) * this.ovW;
      c.fillStyle = "#fff";
      c.fillRect(px, 0, 1.5, 25);
    }
  }

  // ------------------------------------------------------------------ interaction
  bind() {
    const P = this.app.player;
    this.unsub = P.on(() => this.syncHeads());

    const topRow = (y) => { let acc = 0; for (const r of TOP) { if (y < acc + r.h) return r.id; acc += r.h; } return null; };
    const snapBar = (b) => Math.round(b / this.grid.meter) * this.grid.meter;

    this.topOv.addEventListener("mousedown", (ev) => {
      const { x, y } = this.local(ev, this.topOv);
      const row = topRow(y);
      const beat = this.xToBeat(x);
      if (row === "ruler") {
        this.dragLoop = { b0: snapBar(beat), b1: snapBar(beat), x0: x };
        const move = (e2) => { const p = this.local(e2, this.topOv); this.dragLoop.b1 = e2.shiftKey ? Math.round(this.xToBeat(p.x)) : snapBar(this.xToBeat(p.x)); };
        const up = (e2) => {
          removeEventListener("mousemove", move); removeEventListener("mouseup", up);
          const d = this.dragLoop; this.dragLoop = null;
          const p = this.local(e2, this.topOv);
          if (Math.abs(p.x - d.x0) < 4) { P.seek(Math.max(0, this.grid.time(beat))); return; }
          const a = Math.min(d.b0, d.b1), b = Math.max(d.b0, d.b1);
          if (b > a) { P.setLoop({ t0: Math.max(0, this.grid.time(a)), t1: this.grid.time(b) }); this.app.onLoopChange?.(); }
        };
        addEventListener("mousemove", move); addEventListener("mouseup", up);
      } else if (row === "sections") {
        const i = this.A.sections.findIndex((s) => beat >= s.b0 * this.grid.meter && beat < s.b1 * this.grid.meter);
        if (i >= 0) this.app.selectSection(i, ev.detail >= 2);
      } else if (row === "chords") {
        const ch = this.A.chords.find((c) => beat >= c.b0 && beat < c.b1);
        P.seek(Math.max(0, this.grid.time(ch ? ch.b0 : beat)));
      } else {
        P.seek(Math.max(0, this.grid.time(beat)));
      }
    });

    this.laneOv.addEventListener("mousedown", (ev) => {
      const { x, y } = this.local(ev, this.laneOv);
      const r = this.rows.find((r) => y >= r.y && y < r.y + r.h);
      if (r?.type === "lane") this.app.select(r.e.id, true);
      P.seek(Math.max(0, this.grid.time(this.xToBeat(x))));
    });

    const hoverTop = (ev) => {
      const { x, y } = this.local(ev, this.topOv);
      this.hoverX = x;
      const beat = this.xToBeat(x), m = this.grid.meter;
      const row = topRow(y);
      let html = `<div class="tt-t">${this.grid.barBeatLabel(beat).trim()}</div>`;
      this.hover = null;
      if (row === "sections") {
        const i = this.A.sections.findIndex((s) => beat >= s.b0 * m && beat < s.b1 * m);
        if (i >= 0) {
          const s = this.A.sections[i];
          this.hover = { kind: "section", i };
          const names = s.elements.map((id) => this.A.elements.find((e) => e.id === id)?.name).filter(Boolean);
          html = `<div class="tt-t">${esc(s.label)} · bars ${s.b0 + 1}–${s.b1}</div><div class="tt-s">${esc(names.join(", ") || "—")}</div><div class="tt-s">Click to loop · double-click to play</div>`;
        }
      } else if (row === "chords") {
        const ch = this.A.chords.find((c) => beat >= c.b0 && beat < c.b1);
        if (ch && ch.root != null) html = `<div class="tt-t">${esc(ch.symbol)} <span class="tt-s">${esc(ch.roman)}</span></div><div class="tt-s">${esc(ch.tones.join(" – "))} · ${(ch.b1 - ch.b0)} beats</div>`;
      } else if (row === "fx") {
        const ev2 = this.A.fx_events.find((e) => beat >= e.bar0 * m - 1 && beat <= (Math.max(e.bar1, e.bar0 + 0.3)) * m + 1);
        if (ev2) html = `<div class="tt-t">${esc(ev2.label)}</div><div class="tt-s">${ev2.bar0 === ev2.bar1 ? `bar ${ev2.bar0 + 1}` : `bars ${ev2.bar0 + 1}–${ev2.bar1}`}${ev2.detail ? " · " + esc(ev2.detail) : ""}</div>`;
      } else if (row === "ruler") {
        html += `<div class="tt-s">Click to jump · drag to set a loop</div>`;
      }
      tooltip.show(ev.clientX, ev.clientY, html);
      this.drawTop();
    };
    this.topOv.addEventListener("mousemove", hoverTop);
    this.topOv.addEventListener("mouseleave", () => { this.hoverX = null; this.hover = null; tooltip.hide(); this.drawTop(); });

    this.laneOv.addEventListener("mousemove", (ev) => {
      const { x, y } = this.local(ev, this.laneOv);
      this.hoverX = x;
      const beat = this.xToBeat(x);
      const r = this.rows.find((r) => y >= r.y && y < r.y + r.h);
      if (!r || r.type !== "lane") { tooltip.hide(); return; }
      const e = r.e;
      let extra = "";
      if (e.notes?.length) {
        const here = e.notes.filter((n) => beat >= n[0] && beat < n[1]).map((n) => noteName(n[2]));
        if (here.length) extra = `<div class="tt-s">${esc([...new Set(here)].join(" "))}</div>`;
      } else if (e.hits?.length) {
        const near = e.hits.find((hh) => Math.abs(hh[0] - beat) < 6 / this.ppb);
        if (near) extra = `<div class="tt-s">hit · velocity ${near[1]}</div>`;
      }
      const bar = Math.floor(beat / this.grid.meter);
      const act = e.activity[bar];
      tooltip.show(ev.clientX, ev.clientY, `<div class="tt-t">${esc(e.name)}</div><div class="tt-s">${this.grid.barBeatLabel(beat).trim()}${act != null ? ` · ${act >= 0.15 ? "playing" : "silent"}` : ""}</div>${extra}`);
    });
    this.laneOv.addEventListener("mouseleave", () => { this.hoverX = null; tooltip.hide(); });

    const wheel = (ev, isTop) => {
      if (ev.ctrlKey || ev.metaKey) {
        ev.preventDefault();
        const { x } = this.local(ev, isTop ? this.topOv : this.laneOv);
        this.zoom(Math.exp(-ev.deltaY * 0.0022), x);
        return;
      }
      const dx = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : (ev.shiftKey || isTop ? ev.deltaY : 0);
      if (dx) {
        ev.preventDefault();
        this.start += dx / this.ppb;
        this.clampView();
        this.draw();
      }
    };
    this.topOv.addEventListener("wheel", (e) => wheel(e, true), { passive: false });
    this.laneOv.addEventListener("wheel", (e) => wheel(e, false), { passive: false });

    const ovDrag = (ev) => {
      const r = this.ovCv.getBoundingClientRect();
      const go = (e2) => {
        const b = ((e2.clientX - r.left) / r.width) * this.totalBeats;
        this.start = b - (this.W / this.ppb) / 2;
        this.clampView();
        this.draw();
      };
      go(ev);
      const up = () => { removeEventListener("mousemove", go); removeEventListener("mouseup", up); };
      addEventListener("mousemove", go); addEventListener("mouseup", up);
    };
    this.ovCv.addEventListener("mousedown", ovDrag);
  }

  local(ev, el) {
    const r = el.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  showRange(b0, b1) {
    const span = b1 - b0;
    this.ppb = Math.max(this.W / (this.totalBeats + 8), Math.min(160, this.W / (span * 1.1)));
    this.start = b0 - span * 0.05;
    this.clampView();
    this.draw();
  }
}
