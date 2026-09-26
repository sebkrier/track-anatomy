// Piano roll for one element; follows the arrangement's horizontal view.
import { h, fitCanvas, noteName, isBlack, tooltip, esc, cssVar, hexToRgba } from "./util.js";

const KEY_W = 46;
const CHORD_H = 20;

export class PianoRoll {
  constructor(app, element, color) {
    this.app = app;
    this.e = element;
    this.color = color;
    // other layers split from the same stem, drawn as ghosts so an imperfect split hides nothing
    this.ghosts = app.A.elements.filter((x) => x !== element && x.kind === element.kind && x.notes?.length && element.kind !== "bass" && element.kind !== "vocals");
    this.showGhosts = true;
    const ps = element.notes.map((n) => n[2]);
    this.lo = Math.max(0, Math.min(...ps) - 2);
    this.hi = Math.min(127, Math.max(...ps) + 2);
    if (this.hi - this.lo < 14) { const mid = (this.lo + this.hi) / 2; this.lo = Math.round(mid - 7); this.hi = this.lo + 14; }
    this.scrollP = null;
    const key = app.A.global.key;
    const steps = key.mode === "major" ? [0, 2, 4, 5, 7, 9, 11] : [0, 2, 3, 5, 7, 8, 10];
    this.scalePcs = new Set(steps.map((s) => (key.tonic_pc + s) % 12));
    this.tonic = key.tonic_pc;
  }

  mount(root) {
    this.cv = h("canvas");
    this.ov = h("canvas");
    this.wrap = h("div", { class: "roll-wrap" }, this.cv, this.ov);
    root.replaceChildren(this.wrap);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.wrap);
    this.bind();
    this.resize();
  }
  destroy() { this.ro?.disconnect(); }

  resize() {
    const w = this.wrap.clientWidth, H = this.wrap.clientHeight;
    if (!w) return;
    this.W = w; this.H = H;
    this.ctx = fitCanvas(this.cv, w, H);
    this.octx = fitCanvas(this.ov, w, H);
    const span = this.hi - this.lo + 1;
    this.rowH = Math.max(6, Math.min(16, (H - CHORD_H) / span));
    const visibleRows = Math.floor((H - CHORD_H) / this.rowH);
    if (this.scrollP == null) {
      // centre on the median note
      const ps = this.e.notes.map((n) => n[2]).sort((a, b) => a - b);
      const med = ps[Math.floor(ps.length / 2)];
      this.scrollP = Math.min(this.hi, Math.max(this.lo + visibleRows - 1, med + Math.floor(visibleRows / 2)));
    }
    this.visibleRows = visibleRows;
    this.draw();
  }

  view() {
    const T = this.app.timeline;
    return { start: T.start, end: T.endBeat, ppb: (this.W - KEY_W) / (T.endBeat - T.start) };
  }
  px(beat, v) { return KEY_W + (beat - v.start) * v.ppb; }
  py(p) { return CHORD_H + (this.scrollP - p) * this.rowH; }

  draw() {
    const c = this.ctx;
    if (!c) return;
    const v = this.view(), W = this.W, H = this.H, m = this.app.grid.meter;
    c.clearRect(0, 0, W, H);
    c.fillStyle = cssVar("--surface");
    c.fillRect(0, 0, W, H);
    // rows
    for (let p = this.scrollP; p > this.scrollP - this.visibleRows - 1; p--) {
      const y = this.py(p);
      const inScale = this.scalePcs.has(((p % 12) + 12) % 12);
      c.fillStyle = isBlack(p) ? "#161615" : "#1d1c1b";
      c.fillRect(KEY_W, y, W - KEY_W, this.rowH);
      if (inScale) { c.fillStyle = "rgba(57,135,229,0.045)"; c.fillRect(KEY_W, y, W - KEY_W, this.rowH); }
      if (p % 12 === 0) { c.fillStyle = cssVar("--axis"); c.fillRect(KEY_W, y + this.rowH - 1, W - KEY_W, 1); }
      // keyboard
      const pc = ((p % 12) + 12) % 12;
      c.fillStyle = isBlack(p) ? "#2b2a28" : "#cfcec7";
      if (pc === this.tonic) c.fillStyle = isBlack(p) ? "#2a5c9c" : "#9ec5f4";
      c.fillRect(0, y + 0.5, KEY_W - 2, this.rowH - 1);
      if (pc === 0 || this.rowH >= 12) {
        c.fillStyle = isBlack(p) ? "#bbb" : "#222";
        c.font = `${Math.min(10, this.rowH - 2)}px system-ui, sans-serif`;
        c.textBaseline = "middle";
        c.fillText(noteName(p), 4, y + this.rowH / 2);
      }
    }
    // beat / bar lines
    const b0 = Math.floor(v.start), b1 = Math.ceil(v.end);
    for (let b = b0; b <= b1; b++) {
      const x = Math.round(this.px(b, v));
      if (x < KEY_W) continue;
      if (b % m === 0) { c.fillStyle = cssVar("--axis"); c.fillRect(x, CHORD_H, 1, H); }
      else if (v.ppb > 12) { c.fillStyle = "rgba(255,255,255,0.045)"; c.fillRect(x, CHORD_H, 1, H); }
      if (v.ppb > 48) {
        for (let s = 1; s < 4; s++) {
          c.fillStyle = "rgba(255,255,255,0.02)";
          c.fillRect(Math.round(this.px(b + s / 4, v)), CHORD_H, 1, H);
        }
      }
    }
    // chord strip
    c.fillStyle = cssVar("--surface-2");
    c.fillRect(KEY_W, 0, W - KEY_W, CHORD_H);
    c.font = "11.5px system-ui, sans-serif";
    c.textBaseline = "middle";
    for (const ch of this.app.A.chords) {
      if (ch.root == null) continue;
      const x0 = Math.max(KEY_W, this.px(ch.b0, v)), x1 = this.px(ch.b1, v);
      if (x1 < KEY_W || x0 > W) continue;
      c.fillStyle = cssVar("--grid");
      c.fillRect(Math.round(x0), 2, 1, CHORD_H - 4);
      if (x1 - x0 > 20) {
        c.fillStyle = cssVar("--text-2");
        c.fillText(ch.symbol, x0 + 4, CHORD_H / 2);
      }
    }
    // notes
    c.save();
    c.beginPath(); c.rect(KEY_W, CHORD_H, W - KEY_W, H - CHORD_H); c.clip();
    if (this.showGhosts) {
      c.fillStyle = "rgba(195,194,183,0.16)";
      for (const g of this.ghosts) {
        for (const n of g.notes) {
          if (n[1] < v.start || n[0] > v.end) continue;
          const y = this.py(n[2]);
          if (y < CHORD_H - this.rowH || y > H) continue;
          c.fillRect(this.px(n[0], v), y + 2, Math.max(2, (n[1] - n[0]) * v.ppb - 1), this.rowH - 4);
        }
      }
    }
    for (const n of this.e.notes) {
      if (n[1] < v.start || n[0] > v.end) continue;
      const x = this.px(n[0], v), w = Math.max(2, (n[1] - n[0]) * v.ppb - 1);
      const y = this.py(n[2]);
      if (y < CHORD_H - this.rowH || y > H) continue;
      c.fillStyle = hexToRgba(this.color, 0.35 + 0.65 * (n[3] / 127));
      c.fillRect(x, y + 1, w, this.rowH - 2);
      c.fillStyle = this.color;
      c.fillRect(x, y + 1, Math.min(2, w), this.rowH - 2);
      if (w > 26 && this.rowH >= 11) {
        c.fillStyle = "rgba(0,0,0,0.75)";
        c.font = `${Math.min(10, this.rowH - 3)}px system-ui, sans-serif`;
        c.fillText(noteName(n[2]), x + 4, y + this.rowH / 2);
      }
    }
    c.restore();
    c.fillStyle = cssVar("--axis");
    c.fillRect(KEY_W - 1, 0, 1, H);
  }

  tick(beat) {
    const c = this.octx;
    if (!c) return;
    c.clearRect(0, 0, this.W, this.H);
    const v = this.view();
    const x = this.px(beat, v);
    if (x >= KEY_W && x <= this.W) {
      c.fillStyle = "#fff";
      c.fillRect(Math.round(x), 0, 1.5, this.H);
    }
    // highlight sounding notes
    for (const n of this.e.notes) {
      if (beat >= n[0] && beat < n[1]) {
        const y = this.py(n[2]);
        c.strokeStyle = "#fff";
        c.lineWidth = 1.5;
        c.strokeRect(this.px(n[0], v) + 0.5, y + 1.5, Math.max(2, (n[1] - n[0]) * v.ppb - 2), this.rowH - 3);
        c.fillStyle = "rgba(255,255,255,0.9)";
        c.fillRect(0, y + 1, KEY_W - 2, this.rowH - 2);
      }
    }
  }

  bind() {
    this.ov.addEventListener("wheel", (ev) => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {
        // horizontal zoom / pan goes to the arrangement so both stay in sync
        const T = this.app.timeline;
        ev.preventDefault();
        if (ev.ctrlKey || ev.metaKey) {
          const x = ev.offsetX - KEY_W;
          T.zoom(Math.exp(-ev.deltaY * 0.0022), (x / (this.W - KEY_W)) * T.W);
        } else {
          T.start += (Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY) / T.ppb;
          T.clampView(); T.draw();
        }
        this.draw();
        return;
      }
      ev.preventDefault();
      const dir = Math.sign(ev.deltaY);
      this.scrollP = Math.max(this.lo + this.visibleRows - 1, Math.min(this.hi, this.scrollP - dir * 2));
      this.draw();
    }, { passive: false });
    this.ov.addEventListener("mousemove", (ev) => {
      const v = this.view();
      const beat = v.start + (ev.offsetX - KEY_W) / v.ppb;
      const p = Math.round(this.scrollP - (ev.offsetY - CHORD_H) / this.rowH + 0.5);
      const n = this.e.notes.find((n) => n[2] === p && beat >= n[0] && beat < n[1]);
      if (n) {
        const len = n[1] - n[0];
        tooltip.show(ev.clientX, ev.clientY, `<div class="tt-t">${noteName(n[2])}</div><div class="tt-s">starts ${esc(this.app.grid.barBeatLabel(n[0]).trim())} · ${len.toFixed(2)} beats · velocity ${n[3]}</div>`);
      } else tooltip.hide();
    });
    this.ov.addEventListener("mouseleave", () => tooltip.hide());
    this.ov.addEventListener("mousedown", (ev) => {
      const p = Math.round(this.scrollP - (ev.offsetY - CHORD_H) / this.rowH + 0.5);
      if (ev.offsetX < KEY_W) {                     // keyboard: play the key
        if (ev.offsetY > CHORD_H) this.app.player.audition([p], 0.6);
        return;
      }
      const v = this.view();
      const beat = v.start + (ev.offsetX - KEY_W) / v.ppb;
      const n = this.e.notes.find((n) => n[2] === p && beat >= n[0] && beat < n[1]);
      if (n) {                                      // a note: hear it
        const secs = this.app.grid.time(n[1]) - this.app.grid.time(n[0]);
        this.app.player.audition([n[2]], Math.min(1.2, Math.max(0.25, secs)));
        return;
      }
      this.app.player.seek(Math.max(0, this.app.grid.time(beat)));
    });
  }
}
