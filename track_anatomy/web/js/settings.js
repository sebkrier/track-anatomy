// Settings & about dialog.
import { h, toast } from "./util.js";
import * as api from "./api.js";

const fmtBytes = (n) => (n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`);

function refreshRow(n, dlg) {
  const b = h("button", { class: "btn btn-sm" }, `Refresh ${n} older ${n === 1 ? "analysis" : "analyses"}`);
  b.onclick = async () => {
    try {
      const r = await api.refreshStale();
      toast(`Queued ${r.queued} tracks (stems are reused, so each takes under a minute)`);
      dlg.close();
      location.hash = "#/";
    } catch (e) { toast(e.message); }
  };
  return h("div", { class: "row", style: { gap: "10px", marginTop: "8px" } },
    b, h("span", { class: "note" }, "Made with an older version: re-running picks up the analysis improvements."));
}

export async function openSettings() {
  let s;
  try { s = await api.settings(); } catch (e) { toast(`Couldn't load settings: ${e.message}`); return; }
  const dlg = h("dialog", { class: "modal", "aria-labelledby": "settingsTitle" });
  const folder = h("input", { type: "text", class: "path-input", value: s.ableton_dir || "", spellcheck: "false" });
  const model = h("select", { class: "select" },
    Object.entries(s.stem_models || {}).map(([id, label]) => h("option", { value: id }, label)));
  model.value = s.stem_model;
  const save = h("button", { class: "btn btn-primary" }, "Save");
  const close = h("button", { class: "btn" }, "Close");
  close.onclick = () => dlg.close();
  save.onclick = async () => {
    try {
      await api.saveSettings({ ableton_dir: folder.value, stem_model: model.value });
      toast("Settings saved");
      dlg.close();
    } catch (e) { toast(`Couldn't save: ${e.message}`); }
  };
  dlg.append(
    h("div", { class: "modal-head" }, h("h2", { id: "settingsTitle" }, "Settings"), h("span", { class: "muted" }, `Track Anatomy ${s.version}`)),
    h("section", {},
      h("h3", {}, "Ableton export folder"),
      h("p", { class: "note" }, "“Save Ableton project” writes a project folder here (the .als, aligned stems, MIDI and the guide)."),
      folder,
      s.als_available ? null : h("p", { class: "warn-note" }, "Ableton Live's template wasn't found on this machine, so exports contain stems + MIDI without a .als. Set TRACK_ANATOMY_ABLETON_TEMPLATE to Live's DefaultLiveSet.als to enable it.")),
    h("section", {},
      h("h3", {}, "Separation model"),
      h("p", { class: "note" }, "Used for new tracks and for “Re-run from scratch”. BS-RoFormer SW sounds clearly better; its license is undocumented. Demucs is MIT-licensed."),
      model),
    h("section", {},
      h("h3", {}, "Storage"),
      h("p", { class: "note" }, `${s.n_tracks} tracks use ${fmtBytes(s.disk_bytes || 0)} in `, h("code", {}, s.data_dir), "."),
      h("p", { class: "note" }, `Models run on: ${s.device}.`),
      s.n_stale ? refreshRow(s.n_stale, dlg) : null),
    h("section", {},
      h("h3", {}, "About & licenses"),
      h("p", { class: "note" }, "Track Anatomy runs entirely on your machine. It relies on open models: python-audio-separator (BS-RoFormer SW, Demucs, MDX23C DrumSep), beat_this (CPJKU), All-In-One (Harmonix), Spotify's basic-pitch and lv-chordia."),
      h("p", { class: "note" }, h("b", {}, "The drum-separation weights are licensed for non-commercial use only"), " (CC BY-NC). Use Track Anatomy for personal study, and only on music you have the right to use.")),
    h("div", { class: "modal-foot" }, close, save));
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  dlg.showModal();
}
