// App shell: library, processing view, workspace; routing, shortcuts, uploads.
import { h, $, esc, fmtTime, Grid, toast, groupColor, GROUPS, fill } from "./util.js";
import * as api from "./api.js";
import { Player } from "./player.js";
import { Timeline } from "./timeline.js";
import { Detail } from "./detail.js";
import { openSettings } from "./settings.js";

const appEl = $("#app");
const topTrack = $("#topTrack");
const player = new Player();

const app = {
  id: null, A: null, grid: null, sel: null, player,
  timeline: null, detail: null,
  select(id) {
    this.sel = id;
    this.detail.tab = "element";
    this.detail.render();
    this.timeline.syncHeads();
    this.timeline.draw();
  },
  selectSection(i, play = false) {
    const s = this.A.sections[i];
    this.loopBars(s.b0, s.b1, play);
  },
  loopBars(b0, b1, play = false) {
    const m = this.grid.meter;
    const t0 = Math.max(0, this.grid.time(b0 * m));
    const t1 = Math.min(this.A.track.duration, this.grid.time(b1 * m));
    player.setLoop({ t0, t1 });
    if (play) player.play(t0); else player.seek(t0);
    const T = this.timeline;
    if (b0 * m < T.start || b1 * m > T.endBeat) T.showRange(b0 * m, b1 * m);
    updateTransport();
  },
  goProcessing() { route(`#/t/${this.id}`, true); },
  onTick(t, beat) {
    if (posEl) posEl.innerHTML = `${esc(app.grid.barBeatLabel(Math.max(0, beat)))}<span>${fmtTime(t, true)} / ${fmtTime(app.A.track.duration)}</span>`;
    app.detail?.onTick(t, beat);
    if (app._lastStart !== app.timeline.start || app._lastPpb !== app.timeline.ppb) {
      app._lastStart = app.timeline.start;
      app._lastPpb = app.timeline.ppb;
      app.detail?.onView();
    }
  },
};
window.__app = app;          // handy for debugging from the console
let posEl = null;
let pollTimer = null;
let transportEls = {};
let renderSeq = 0;           // bumped on every navigation; stale async work checks it and bails
let unsubPlayer = null;

// ------------------------------------------------------------------ routing
function route(hash, force = false) {
  if (location.hash !== hash) location.hash = hash;      // hashchange -> render()
  else if (force) render();
}
addEventListener("hashchange", render);

function teardown() {
  clearTimeout(pollTimer);
  pollTimer = null;
  app.timeline?.destroy();
  app.timeline = null;
  app.detail?.destroy();
  app.detail = null;
  player.stop();
  unsubPlayer?.();
  unsubPlayer = null;
  $("#trackMenu")?.remove();
  app.A = app.grid = app.sel = null;
  app.id = null;
  posEl = null;
  transportEls = {};
  $("#helpMenu")?.remove();
}

async function render() {
  const seq = ++renderSeq;
  teardown();
  const m = location.hash.match(/^#\/t\/([a-z0-9-]+)/);
  if (!m) return renderLibrary(seq);
  const id = m[1];
  let st;
  try { st = await api.status(id); } catch { toast("That track isn't in the library any more."); return route("#/"); }
  if (seq !== renderSeq) return;
  if (st.state === "done") return renderWorkspace(id, seq, st);
  renderProcessing(id, st, seq);
}

// ------------------------------------------------------------------ library
async function renderLibrary(seq) {
  document.title = "Track Anatomy";
  topTrack.replaceChildren();
  const list = h("div", { class: "track-list" }, h("div", { class: "empty" }, "Loading…"));
  const search = h("input", { type: "search", class: "search", placeholder: "Search tracks", "aria-label": "Search tracks" });
  const sort = h("select", { class: "select", "aria-label": "Sort tracks" },
    h("option", { value: "new" }, "Newest first"), h("option", { value: "title" }, "Title A–Z"), h("option", { value: "bpm" }, "Tempo"));
  sort.value = localStore("sort") || "new";
  let items = [];
  const drawList = () => {
    const q = search.value.trim().toLowerCase();
    let shown = items.filter((t) => !q || `${t.title} ${t.artist} ${t.key || ""}`.toLowerCase().includes(q));
    if (sort.value === "title") shown = [...shown].sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    if (sort.value === "bpm") shown = [...shown].sort((a, b) => (a.bpm || 999) - (b.bpm || 999));
    fill(list, shown.length ? shown.map(trackCard) : h("div", { class: "empty" }, q ? `No tracks match "${q}".` : "Nothing here yet."));
  };
  search.oninput = drawList;
  sort.onchange = () => { localStore("sort", sort.value); drawList(); };
  const drop = h("label", { class: "dropzone", for: "fileInput" },
    h("div", {},
      h("div", { html: LOGO_BIG }),
      h("div", { class: "big" }, "Drop a track here"),
      h("div", { class: "muted" }, "or click to choose · mp3, wav, flac, aiff, m4a, ogg"),
      h("div", { class: "muted", style: { marginTop: "10px", fontSize: "12.5px" } }, "About 2–3 minutes per track with an NVIDIA GPU. Everything runs locally.")));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  const hero = h("div", { class: "hero" },
    h("div", {},
      h("div", { class: "section-title" }, "Reverse-engineer a track"),
      h("h1", {}, "See every part of a song, then rebuild it in Ableton."),
      h("p", {}, "Track Anatomy separates a mix into drums (kick, snare, hats, cymbals), bass, pads, leads, arps, vocals and FX, then transcribes them. Everything runs on your machine."),
      h("ul", {},
        h("li", {}, "Arrangement map: what plays in every bar, with sections, chords and risers"),
        h("li", {}, "Drum patterns as step grids, with swing and feel"),
        h("li", {}, "Notes for each part in a piano roll, exported as MIDI"),
        h("li", {}, "Sound-design hints: waveform, envelope, width, sidechain, delay, reverb, filter moves"),
        h("li", {}, "One click to an Ableton Live Set with every part laid out"))),
    drop);
  const head = h("div", { class: "lib-head" },
    h("h1", {}, "Library ", h("span", { class: "count" })),
    h("div", { class: "grow" }), search, sort);
  const staleBar = h("div", { class: "notice", hidden: true });
  const inner = h("div", { class: "library-inner" }, head, staleBar, list);
  appEl.replaceChildren(h("div", { class: "library" }, inner));
  const refresh = async () => {
    try { items = await api.list(); } catch { return true; }
    if (seq !== renderSeq) return false;
    const empty = !items.length;
    if (empty && !inner.contains(hero)) inner.replaceChildren(hero);
    if (!empty && !inner.contains(head)) inner.replaceChildren(head, staleBar, list);
    $(".count", head).textContent = `${items.length}`;
    const nStale = items.filter((t) => t.stale && t.state === "done").length;
    staleBar.hidden = !nStale;
    if (nStale) {
      const b = h("button", { class: "btn btn-sm" }, "Refresh them");
      b.onclick = async () => { b.disabled = true; try { const r = await api.refreshStale(); toast(`Queued ${r.queued} tracks`); loop(); } catch (e) { toast(e.message); } };
      fill(staleBar, h("span", {}, `${nStale} ${nStale === 1 ? "track was" : "tracks were"} analysed with an older version. Re-running takes under a minute each (the stems are reused).`), b);
    }
    if (!empty) drawList();
    return items.some((t) => t.state === "running" || t.state === "queued");
  };
  const loop = async () => {
    clearTimeout(pollTimer);
    const busy = await refresh();
    if (seq === renderSeq) pollTimer = setTimeout(loop, busy ? 2000 : 8000);
  };
  loop();
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** Mini arrangement silhouette: one block per section, height = energy, peaks highlighted. */
function arrangementStrip(sections, bars) {
  const W = 300, H = 34;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "strip");
  for (const [b0, b1, energy, name] of sections || []) {
    const x0 = (b0 / bars) * W, x1 = (b1 / bars) * W;
    const hh = 4 + Math.max(0, Math.min(1, energy)) * (H - 6);
    const r = document.createElementNS(SVG_NS, "rect");
    r.setAttribute("x", x0 + 0.5);
    r.setAttribute("y", H - hh);
    r.setAttribute("width", Math.max(0.5, x1 - x0 - 1));
    r.setAttribute("height", hh);
    r.setAttribute("rx", 1.5);
    if (energy >= 0.8) r.setAttribute("class", "peak");
    const tt = document.createElementNS(SVG_NS, "title");
    tt.textContent = `${name} · bars ${b0 + 1}–${b1}`;
    r.append(tt);
    svg.append(r);
  }
  return svg;
}

function trackCard(t) {
  const chips = [];
  if (t.bpm) chips.push(t.has_beat === false ? h("span", { class: "chip" }, "no clear beat") :
    h("span", { class: "chip" }, h("b", {}, `${Math.round(t.bpm * 10) / 10}`), "BPM"));
  if (t.analyzed) chips.push(t.key ? h("span", { class: "chip" }, h("b", {}, t.key)) : h("span", { class: "chip" }, "key unclear"));
  if (t.duration) chips.push(h("span", { class: "chip" }, `${t.bars ? `${t.bars} bars · ` : ""}${fmtTime(t.duration)}`));
  const del = h("button", { class: "icon-btn del", title: "Delete", "aria-label": `Delete ${t.title}`, html: ICONS.trash });
  del.onclick = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Delete "${t.title}" and its stems?`)) return;
    try { await api.remove(t.id); route("#/", true); } catch (err) { toast(err.message); }
  };
  let state = null;
  if (t.state === "error" || t.state === "cancelled") {
    const retry = t.input_error ? null : h("button", { class: "btn btn-sm" }, "Retry");
    if (retry) retry.onclick = async (e) => { e.preventDefault(); e.stopPropagation(); await api.retry(t.id); toast("Queued again"); };
    state = h("div", { class: "card-error" },
      h("span", { title: t.error || "" }, t.state === "cancelled" ? "Cancelled" : t.input_error ? t.error : "Analysis failed"),
      h("span", { class: "grow" }), retry);
  } else if (t.state !== "done") {
    const pct = Math.round((t.progress || 0) * 100);
    state = h("div", {}, h("div", { class: "progress" }, h("i", { style: { width: `${pct}%` } })),
      h("div", { class: "note", style: { marginTop: "4px" } },
        t.state === "queued" ? `Queued${t.queue_position ? ` · #${t.queue_position}` : ""}` : `Analysing · ${pct}%`));
  }
  const groups = (t.groups || []).map((g) => h("span", { class: "gdot", title: GROUPS[g]?.label || g, style: { background: groupColor(g) } }));
  return h("a", { class: `track-card state-${t.state}`, href: `#/t/${t.id}` },
    t.sections?.length ? arrangementStrip(t.sections, t.bars || 1) : h("div", { class: "strip strip-empty" }),
    h("div", { class: "t", title: t.title || "" }, t.title || t.id),
    h("div", { class: "a" }, t.artist || " "),
    chips.length || groups.length ? h("div", { class: "meta" }, chips, groups.length ? h("span", { class: "gdots" }, groups) : null) : null,
    state, del);
}

// ------------------------------------------------------------------ processing
function renderProcessing(id, st, seq) {
  const title = st.meta?.title || id;
  fill(topTrack, h("span", { class: "dim" }, title));
  const card = h("div", { class: "proc-card", role: "status", "aria-live": "polite" });
  appEl.replaceChildren(h("div", { class: "processing" }, card));
  const icon = (s) => s === "done" ? h("span", { class: "ic ok" }, "✓") :
    s === "skipped" ? h("span", { class: "ic muted", title: "already done (cached)" }, "↷") :
      s === "running" ? h("span", { class: "ic" }, h("span", { class: "spinner" })) :
        s === "error" ? h("span", { class: "ic bad" }, "✕") : h("span", { class: "ic muted" }, "○");
  const draw = (st) => {
    const pct = Math.round((st.progress || 0) * 100);
    const failed = st.state === "error" || st.state === "cancelled";
    document.title = `${title} · ${st.state === "error" ? "failed" : st.state === "cancelled" ? "cancelled" :
      st.state === "queued" ? "queued" : `${pct}%`}`;
    const elapsed = st.started && st.state === "running" ? ` · ${fmtTime(Math.max(0, Date.now() / 1000 - st.started))} elapsed` : "";
    const steps = (st.steps || []).map((s) => [
      h("li", { class: s.state }, icon(s.state), h("span", {}, s.label), h("span", { class: "num muted" }, s.secs != null ? `${s.secs}s` : "")),
      s.state === "running" && st.detail ? h("div", { class: "proc-detail" }, st.detail) : null]);
    const btn = (label, cls, fn) => { const b = h("button", { class: `btn ${cls}` }, label); b.onclick = fn; return b; };
    fill(card,
      h("div", { class: "section-title" }, st.state === "error" ? "Analysis failed" : st.state === "cancelled" ? "Cancelled" :
        st.state === "queued" ? "Waiting in the queue" : "Analysing"),
      h("h2", {}, title),
      h("div", { class: "muted" }, st.state === "queued" ? `Position ${st.queue_position || 1} in the queue` :
        failed ? "" : `${pct}%${elapsed} · you can leave this page; it keeps going`),
      failed ? null : h("div", { class: "progress", style: { height: "6px", marginTop: "14px" } }, h("i", { style: { width: `${pct}%` } })),
      st.steps?.length ? h("ul", { class: "proc-steps" }, steps) : null,
      st.error ? h("div", { class: "err-box" }, st.error) : null,
      h("div", { class: "btn-row" },
        failed && !st.input_error ? btn("Retry", "btn-primary", async () => { await api.retry(id); poll(); }) : null,
        failed && !st.input_error ? btn("Re-run from scratch", "", async () => { await api.retry(id, true); poll(); }) : null,
        st.input_error ? btn("Delete this track", "btn-primary", async () => { await api.remove(id); route("#/"); }) : null,
        failed && st.has_analysis ? btn("Open the last results", "", () => renderWorkspace(id, seq)) : null,
        !failed ? btn("Cancel", "", async () => { try { await api.cancel(id); } catch (e) { toast(e.message); } poll(); }) : null,
        btn("Back to library", "btn-ghost", () => route("#/"))));
  };
  draw(st);
  const poll = async () => {
    clearTimeout(pollTimer);
    if (seq !== renderSeq) return;
    try {
      const s2 = await api.status(id);
      if (seq !== renderSeq) return;
      if (s2.state === "done") return renderWorkspace(id, seq, s2);
      draw(s2);
      if (s2.state === "error" || s2.state === "cancelled") return;
    } catch { }
    if (seq === renderSeq) pollTimer = setTimeout(poll, 1000);
  };
  pollTimer = setTimeout(poll, 1000);
}

// ------------------------------------------------------------------ workspace
async function renderWorkspace(id, seq, st = {}) {
  clearTimeout(pollTimer);
  appEl.replaceChildren(h("div", { class: "processing" }, h("div", { class: "spinner" })));
  let A;
  try { A = await api.analysis(id); } catch (e) { toast(`Couldn't load the analysis: ${e.message}`); return; }
  if (seq !== renderSeq) return;
  app.id = id;
  app.A = A;
  app.grid = new Grid(A.grid.beats, A.grid.first_beat, A.global.meter);
  app.sel = null;
  document.title = `${A.track.title} · Track Anatomy`;
  renderTopbar(A, id);

  const transport = buildTransport();
  const arr = h("div", { style: { minHeight: 0, display: "grid" } });
  const splitter = h("div", { class: "splitter", title: "Drag to resize", role: "separator", "aria-orientation": "horizontal" });
  const det = h("div", { style: { minHeight: 0, display: "grid" } });
  // first grid row: an empty div (zero height) unless the analysis predates the current pipeline
  const notice = h("div");
  if (st.stale) {
    const b = h("button", { class: "btn btn-sm" }, "Re-run the analysis");
    b.onclick = async () => { b.disabled = true; try { await api.reanalyze(id, {}); app.goProcessing(); } catch (e) { toast(e.message); b.disabled = false; } };
    notice.className = "notice ws-notice";
    notice.append(h("span", {}, "This track was analysed with an older version of Track Anatomy. Re-running takes under a minute (the stems are reused)."), b);
  }
  const ws = h("div", { class: "workspace" }, notice, transport, arr, splitter, det);
  appEl.replaceChildren(ws);

  let dh = localStore("detailH") || 46;
  ws.style.setProperty("--detail-h", `${dh}%`);
  splitter.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const r = ws.getBoundingClientRect();
    const move = (e2) => {
      dh = Math.min(80, Math.max(18, ((r.bottom - e2.clientY) / r.height) * 100));
      ws.style.setProperty("--detail-h", `${dh}%`);
    };
    const up = () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); localStore("detailH", dh); };
    addEventListener("mousemove", move);
    addEventListener("mouseup", up);
  });

  app.timeline = new Timeline(app);
  app.detail = new Detail(app);
  // deep links: #/t/<id>?tab=drums|guide|overview or ?sel=<element id>
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  if (q.get("sel") && A.elements.some((e) => e.id === q.get("sel"))) {
    app.sel = q.get("sel");
    app.detail.tab = "element";
  } else if (["overview", "drums", "guide"].includes(q.get("tab"))) {
    app.detail.tab = q.get("tab");
  }
  app.timeline.mount(arr);
  app.detail.mount(det);

  transportEls.loading.hidden = false;
  player.onError = (e) => toast(`Couldn't load the audio for playback: ${e.message}`, 6000);
  player.attach(A, id, app.grid)
    .then(() => { if (transportEls.loading) transportEls.loading.hidden = true; })
    .catch((e) => toast(`The audio didn't load: ${e.message}`));
  unsubPlayer = player.on(updateTransport);
  updateTransport();
}

function renderTopbar(A, id) {
  const g = A.global;
  const sw = A.drums?.swing;
  const titleEl = h("b", { class: "tt-title", title: "Click to rename" }, A.track.title);
  titleEl.onclick = () => renameTrack(id, A.track.title);
  const menuBtn = h("button", { class: "icon-btn", title: "Track actions", "aria-label": "Track actions", html: ICONS.dots });
  menuBtn.onclick = (ev) => { ev.stopPropagation(); trackMenu(menuBtn, id, A); };
  fill(topTrack,
    h("div", { class: "tt-name" }, titleEl, h("span", { class: "muted" }, A.track.artist || A.track.filename)),
    g.has_beat === false ? h("span", { class: "chip", title: "No steady pulse was found" }, "no clear beat") :
      h("span", { class: "chip" }, h("b", { class: "num" }, g.bpm.toFixed(g.bpm % 1 ? 2 : 0)), "BPM"),
    h("span", { class: "chip" }, h("b", {}, `${g.meter}/4`)),
    g.key.clear === false ? h("span", { class: "chip", title: "Not enough tonal material to call a key" }, "key unclear") :
      h("span", { class: "chip" }, h("b", {}, g.key.name)),
    sw && !sw.straight ? h("span", { class: "chip" }, "swing ", h("b", {}, `${Math.round(sw.percent)}%`)) : null,
    h("span", { class: "chip hide-sm" }, `${g.n_bars} bars · ${fmtTime(A.track.duration)}`),
    menuBtn);
}

async function renameTrack(id, current) {
  const title = prompt("Rename track", current);
  if (title == null || !title.trim() || title.trim() === current) return;
  try { await api.rename(id, title.trim()); toast("Renamed"); route(location.hash, true); } catch (e) { toast(e.message); }
}

function trackMenu(anchor, id, A) {
  const old = $("#trackMenu");
  if (old) { old.remove(); return; }
  const r = anchor.getBoundingClientRect();
  const item = (label, sub, fn) => {
    const b = h("button", {}, label, sub ? h("small", {}, sub) : null);
    b.onclick = () => { menu.remove(); fn(); };
    return b;
  };
  const menu = h("div", { class: "menu", id: "trackMenu", style: { position: "fixed", top: `${r.bottom + 6}px`, right: `${Math.max(8, innerWidth - r.right)}px` } },
    item("Rename…", null, () => renameTrack(id, A.track.title)),
    item("Re-run the analysis", "keeps the stems and your grid fixes · under a minute", async () => {
      await api.reanalyze(id, {});
      app.goProcessing();
    }),
    item("Re-run from scratch", "separates the stems again with the current model", async () => {
      if (!confirm("Separate the stems again from scratch? This takes a few minutes.")) return;
      await api.retry(id, true);
      app.goProcessing();
    }),
    h("hr"),
    item("Delete track", "removes the stems and analysis", async () => {
      if (!confirm(`Delete "${A.track.title}" and its stems?`)) return;
      await api.remove(id);
      route("#/");
    }));
  document.body.append(menu);
  setTimeout(() => addEventListener("click", function close(e) {
    if (!menu.contains(e.target)) { menu.remove(); removeEventListener("click", close); }
  }), 0);
}

function buildTransport() {
  const btn = (label, title, on) => { const b = h("button", { class: "icon-btn", title, "aria-label": title, html: label }); b.onclick = on; return b; };
  const play = btn(ICONS.play, "Play / pause (Space)", () => player.toggle());
  const back = btn(ICONS.back, "Back to start (Home)", () => player.seek(player.loop ? player.loop.t0 : 0));
  posEl = h("div", { class: "pos num", "aria-live": "off" }, "");
  const loop = h("button", { class: "btn btn-sm", title: "Loop (L): drag on the ruler or click a section to set one" }, ICON_TEXT("loop", "Loop"));
  loop.onclick = () => {
    if (player.loop) { app._lastLoop = player.loop; player.setLoop(null); }
    else if (app._lastLoop) player.setLoop(app._lastLoop);
    else toast("Drag across the bar ruler, or click a section, to set a loop");
    updateTransport();
  };
  const click = h("button", { class: "btn btn-sm", title: "Metronome on the detected grid (K)" }, ICON_TEXT("click", "Click"));
  click.onclick = () => { player.setClick(!player.click); updateTransport(); };
  const follow = h("button", { class: "btn btn-sm on", title: "Follow playhead (F)" }, "Follow");
  follow.onclick = () => { app.timeline.follow = !app.timeline.follow; updateTransport(); };
  const clear = h("button", { class: "btn btn-sm", title: "Clear all mutes and solos (Esc)" }, "Clear M/S");
  clear.onclick = () => { player.mutes.clear(); player.solos.clear(); player.restartIfPlaying(); };
  const zoomOut = btn(ICONS.minus, "Zoom out (-)", () => app.timeline.zoom(1 / 1.5));
  const zoomIn = btn(ICONS.plus, "Zoom in (+)", () => app.timeline.zoom(1.5));
  const fit = h("button", { class: "btn btn-sm", title: "Show the whole song (0)" }, "Fit");
  fit.onclick = () => app.timeline.fit();
  const vol = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: player.volume, "aria-label": "Volume" });
  vol.oninput = () => player.setVolume(+vol.value);
  const loading = h("span", { class: "loading-pill" }, h("span", { class: "spinner" }), "loading audio");
  const solos = h("span", { class: "note solo-note" });
  const help = h("button", { class: "icon-btn", title: "Keyboard shortcuts (?)", "aria-label": "Keyboard shortcuts" }, "?");
  help.onclick = (ev) => { ev.stopPropagation(); toggleHelp(help); };
  transportEls = { play, loop, click, follow, loading, solos, clear, help };
  return h("div", { class: "transport" },
    h("div", { class: "tgroup" }, play, back, posEl),
    h("div", { class: "tgroup" }, loop, click, follow),
    h("div", { class: "tgroup" }, zoomOut, zoomIn, fit),
    h("div", { class: "tgroup" }, clear, solos),
    h("span", { class: "grow" }),
    h("div", { class: "tgroup" }, loading, h("label", { class: "small" }, "Vol", vol), help));
}

const SHORTCUTS = [
  ["Space", "play / pause"], ["← →", "previous / next bar"], ["Home", "back to start (or loop start)"],
  ["L", "loop on / off"], ["K", "metronome on the detected grid"], ["F", "follow playhead"],
  ["S / M", "solo / mute the selected part (Shift+S adds to the solo)"], ["Esc", "clear all solos and mutes"],
  ["+ − 0", "zoom in / out / whole song"], ["Ctrl + wheel", "zoom at the mouse"], ["Wheel on ruler", "scroll the song"],
  ["Drag on ruler", "set a loop"], ["Click a section", "loop it (double-click plays)"], ["Click a note or chord", "hear it"],
];
function toggleHelp(anchor) {
  const old = $("#helpMenu");
  if (old) { old.remove(); return; }
  const r = anchor.getBoundingClientRect();
  const menu = h("div", { class: "menu", id: "helpMenu", style: { position: "fixed", top: `${r.bottom + 6}px`, right: `${Math.max(8, innerWidth - r.right)}px` } },
    h("div", { class: "section-title", style: { padding: "4px 8px 6px" } }, "Shortcuts"),
    h("table", { class: "t" }, h("tbody", {}, SHORTCUTS.map(([k, d]) => h("tr", {}, h("td", {}, h("code", {}, k)), h("td", { class: "dim" }, d))))));
  document.body.append(menu);
  setTimeout(() => addEventListener("click", function close(e) { if (!menu.contains(e.target)) { menu.remove(); removeEventListener("click", close); } }), 0);
}

function updateTransport() {
  const T = transportEls;
  if (!T.play || !app.A) return;
  T.play.innerHTML = player.playing ? ICONS.pause : ICONS.play;
  T.play.setAttribute("aria-pressed", String(player.playing));
  T.loop.classList.toggle("on", !!player.loop);
  T.click.classList.toggle("on", player.click);
  T.follow.classList.toggle("on", !!app.timeline?.follow);
  const names = (set) => [...set].map((i) => app.A.elements.find((e) => e.id === i)?.name).filter(Boolean);
  T.solos.textContent = player.solos.size ? `Solo: ${names(player.solos).join(", ")}` : player.mutes.size ? `Muted: ${names(player.mutes).join(", ")}` : "";
  T.clear.hidden = !player.solos.size && !player.mutes.size;
  T.loop.title = player.loop ? `Looping ${fmtTime(player.loop.t0, true)}–${fmtTime(player.loop.t1, true)} (L to toggle)` : "Loop (L)";
  app.timeline?.syncHeads();
}

const ICONS = {
  play: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M7 5v14l12-7z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor"/></svg>',
  back: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 5h2v14H6zm3 7 10-7v14z" fill="currentColor"/></svg>',
  minus: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 12h14" stroke="currentColor" stroke-width="2"/></svg>',
  plus: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 12h14M12 5v14" stroke="currentColor" stroke-width="2"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 7h12M9 7V5h6v2m-8 0 1 12h8l1-12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  dots: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="5" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="19" cy="12" r="1.8" fill="currentColor"/></svg>',
};
const LOGO_BIG = '<svg viewBox="0 0 48 48" width="44" height="44" aria-hidden="true"><rect x="6" y="18" width="5" height="14" rx="2.5" fill="var(--g-bass)"/><rect x="15" y="10" width="5" height="28" rx="2.5" fill="var(--g-drums)"/><rect x="24" y="15" width="5" height="18" rx="2.5" fill="var(--g-harmony)"/><rect x="33" y="8" width="5" height="32" rx="2.5" fill="var(--g-melody)"/><rect x="42" y="19" width="5" height="10" rx="2.5" fill="var(--g-vocals)"/></svg>';

function ICON_TEXT(kind, text) {
  const svg = kind === "loop" ? '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M17 2l3 3-3 3M4 11V9a4 4 0 0 1 4-4h12M7 22l-3-3 3-3m13-3v2a4 4 0 0 1-4 4H4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    : '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M8 21h8l-3-17h-2zM12 14l5-7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  return h("span", { class: "row", style: { gap: "6px" }, html: svg + esc(text) });
}

function localStore(k, v) {
  try {
    if (v === undefined) return JSON.parse(localStorage.getItem(`ta.${k}`));
    localStorage.setItem(`ta.${k}`, JSON.stringify(v));
  } catch { return null; }
}

// ------------------------------------------------------------------ keyboard
addEventListener("keydown", (e) => {
  const openMenu = $("#trackMenu") || $("#helpMenu");
  if (e.key === "Escape" && openMenu) { openMenu.remove(); return; }
  if (!app.A || !app.timeline || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, [contenteditable], dialog")) return;
  const k = e.key;
  if (k === " ") { e.preventDefault(); player.toggle(); }
  else if (k === "Home") player.seek(player.loop ? player.loop.t0 : 0);
  else if (k === "l" || k === "L") transportEls.loop.click();
  else if (k === "k" || k === "K") transportEls.click.click();
  else if (k === "f" || k === "F") transportEls.follow.click();
  else if (k === "+" || k === "=") app.timeline.zoom(1.5);
  else if (k === "-") app.timeline.zoom(1 / 1.5);
  else if (k === "0") app.timeline.fit();
  else if (k === "?") toggleHelp(transportEls.help);
  else if (k === "Escape") { player.mutes.clear(); player.solos.clear(); player.restartIfPlaying(); }
  else if ((k === "s" || k === "S") && app.sel) { const on = !player.solos.has(app.sel); player.toggleSolo([app.sel], on, on && !e.shiftKey); }
  else if ((k === "m" || k === "M") && app.sel) player.toggleMute([app.sel], !player.mutes.has(app.sel));
  else if (k === "ArrowRight" || k === "ArrowLeft") {
    e.preventDefault();
    const bar = Math.floor(app.grid.beat(player.position()) / app.grid.meter) + (k === "ArrowRight" ? 1 : -1);
    player.seek(Math.max(0, app.grid.time(Math.max(0, bar) * app.grid.meter)));
  }
});

// ------------------------------------------------------------------ upload
async function uploadFiles(files) {
  files = [...files].filter((f) => f.size > 0);
  if (!files.length) return;
  let first = null;
  for (const [i, f] of files.entries()) {
    const n = files.length > 1 ? ` (${i + 1}/${files.length})` : "";
    toast(`Uploading ${f.name}${n}…`, 60000);
    try {
      const r = await api.upload(f, (p) => toast(`Uploading ${f.name}${n}… ${Math.round(p * 100)}%`, 60000));
      first ||= r.id;
      toast(r.existing ? `${f.name} was already analysed` : `${f.name} is queued for analysis`);
    } catch (e) {
      toast(`${f.name}: ${e.message}`, 7000);
    }
  }
  if (first) route(`#/t/${first}`);
}
$("#fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
$("#homeBtn").addEventListener("click", () => route("#/", true));
$("#settingsBtn").addEventListener("click", () => openSettings());
let dragDepth = 0;
addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; $("#dropOverlay").classList.add("on"); } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropOverlay").classList.remove("on"); } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#dropOverlay").classList.remove("on");
  if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
});

render();
