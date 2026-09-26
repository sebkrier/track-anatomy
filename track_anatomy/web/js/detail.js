// Bottom panel: Overview, Drums, Element and Guide tabs.
import { h, $, esc, groupColor, GROUPS, noteName, markdown, toast, hexToRgba, cssVar, tooltip, midiFile, downloadBlob } from "./util.js";
import { PianoRoll } from "./pianoroll.js";
import * as api from "./api.js";

const svgNS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const kid of kids) el.append(kid);
  return el;
};

export class Detail {
  constructor(app) {
    this.app = app;
    this.tab = "overview";
    this.roll = null;
  }

  mount(root) {
    this.tabsEl = h("div", { class: "tabs" });
    this.pane = h("div", { class: "pane" });
    this.root = h("div", { class: "detail" }, this.tabsEl, this.pane);
    root.replaceChildren(this.root);
    this.render();
  }

  setTab(t) { this.tab = t; this.render(); }

  destroy() { this.roll?.destroy(); this.roll = null; this.root = null; this.barCells = null; }

  renderTabs() {
    const A = this.app.A;
    const sel = this.app.sel && A.elements.find((e) => e.id === this.app.sel);
    const tabs = [["overview", "Overview"]];
    if (A.drums?.present) tabs.push(["drums", "Drum patterns"]);
    if (sel) tabs.push(["element", h("span", { class: "row", style: { gap: "7px" } }, h("span", { class: "swatch", style: { background: groupColor(sel.group) } }), sel.name)]);
    tabs.push(["guide", "Recreation guide"]);
    this.tabsEl.replaceChildren(...tabs.map(([id, label]) => {
      const b = h("button", { class: `tab${this.tab === id ? " on" : ""}` }, label);
      b.onclick = () => this.setTab(id);
      return b;
    }), h("span", { class: "spacer" }));
  }

  render() {
    if (!this.root) return;
    if (this.tab === "element" && !this.app.sel) this.tab = "overview";
    this.renderTabs();
    this.roll?.destroy();
    this.roll = null;
    this.pane.scrollTop = 0;
    const fn = { overview: this.overview, drums: this.drums, element: this.element, guide: this.guide }[this.tab];
    this.pane.replaceChildren();
    fn.call(this, this.pane);
  }

  onTick(t, beat) {
    this.roll?.tick(beat);
    if (this.tab === "drums" && this.barCells) {
      const bar = Math.floor(beat / this.app.grid.meter);
      if (bar !== this._curBar) {
        this.barCells[this._curBar]?.classList.remove("cur");
        this.barCells[bar]?.classList.add("cur");
        this._curBar = bar;
      }
    }
  }
  onView() { this.roll?.draw(); }

  // ------------------------------------------------------------------ overview
  overview(pane) {
    const A = this.app.A, g = A.global, k = g.key;
    const cards = h("div", { class: "cards" });

    // tempo
    const tf = A.options?.tempo_factor || 1;
    const fixed = A.options && Object.keys(A.options).length;
    const tempo = g.has_beat === false ?
      h("div", { class: "card" },
        h("h3", {}, "Tempo & grid"),
        h("div", { class: "big" }, "No clear beat"),
        h("p", {}, "No steady pulse was found (ambient, free time or spoken word), so bars are drawn on a 120 BPM placeholder grid. Read bar numbers as time markers only.")) :
      h("div", { class: "card" },
        h("h3", {}, "Tempo & grid"),
        h("div", { class: "big num" }, g.bpm.toFixed(g.bpm % 1 ? 2 : 0), h("small", {}, `BPM · ${g.meter}/4`)),
        h("p", {}, g.steady ? "Steady tempo: set Ableton to this BPM and everything lines up." : "The tempo drifts: the grid follows the performance. Warp the stems in Ableton."),
        ...(g.notes || []).map((n) => h("div", { class: "note" }, n)),
        h("div", { class: "note", style: { marginTop: "8px" } }, "Turn on the metronome (K) and listen: if the clicks sit between the beats or the bar numbers feel off, fix the grid:"),
        h("div", { class: "btn-row" },
          tf >= 1 ? this.optBtn("Half tempo", { tempo_factor: 0.5 }, `${(g.bpm / 2).toFixed(1)} BPM`) : null,
          tf <= 1 ? this.optBtn("Double tempo", { tempo_factor: 2 }, `${(g.bpm * 2).toFixed(1)} BPM`) : null,
          this.optBtn("Shift bar 1 by a beat", { downbeat_shift: 1 }, "move the downbeat one beat later"),
          fixed ? this.optBtn("Reset", { reset: true }, "undo grid fixes") : null));
    cards.append(tempo);

    // key
    const kb = keyboard(k);
    const unclear = k.clear === false;
    cards.append(h("div", { class: "card" },
      h("h3", {}, "Key", h("span", { class: "r muted" }, `confidence ${Math.round(Math.min(1, k.confidence * 4) * 100)}%`)),
      h("div", { class: "big" }, unclear ? "Unclear" : k.name),
      unclear ? h("p", {}, `Not enough tonal material to call a key. Best guess: ${k.name}.`) : null,
      h("p", {}, `Scale: ${k.scale.join(" ")}`),
      kb,
      h("div", { class: "note", style: { marginTop: "8px" } }, "Also possible: " + k.alternatives.slice(0, 2).map((a) => `${a.tonic} ${a.mode}`).join(", ")),
      k.note ? h("div", { class: "note" }, k.note) : null));

    // groove
    const D = A.drums;
    if (D?.present) {
      const sw = D.swing && !D.swing.straight ? D.swing : null;
      const feel = Object.entries(D.feel_ms || {}).filter(([, v]) => Math.abs(v) >= 4);
      cards.append(h("div", { class: "card" },
        h("h3", {}, "Groove"),
        h("div", { class: "big num" }, sw ? `${Math.round(sw.percent)}%` : "Straight", h("small", {}, sw ? `swing on ${sw.grid} notes` : "")),
        h("p", {}, sw ? `Apply a ${Math.round(sw.percent)}% swing (Groove Pool, "Swing ${sw.grid === "16th" ? "16ths" : "8ths"}"). 50% is straight, 66% is a full triplet shuffle.` :
          "No measurable swing: program the drums straight on the grid."),
        h("p", {}, `Grid: ${D.steps_per_beat === 4 ? "16th notes" : D.steps_per_beat === 3 ? "8th-note triplets" : "16th-note triplets"}.`),
        feel.length ? h("div", { class: "note" }, "Timing feel vs the grid: " + feel.map(([p, v]) => `${pieceName(p)} ${v > 0 ? "+" : ""}${Math.round(v)} ms`).join(" · ") + " (+ = behind the beat)") : null));
    }

    // reliability
    const level = (x) => (x >= 0.75 ? ["high", "var(--good)"] : x >= 0.45 ? ["medium", "var(--warning)"] : ["low", "var(--critical)"]);
    const rel = [];
    const gl = level(g.has_beat === false ? 0 : g.confidence);
    rel.push(["Beat grid", gl, g.has_beat === false ? "no pulse found" : g.steady ? "steady tempo" : "tempo drifts"]);
    const kl = level(Math.min(1, k.confidence * 4));
    rel.push(["Key", kl, `vs ${k.alternatives[0].tonic} ${k.alternatives[0].mode}`]);
    if (D?.present && D.patterns.length) {
      const drumBars = D.bar_pattern.filter(Boolean).length || 1;
      const cov = D.patterns.slice(0, 3).reduce((a, p) => a + p.count, 0) / drumBars;
      rel.push(["Drum patterns", level(cov), `top 3 patterns cover ${Math.round(cov * 100)}% of drum bars`]);
    }
    cards.append(h("div", { class: "card" },
      h("h3", {}, "How much to trust this"),
      h("div", { style: { display: "grid", gap: "6px" } }, rel.map(([name, [lab, col], why]) =>
        h("div", { class: "row", style: { gap: "8px" } },
          h("span", { style: { width: "8px", height: "8px", borderRadius: "50%", background: col, flex: "none" } }),
          h("b", { style: { fontWeight: 600 } }, name), h("span", { class: "dim" }, lab), h("span", { class: "muted", style: { fontSize: "12px" } }, `· ${why}`)))),
      h("p", { class: "note", style: { marginTop: "10px" } },
        "Notes come from AI transcription of separated stems: expect some missing or extra notes in dense mixes. " +
        "Pad / lead / arp layers are split by how the notes behave, not by a model, so solo each layer to check it by ear.")));

    // structure table
    const byId = Object.fromEntries(A.elements.map((e) => [e.id, e]));
    const tag = (id, sign) => {
      const e = byId[id];
      return e ? h("span", { class: `el-tag${sign ? " chg" : ""}` }, sign ? h("b", { class: sign === "+" ? "plus" : "minus" }, sign) : null,
        h("i", { style: { background: groupColor(e.group) } }), e.name) : null;
    };
    const drumPatterns = (sec) => {
      if (!D?.present) return "";
      const cnt = {};
      for (let b = sec.b0; b < sec.b1; b++) {
        const p = (D.bar_pattern[b] || "").replace("'", "");
        if (p && p !== "*") cnt[p] = (cnt[p] || 0) + 1;
      }
      return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([p, n]) => `${p}×${n}`).join(" ");
    };
    const tbl = h("table", { class: "t" },
      h("thead", {}, h("tr", {}, ["Section", "Bars", "Energy", "Changes", "What's playing", "Drums", "Chords"].map((t) => h("th", {}, t)))),
      h("tbody", {}, A.sections.map((sec, i) => {
        const prev = i ? new Set(A.sections[i - 1].elements) : new Set();
        const cur = new Set(sec.elements);
        const ins = sec.elements.filter((x) => !prev.has(x));
        const outs = i ? A.sections[i - 1].elements.filter((x) => !cur.has(x)) : [];
        const tr = h("tr", { class: "click" },
          h("td", {}, h("b", {}, sec.label), sec.key ? h("div", { class: "note" }, `in ${sec.key}`) : null),
          h("td", { class: "num" }, `${sec.b0 + 1}–${sec.b1}`, h("div", { class: "note" }, `${sec.bars} bars`)),
          h("td", {}, h("span", { class: "energy", title: `${Math.round(sec.energy * 100)}%` }, h("i", { style: { width: `${Math.round(sec.energy * 100)}%` } }))),
          h("td", {}, h("div", { class: "els" }, i === 0 ? h("span", { class: "muted" }, "start") : [...ins.map((x) => tag(x, "+")), ...outs.map((x) => tag(x, "−"))])),
          h("td", {}, h("div", { class: "els" }, sec.elements.map((id) => tag(id)))),
          h("td", { class: "dim num" }, drumPatterns(sec)),
          h("td", { class: "dim" }, sec.chord_loop ? sec.chord_loop.chords.join(" | ") : ""));
        tr.onclick = () => this.app.selectSection(i);
        tr.ondblclick = () => this.app.selectSection(i, true);
        return tr;
      })));
    cards.append(h("div", { class: "card wide" }, h("h3", {}, "Arrangement", h("span", { class: "r muted" }, "click a row to loop it · double-click to play")), tbl));

    // chord loops
    const loops = [];
    const seen = new Set();
    for (const sec of A.sections) {
      const lp = sec.chord_loop;
      if (!lp) continue;
      const key = lp.chords.join("|");
      if (seen.has(key)) { loops.find((l) => l.key === key).secs.push(sec.label); continue; }
      seen.add(key);
      loops.push({ key, lp, secs: [sec.label] });
    }
    const romanOf = (sym) => A.chords.find((c) => c.symbol === sym)?.roman || "";
    const chordChip = (bar) => {
      const syms = bar.split(" ").filter((x) => x && x !== "-");
      const chip = h("button", { class: "chord", title: syms.length ? "Click to hear it" : "" },
        h("b", {}, syms.length ? syms.join(" ") : "–"), h("span", {}, syms.map(romanOf).join(" ")));
      chip.onclick = () => {
        syms.forEach((s, i) => {
          const ch = A.chords.find((c) => c.symbol === s && c.root != null);
          if (ch) setTimeout(() => this.app.player.audition(chordPitches(ch), 0.9), i * 450);
        });
      };
      return chip;
    };
    cards.append(h("div", { class: "card wide" },
      h("h3", {}, "Chord progression", h("span", { class: "r" }, dl(this.app, "midi", "chords.mid", "chords.mid"))),
      loops.length ? loops.map((l) => h("div", { style: { marginBottom: "12px" } },
        h("div", { class: "note", style: { marginBottom: "6px" } }, `${l.lp.bars}-bar loop · ${l.secs.join(", ")}`),
        h("div", { class: "chord-seq" }, l.lp.chords.map(chordChip)))) :
        h("p", {}, A.chords.length ? "No repeating loop; see the chord lane in the arrangement." : "No chords detected (drums-only or atonal material)."),
      h("div", { class: "note" }, "Click a chord to hear it. Chords come from the harmonic stems (bass + synths + keys + guitar); chords.mid has simple block voicings to drop on a pad.")));

    // fx
    if (A.fx_events.length) {
      cards.append(h("div", { class: "card" },
        h("h3", {}, "Transitions & FX"),
        h("ul", { class: "tips" }, A.fx_events.filter((e) => e.type !== "impact").slice(0, 14).map((e) => {
          const li = h("li", { style: { cursor: "pointer" } }, h("b", {}, e.label), ` · bars ${e.bar0 + 1}${e.bar1 > e.bar0 ? "–" + e.bar1 : ""}`, e.detail ? h("span", { class: "muted" }, ` (${e.detail})`) : "");
          li.onclick = () => this.app.player.seek(this.app.grid.time(e.bar0 * this.app.grid.meter));
          return li;
        })),
        A.fx_events.some((e) => e.type === "impact") ? h("div", { class: "note" }, `Crash/impact hits on ${A.fx_events.filter((e) => e.type === "impact").length} section downbeats.`) : null));
    }

    // export
    const folderInput = h("input", { type: "text", class: "path-input", placeholder: "e.g. D:\\Music\\Ableton Projects", spellcheck: "false" });
    const saveBtn = h("button", { class: "btn btn-primary" }, "Save Ableton project to this folder");
    const saveMsg = h("div", { class: "note", style: { marginTop: "6px" } });
    api.settings().then((s) => {
      folderInput.value = s.ableton_dir || "";
      if (!s.als_available) saveMsg.textContent = "Live's template wasn't found, so the export contains stems + MIDI without a .als.";
    }).catch(() => { });
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      saveMsg.textContent = "Writing the project (aligning stems takes a few seconds)…";
      try {
        await api.saveSettings({ ableton_dir: folderInput.value });
        const r = await api.saveProject(this.app.id, folderInput.value);
        saveMsg.replaceChildren("Saved: ", h("code", {}, r.als || r.folder), r.als ? " · open it in Live (File > Open Live Set, or double-click it)." : "");
      } catch (e) { saveMsg.textContent = `Couldn't save: ${e.message}`; }
      saveBtn.disabled = false;
    };
    cards.append(h("div", { class: "card wide" },
      h("h3", {}, "Rebuild it in Ableton"),
      h("p", {}, "The Ableton project has the tempo and time signature set, a locator at every section, one MIDI track per part (add your instruments; the drums track uses the Drum Rack layout), every stem on an audio track aligned to bar 1 and warped to the grid, and the track's measured groove in the Groove Pool."),
      h("div", { class: "row", style: { marginTop: "10px" } }, folderInput, saveBtn),
      saveMsg,
      h("div", { class: "btn-row" },
        h("a", { class: "btn", href: `/api/tracks/${this.app.id}/export?full=1`, title: "Stems are 24-bit WAV. Saving to a folder (above) skips the download.",
          onclick: () => toast("Building the project: aligning stems takes a few seconds…") }, `Download Ableton project (.zip, ~${zipEstimate(A)})`),
        h("a", { class: "btn", href: `/api/tracks/${this.app.id}/export?full=0` }, "MIDI + guide only (.zip)"),
        dl(this.app, "midi", "all_parts.mid", "All parts (one multi-track MIDI)", "btn")),
      h("ol", { class: "tips", style: { marginTop: "14px" } },
        h("li", {}, "Work part by part: solo it here, load the suggested device on its MIDI track, A/B against the stem, then mute the stem."),
        h("li", {}, `Tempo ${g.bpm.toFixed(2)} BPM, ${g.meter}/4. Bar 1 is ${g.origin_time.toFixed(3)} s into the original file (the exported stems are already shifted).`),
        h("li", {}, "MIDI comes in two flavours: groove (exact timing) and quantized (snapped to the grid)."))));

    pane.append(cards);
  }

  optBtn(label, opts, sub) {
    const b = h("button", { class: "btn btn-sm", title: sub }, label);
    b.onclick = async () => {
      if (!confirm(`Re-run the analysis with "${label}"? Separation is cached, so this takes under a minute.`)) return;
      try {
        await api.reanalyze(this.app.id, opts);
        this.app.goProcessing();
      } catch (e) { toast(e.message); }
    };
    return b;
  }

  // ------------------------------------------------------------------ drums
  drums(pane) {
    const A = this.app.A, D = A.drums, m = this.app.grid.meter;
    const color = groupColor("drums");
    const laneLabel = Object.fromEntries(D.lanes.map((l) => [l.key, l.label]));
    const top = h("div", { class: "row", style: { marginBottom: "12px", gap: "10px" } },
      h("span", { class: "chip" }, "Grid ", h("b", {}, D.steps_per_beat === 4 ? "16ths" : "triplets")),
      h("span", { class: "chip" }, "Swing ", h("b", {}, D.swing && !D.swing.straight ? `${Math.round(D.swing.percent)}%` : "straight")),
      h("span", { class: "chip" }, "Patterns ", h("b", {}, D.patterns.length)),
      h("span", { class: "spacer", style: { flex: 1 } }),
      dl(this.app, "midi", "drums.mid", "drums.mid (groove)", "btn btn-sm"),
      dl(this.app, "midi", "drums_quantized.mid", "drums.mid (quantized)", "btn btn-sm"));
    pane.append(top);

    // bar strip
    this.barCells = [];
    const strip = h("div", { class: "bar-strip" });
    D.bar_pattern.forEach((p, i) => {
      const id = p.replace("'", "");
      const idx = D.patterns.findIndex((x) => x.id === id);
      const bg = idx >= 0 ? hexToRgba(color, Math.max(0.25, 0.9 - idx * 0.13)) : "";
      const cell = h("span", {
        class: `${p ? "" : "empty"} ${p.includes("'") ? "var" : ""}`,
        style: bg ? { background: bg, color: "#fff" } : {},
        title: `Bar ${i + 1}: ${p ? (p === "*" ? "one-off fill / transition" : `pattern ${p}${p.includes("'") ? " (variation)" : ""}`) : "no drums"}`,
      }, p === "*" ? "·" : p.replace("'", "′") || "");
      cell.onclick = () => this.app.player.seek(this.app.grid.time(i * m));
      strip.append(cell);
      this.barCells.push(cell);
    });
    pane.append(h("div", { class: "card", style: { marginBottom: "12px" } },
      h("h3", {}, "Pattern per bar", h("span", { class: "r muted" }, "′ = small variation · · = one-off fill · click to jump")), strip));

    // pattern grids
    const grids = h("div", { class: "patterns" });
    for (const p of D.patterns) {
      const steps = D.steps_per_bar, spb = D.steps_per_beat;
      const lanes = Object.keys(p.grid);
      const g = h("div", { class: "steps", style: { gridTemplateColumns: `max-content 1fr` } });
      g.append(h("span"), h("div", { class: "cells", style: { gridTemplateColumns: `repeat(${steps}, 1fr)` } },
        Array.from({ length: steps }, (_, i) => h("span", { class: `num${i % spb === 0 && i ? " gap" : ""}` }, i % spb === 0 ? String(i / spb + 1) : ""))));
      for (const k of lanes) {
        g.append(h("span", { class: "lbl" }, laneLabel[k] || k));
        g.append(h("div", { class: "cells", style: { gridTemplateColumns: `repeat(${steps}, 1fr)` } },
          p.grid[k].map((v, i) => h("span", {
            class: `cell${Math.floor(i / spb) % 2 ? " beat" : ""}${i % spb === 0 && i ? " gap" : ""}`,
            style: v ? { background: hexToRgba(color, 0.3 + 0.7 * (v / 127)) } : {},
            title: v ? `${laneLabel[k]} · step ${i + 1} · velocity ${v}` : "",
          }))));
      }
      const first = p.bars[0];
      const play = h("button", { class: "btn btn-sm", title: "Loop the first bar that plays this pattern" }, "▶ Loop it");
      play.onclick = () => {
        const exact = p.bars.find((b) => D.bar_pattern[b] === p.id) ?? first;
        this.app.loopBars(exact, exact + 1, true);
      };
      const midi = h("button", { class: "btn btn-sm", title: "One-bar MIDI clip in the Drum Rack layout" }, "MIDI");
      midi.onclick = () => {
        const notes = [];
        for (const [k, vels] of Object.entries(p.grid)) {
          const pitch = +k.split(":")[1];
          vels.forEach((v, i) => { if (v) notes.push({ beat: i / spb, dur: 0.25, pitch, vel: v }); });
        }
        const slug = (A.track.title || "track").replace(/[^\w]+/g, "-").slice(0, 40);
        downloadBlob(midiFile(notes, { bpm: A.global.bpm, meter: m, channel: 9, name: `Pattern ${p.id}` }), `${slug} - drum pattern ${p.id}.mid`);
      };
      grids.append(h("div", { class: "pattern" },
        h("h4", {}, h("span", { class: "id" }, p.id), `${p.count} bars`, h("small", {}, `first at bar ${first + 1}`), h("span", { style: { flex: 1 } }), midi, play),
        g));
    }
    pane.append(grids);

    // sounds
    const sounds = h("div", { class: "cards", style: { marginTop: "12px" } });
    for (const e of A.elements.filter((e) => e.group === "drums")) {
      sounds.append(this.suggestCard(e, true));
    }
    pane.append(h("h3", { class: "section-title", style: { margin: "18px 0 10px" } }, "Drum sounds"), sounds);
  }

  // ------------------------------------------------------------------ element
  element(pane) {
    const A = this.app.A;
    const e = A.elements.find((x) => x.id === this.app.sel);
    if (!e) return;
    const color = groupColor(e.group);
    const P = this.app.player;
    const soloBtn = h("button", { class: `btn btn-sm${P.solos.has(e.id) && P.solos.size === 1 ? " on" : ""}` }, "Solo");
    soloBtn.onclick = () => { const on = !(P.solos.has(e.id) && P.solos.size === 1); P.toggleSolo([e.id], on, true); this.render(); };
    const firstBar = e.activity.findIndex((a) => a >= 0.3);
    const jump = h("button", { class: "btn btn-sm" }, "Jump to first entry");
    jump.onclick = () => { if (firstBar >= 0) P.seek(this.app.grid.time(firstBar * this.app.grid.meter)); };
    pane.append(h("div", { class: "el-header" },
      h("span", { class: "swatch", style: { background: color, width: "14px", height: "14px" } }),
      h("h2", {}, e.name),
      h("span", { class: "chip" }, GROUPS[e.group].label),
      h("span", { class: "chip" }, `${e.level_db > 0 ? "+" : ""}${e.level_db} dB vs mix`),
      h("span", { style: { flex: 1 } }),
      soloBtn, firstBar >= 0 ? jump : null,
      e.midi ? dl(this.app, "midi", e.midi, "MIDI (groove)", "btn btn-sm") : null,
      e.midi_q ? dl(this.app, "midi", e.midi_q, "MIDI (quantized)", "btn btn-sm") : null,
      dl(this.app, "stems", e.audio.split("/")[1], "Audio (FLAC)", "btn btn-sm")));

    const plays = A.sections.map((s, i) => [s, i]).filter(([s]) => s.elements.includes(e.id));
    if (plays.length) {
      pane.append(h("div", { class: "plays-in" }, h("span", { class: "note" }, "Plays in"),
        plays.map(([s, i]) => {
          const b = h("button", { class: "sec-chip", title: `Loop ${s.label} (bars ${s.b0 + 1}–${s.b1})` }, s.label);
          b.onclick = () => this.app.selectSection(i, true);
          return b;
        })));
    }
    const left = h("div");
    const right = h("div", { style: { display: "grid", gap: "12px" } });
    pane.append(h("div", { class: "el-layout" }, left, right));

    if (e.notes?.length) {
      const holder = h("div");
      left.append(holder);
      this.roll = new PianoRoll(this.app, e, color);
      this.roll.mount(holder);
      const ghostToggle = this.roll.ghosts.length ? h("label", { class: "note", style: { display: "inline-flex", gap: "6px", alignItems: "center", cursor: "pointer" } },
        h("input", { type: "checkbox", checked: true, onchange: (ev) => { this.roll.showGhosts = ev.target.checked; this.roll.draw(); } }),
        `Show the other layers from the same stem in grey (${this.roll.ghosts.map((g) => g.name).join(", ")})`) : null;
      left.append(h("div", { class: "row", style: { marginTop: "6px", justifyContent: "space-between" } },
        h("span", { class: "note" }, "Click a note to hear it · click empty space to jump · scroll: octaves · Ctrl+scroll: zoom"), ghostToggle));
      if (e.chord_fraction != null && ["other", "piano", "guitar"].includes(e.kind)) {
        left.append(h("div", { class: "note", style: { marginTop: "4px" } },
          "Layers are split from one separated stem by how the notes behave (held chords, short chords, single-note lines, fast runs). Solo the layer to check the split by ear."));
      }
    } else if (e.hits?.length) {
      left.append(this.hitsCard(e, color));
    } else {
      left.append(h("div", { class: "card" }, h("h3", {}, "Audio-only layer"),
        h("p", {}, "No clear notes: noise, sweeps, reverb wash and atmospheres from the synth stem. Solo it to hear what's in it; the FX lane in the arrangement marks risers and impacts.")));
    }
    const ns = e.features?.notes;
    if (ns) {
      left.append(h("div", { class: "card", style: { marginTop: "12px" } },
        h("h3", {}, "Notes"),
        h("dl", { class: "kv" },
          h("dt", {}, "Range"), h("dd", {}, `${ns.low} – ${ns.high} (median ${ns.median})`),
          h("dt", {}, "Voices"), h("dd", {}, ns.mono ? "monophonic (one note at a time)" : `up to ${ns.max_polyphony} at once (avg ${ns.mean_polyphony})`),
          h("dt", {}, "Typical length"), h("dd", {}, `${ns.typical_length} (${ns.median_len_beats} beats)`),
          h("dt", {}, "Density"), h("dd", {}, `${ns.notes_per_bar} notes per bar`),
          h("dt", {}, "Legato"), h("dd", {}, `${Math.round(ns.legato * 100)}% of notes run into the next`),
          ns.glide_fraction ? [h("dt", {}, "Glides"), h("dd", {}, `${Math.round(ns.glide_fraction * 100)}% of notes bend`)] : null)));
    }
    right.append(this.suggestCard(e, false));
    right.append(this.soundCard(e, color));
  }

  hitsCard(e, color) {
    const D = this.app.A.drums;
    const rows = [];
    for (const p of D.patterns.slice(0, 6)) {
      for (const [k, v] of Object.entries(p.grid)) {
        if (!k.startsWith(e.id + ":")) continue;
        rows.push(h("div", { class: "row", style: { gap: "8px", marginBottom: "4px" } },
          h("span", { class: "chip", style: { width: "34px", justifyContent: "center" } }, p.id),
          h("div", { class: "cells", style: { display: "grid", gridTemplateColumns: `repeat(${v.length}, 1fr)`, gap: "2px", flex: 1 } },
            v.map((vel, i) => h("span", { class: `cell${Math.floor(i / D.steps_per_beat) % 2 ? " beat" : ""}`, style: { height: "16px", borderRadius: "3px", background: vel ? hexToRgba(color, 0.3 + 0.7 * vel / 127) : "" } })))));
      }
    }
    return h("div", { class: "card" },
      h("h3", {}, `${e.name} in each pattern`),
      h("div", { class: "steps" }, rows.length ? rows : h("p", {}, "Only in fills / one-off bars.")),
      h("div", { class: "note", style: { marginTop: "8px" } }, `${e.hits.length} hits detected. Full patterns are on the Drum patterns tab.`));
  }

  suggestCard(e, compact) {
    const sg = e.suggest || { device: "", tips: [], fx: [] };
    const ch = e.features?.drum;
    return h("div", { class: "card" },
      h("h3", {}, compact ? h("span", { class: "row", style: { gap: "7px" } }, h("span", { class: "swatch", style: { background: groupColor(e.group) } }), e.name) : "Start with",
        compact ? null : h("span", { class: "r muted" }, "Ableton Live 12")),
      h("div", { class: "device" }, sg.device),
      sg.tips.length || sg.fx.length ? h("ul", { class: "tips" },
        sg.tips.map((t) => h("li", {}, t)),
        sg.fx.map((t) => h("li", { class: "fx" }, t))) : null,
      ch && compact && ch.style ? h("div", { class: "evidence" }, ch.style) : null);
  }

  soundCard(e, color) {
    const f = e.features || {};
    const tim = f.timbre || {}, env = f.envelope || {}, w = f.width || {};
    const card = h("div", { class: "card" }, h("h3", {}, "Sound analysis", h("span", { class: "r muted" }, "measured on the separated stem")));
    const kv = h("dl", { class: "kv" });
    const add = (k, v) => { if (v != null && v !== "") kv.append(h("dt", {}, k), h("dd", {}, v)); };
    add("Waveform", tim.wave);
    add("Movement", tim.movement_label);
    add("Brightness", tim.reach_hz ? `harmonics up to ~${tim.reach_hz >= 1000 ? (tim.reach_hz / 1000).toFixed(1) + " kHz" : Math.round(tim.reach_hz) + " Hz"}` : tim.centroid_hz ? `centroid ${Math.round(tim.centroid_hz)} Hz` : null);
    add("Attack", env.attack_label ? `${env.attack_label} (${Math.round(env.attack_ms)} ms)` : null);
    add("Sustain", env.sustain_label);
    add("Tail", env.tail_label ? `${env.tail_label} (~${env.tail_s} s)` : null);
    add("Stereo", w.label ? `${w.label} (side/mid ${w.side_mid_db} dB)` : null);
    if (f.drum) {
      const d = f.drum;
      add("Pitch", d.pitch_note ? `${d.pitch_note} (${d.pitch_hz} Hz)` : null);
      add("Decay", d.decay_ms ? `${d.decay_ms} ms` : null);
      add("Character", d.style || (d.clap_like ? "clap-like" : null));
    }
    card.append(kv);
    if (tim.harmonics_db?.length) card.append(h("div", { class: "note", style: { marginTop: "12px" } }, "Harmonic spectrum (dB vs strongest of the first 4)"), harmonicsChart(tim.harmonics_db, color));
    if (env.attack_ms != null) card.append(h("div", { class: "note", style: { marginTop: "12px" } }, "Amplitude envelope (sketch)"), envelopeChart(env, color));
    if (w.side_mid_db != null) {
      const pos = Math.min(1, Math.max(0, (w.side_mid_db + 30) / 30));
      card.append(h("div", { class: "note", style: { marginTop: "12px" } }, "Stereo width"),
        h("div", { class: "meter" }, h("i", { style: { left: `calc(${pos * 100}% - 1px)` } })),
        h("div", { class: "meter-labels" }, h("span", {}, "mono"), h("span", {}, "wide"), h("span", {}, "very wide")));
    }
    const pc = f.pumping;
    if (pc && (pc.detected || pc.depth_db >= 3)) {
      card.append(h("div", { class: "note", style: { marginTop: "12px" } },
        pc.detected ? `Sidechain pumping: ${pc.depth_db} dB dip, recovers in ~${pc.release_ms} ms` : `Level movement over a beat (${pc.depth_db} dB): probably the notes' own envelope, not sidechain`),
        pumpChart(pc.shape, color));
    }
    if (f.echo?.detected) {
      card.append(h("div", { class: "warn-note", style: { color: "var(--text-2)", marginTop: "10px" } },
        `Delay detected: repeats every ${f.echo.label} (~${f.echo.ms} ms), feedback ~${Math.round(f.echo.feedback * 100)}% (evidence score ${f.echo.score})`));
    }
    for (const m of f.filter_moves || []) {
      card.append(h("div", { class: "warn-note", style: { color: "var(--text-2)", marginTop: "8px" } }, `Filter ${m.direction} over bars ${m.bar0 + 1}–${m.bar1 + 1} (~${m.octaves} oct)`));
    }
    return card;
  }

  // ------------------------------------------------------------------ guide
  async guide(pane) {
    const box = h("div", { class: "guide" }, h("p", { class: "muted" }, "Loading…"));
    pane.append(box);
    try {
      const md = await api.guide(this.app.id);
      box.innerHTML = markdown(md);
      box.prepend(h("div", { class: "btn-row", style: { marginBottom: "8px" } },
        dl(this.app, "root", "GUIDE.md", "Download GUIDE.md", "btn btn-sm")));
    } catch (e) {
      box.textContent = "Couldn't load the guide.";
    }
  }
}

// ------------------------------------------------------------------ small pieces
const PC = { C: 0, "C#": 1, Db: 1, D: 2, "D#": 3, Eb: 3, E: 4, F: 5, "F#": 6, Gb: 6, G: 7, "G#": 8, Ab: 8, A: 9, "A#": 10, Bb: 10, B: 11, Cb: 11, Fb: 4 };

/** Rough size of the full Ableton export: one 24-bit stereo WAV per part at 44.1 kHz. */
function zipEstimate(A) {
  const bytes = A.track.duration * 44100 * 2 * 3 * A.elements.filter((e) => e.audio).length;
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

/** A playable voicing: bass note in octave 2, chord tones stacked upward from the root in octave 3. */
function chordPitches(ch) {
  const root = 48 + ch.root;
  const out = [36 + (ch.bass ?? ch.root)];
  let prev = root - 1;
  for (const name of ch.tones) {
    let p = 48 + (PC[name] ?? 0);
    while (p <= prev) p += 12;
    out.push(p);
    prev = p;
  }
  return out;
}

function pieceName(p) {
  return { kick: "kick", snare: "snare", hh: "hats", toms: "toms", ride: "ride", crash: "crash" }[p] || p;
}

function dl(app, sub, name, label, cls = "btn btn-sm btn-ghost") {
  return h("a", { class: cls, href: `/api/tracks/${app.id}/file/${sub}/${encodeURIComponent(name)}?download=1` },
    h("span", { style: { display: "inline-flex" }, html: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>' }),
    label);
}

function keyboard(k) {
  const steps = k.mode === "major" ? [0, 2, 4, 5, 7, 9, 11] : [0, 2, 3, 5, 7, 8, 10];
  const inScale = new Set(steps.map((s) => (k.tonic_pc + s) % 12));
  const whites = [0, 2, 4, 5, 7, 9, 11], blacks = [[1, 1], [3, 2], [6, 4], [8, 5], [10, 6]];
  const names = ["C", "D", "E", "F", "G", "A", "B"];
  const kb = h("div", { class: "keyboard" });
  whites.forEach((pc, i) => kb.append(h("div", { class: `w${inScale.has(pc) ? " in" : ""}${pc === k.tonic_pc ? " tonic" : ""}` }, names[i])));
  for (const [pc, pos] of blacks) kb.append(h("div", { class: `b${inScale.has(pc) ? " in" : ""}${pc === k.tonic_pc ? " tonic" : ""}`, style: { left: `${(pos / 7) * 100}%` } }));
  return kb;
}

function chartSvg(w, hgt) {
  return s("svg", { viewBox: `0 0 ${w} ${hgt}`, class: "mini-chart", preserveAspectRatio: "none" });
}

function harmonicsChart(db, color) {
  const W = 300, H = 90, n = Math.min(db.length, 24);
  const svg = chartSvg(W, H);
  const bw = W / n;
  const y = (v) => Math.min(H - 12, ((Math.max(-60, Math.min(0, v)) / -60)) * (H - 14));
  svg.append(s("line", { x1: 0, x2: W, y1: y(-30) + 0.5, y2: y(-30) + 0.5, stroke: cssVar("--grid"), "stroke-width": 1 }));
  for (let i = 0; i < n; i++) {
    const v = db[i];
    const top = y(v);
    const r = s("rect", { x: i * bw + 1, y: top, width: Math.max(1, bw - 2), height: Math.max(1, H - 12 - top), rx: 2, fill: color, opacity: i % 2 ? 0.95 : 0.7 });
    r.addEventListener("mousemove", (ev) => tooltip.show(ev.clientX, ev.clientY, `<div class="tt-t">Harmonic ${i + 1}${i ? ` (${i % 2 ? "even" : "odd"})` : " (fundamental)"}</div><div class="tt-s">${v.toFixed(1)} dB</div>`));
    r.addEventListener("mouseleave", () => tooltip.hide());
    svg.append(r);
    if (i === 0 || (i + 1) % 4 === 0) svg.append(s("text", { x: i * bw + bw / 2, y: H - 1, "text-anchor": "middle", fill: cssVar("--muted"), "font-size": 9 }, String(i + 1)));
  }
  return svg;
}

function envelopeChart(env, color) {
  const W = 300, H = 70;
  const svg = chartSvg(W, H);
  const a = Math.min(90, Math.max(3, (env.attack_ms || 5) / 4));
  const susDb = env.sustain_db ?? -6;
  const sus = Math.max(0.05, Math.pow(10, susDb / 20));
  const tail = Math.min(110, Math.max(10, (env.tail_s || 0.3) * 30));
  const top = 6, base = H - 6;
  const ys = base - (base - top) * sus;
  const d = `M 0 ${base} L ${a} ${top} L ${a + 40} ${ys} L ${W - tail - 20} ${ys} L ${W - 20} ${base}`;
  svg.append(s("path", { d: d + ` L 0 ${base} Z`, fill: hexToRgba(color, 0.18) }));
  svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
  for (const [x, t] of [[a / 2, "A"], [a + 20, "D"], [(a + 40 + W - tail - 20) / 2, "S"], [W - tail / 2 - 20, "R"]]) {
    svg.append(s("text", { x, y: H - 1, "text-anchor": "middle", fill: cssVar("--muted"), "font-size": 9 }, t));
  }
  return svg;
}

function pumpChart(shape, color) {
  const W = 300, H = 70, n = shape.length;
  const svg = chartSvg(W, H);
  const lo = Math.min(-12, ...shape);
  const y = (v) => 6 + ((0 - v) / (0 - lo)) * (H - 18);
  svg.append(s("line", { x1: 0, x2: W, y1: y(0), y2: y(0), stroke: cssVar("--grid") }));
  const pts = shape.map((v, i) => `${(i / (n - 1)) * W},${y(v)}`).join(" ");
  svg.append(s("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
  svg.append(s("text", { x: 2, y: H - 1, fill: cssVar("--muted"), "font-size": 9 }, "beat"));
  svg.append(s("text", { x: W - 2, y: H - 1, "text-anchor": "end", fill: cssVar("--muted"), "font-size": 9 }, "next beat"));
  return svg;
}
