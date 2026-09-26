// Shared helpers: DOM, formatting, beat grid, colours, markdown.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** replaceChildren that skips null/false (plain replaceChildren would insert the text "null"). */
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function fmtTime(s, withMs = false) {
  if (!isFinite(s)) s = 0;
  const neg = s < 0;
  s = Math.abs(s);
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  const out = withMs ? `${m}:${sec.toFixed(1).padStart(4, "0")}` : `${m}:${String(Math.floor(sec)).padStart(2, "0")}`;
  return (neg ? "-" : "") + out;
}

export const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
// Ableton convention: MIDI 60 = C3
export const noteName = (p) => `${NOTE_NAMES[((p % 12) + 12) % 12]}${Math.floor(p / 12) - 2}`;
export const isBlack = (p) => [1, 3, 6, 8, 10].includes(((p % 12) + 12) % 12);

export function b64bytes(b64) {
  const bin = atob(b64 || "");
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export const GROUPS = {
  drums: { label: "Drums", color: "--g-drums" },
  bass: { label: "Bass", color: "--g-bass" },
  harmony: { label: "Harmony", color: "--g-harmony" },
  melody: { label: "Melody", color: "--g-melody" },
  vocals: { label: "Vocals", color: "--g-vocals" },
  fx: { label: "Texture / FX", color: "--g-fx" },
};
const _css = getComputedStyle(document.documentElement);
export const cssVar = (name) => _css.getPropertyValue(name).trim();
export const groupColor = (g) => cssVar(GROUPS[g]?.color || "--muted");

export function hexToRgba(hex, a) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** Beat grid: `beats` are times (s) of consecutive beats starting at beat index `firstBeat`. */
export class Grid {
  constructor(beats, firstBeat, meter) {
    this.t = Float64Array.from(beats);
    this.first = firstBeat;
    this.meter = meter;
    const n = this.t.length;
    this.p0 = this.t[1] - this.t[0];
    this.p1 = this.t[n - 1] - this.t[n - 2];
  }
  beat(time) {
    const t = this.t, n = t.length;
    if (time <= t[0]) return this.first + (time - t[0]) / this.p0;
    if (time >= t[n - 1]) return this.first + n - 1 + (time - t[n - 1]) / this.p1;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= time) lo = mid; else hi = mid;
    }
    return this.first + lo + (time - t[lo]) / (t[hi] - t[lo]);
  }
  time(beat) {
    const t = this.t, n = t.length;
    const i = beat - this.first;
    if (i <= 0) return t[0] + i * this.p0;
    if (i >= n - 1) return t[n - 1] + (i - (n - 1)) * this.p1;
    const k = Math.floor(i);
    return t[k] + (i - k) * (t[k + 1] - t[k]);
  }
  barBeatLabel(beat) {
    const bar = Math.floor(beat / this.meter);
    const inBar = beat - bar * this.meter;
    const b = Math.floor(inBar);
    const six = Math.floor((inBar - b) * 4);
    return `${String(bar + 1).padStart(3, " ")}.${b + 1}.${six + 1}`;
  }
}

let toastTimer;
export function toast(msg, ms = 3200) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), ms);
}

export const tooltip = {
  show(x, y, html) {
    const el = $("#tooltip");
    el.innerHTML = html;
    el.style.display = "block";
    const r = el.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  },
  hide() { $("#tooltip").style.display = "none"; },
};

/** Canvas helper: size a canvas to its CSS box at device pixel ratio. */
export function fitCanvas(cv, w, h) {
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.max(1, Math.round(w * dpr));
  cv.height = Math.max(1, Math.round(h * dpr));
  cv.style.width = `${w}px`;
  cv.style.height = `${h}px`;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

export function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Minimal Standard MIDI File writer (format 0). notes: [{beat, dur, pitch, vel}] */
export function midiFile(notes, { bpm = 120, meter = 4, channel = 0, name = "Track Anatomy" } = {}) {
  const TPB = 480;
  const bytes = [];
  const vlq = (n) => {
    const out = [n & 0x7f];
    while ((n >>= 7)) out.unshift((n & 0x7f) | 0x80);
    return out;
  };
  const ev = [];
  const us = Math.round(60000000 / bpm);
  ev.push([0, 0, [0xff, 0x51, 0x03, (us >> 16) & 255, (us >> 8) & 255, us & 255]]);
  ev.push([0, 0, [0xff, 0x58, 0x04, meter, 2, 24, 8]]);
  const nm = [...new TextEncoder().encode(name)].slice(0, 60);
  ev.push([0, 0, [0xff, 0x03, ...vlq(nm.length), ...nm]]);
  for (const n of notes) {
    const t0 = Math.max(0, Math.round(n.beat * TPB));
    const t1 = Math.max(t0 + 1, Math.round((n.beat + n.dur) * TPB));
    ev.push([t0, 1, [0x90 | channel, n.pitch & 127, Math.max(1, Math.min(127, n.vel | 0))]]);
    ev.push([t1, 0, [0x80 | channel, n.pitch & 127, 0]]);
  }
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let last = 0;
  for (const [t, , data] of ev) { bytes.push(...vlq(t - last), ...data); last = t; }
  bytes.push(0, 0xff, 0x2f, 0x00);
  const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
  const head = [0x4d, 0x54, 0x68, 0x64, ...u32(6), 0, 0, 0, 1, (TPB >> 8) & 255, TPB & 255];
  const trk = [0x4d, 0x54, 0x72, 0x6b, ...u32(bytes.length), ...bytes];
  return new Blob([new Uint8Array([...head, ...trk])], { type: "audio/midi" });
}

export function downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

/** Tiny markdown renderer for the generated guide (headings, lists, tables, code, bold). */
export function markdown(md) {
  const lines = md.split("\n");
  let out = "", i = 0;
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      out += `<pre><code>${esc(buf.join("\n"))}</code></pre>`;
      i++;
      continue;
    }
    const hm = l.match(/^(#{1,4})\s+(.*)/);
    if (hm) { out += `<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`; i++; continue; }
    if (l.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
      out += "<table><thead><tr>" + cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
      for (const r of rows.slice(2)) out += "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      out += "</tbody></table>";
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      out += ordered ? "<ol>" : "<ul>";
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        out += `<li>${inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`;
        i++;
      }
      out += ordered ? "</ol>" : "</ul>";
      continue;
    }
    if (l.trim() === "") { i++; continue; }
    const buf = [l];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !/^(#|\||```|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) buf.push(lines[i++]);
    out += `<p>${inline(buf.join(" "))}</p>`;
  }
  return out;
}
