// Thin wrappers around the server API. State-changing requests carry the X-Track-Anatomy
// header, which the server requires (a cross-site page can't add custom headers).

const MARK = { "X-Track-Anatomy": "1" };

async function json(r) {
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

const post = (url, body) => fetch(url, {
  method: "POST",
  headers: { ...MARK, "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}),
}).then(json);

export const list = () => fetch("/api/tracks").then(json);
export const status = (id) => fetch(`/api/tracks/${id}/status`).then(json);
export const analysis = (id) => fetch(`/api/tracks/${id}/analysis`).then(json);
export const guide = (id) => fetch(`/api/tracks/${id}/guide`).then((r) => { if (!r.ok) throw new Error(r.status); return r.text(); });
export const remove = (id) => fetch(`/api/tracks/${id}`, { method: "DELETE", headers: MARK }).then(json);
export const retry = (id, fresh = false) => post(`/api/tracks/${id}/retry${fresh ? "?fresh=1" : ""}`);
export const cancel = (id) => post(`/api/tracks/${id}/cancel`);
export const rename = (id, title) => post(`/api/tracks/${id}/rename`, { title });
export const settings = () => fetch("/api/settings").then(json);
export const saveSettings = (s) => post("/api/settings", s);
export const saveProject = (id, dir) => post(`/api/tracks/${id}/save_project`, { ableton_dir: dir });
export const reanalyze = (id, opts) => post(`/api/tracks/${id}/reanalyze`, opts);
export const refreshStale = () => post("/api/refresh_stale");

export function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/tracks");
    xhr.setRequestHeader("X-Track-Anatomy", "1");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch { }
      if (xhr.status < 300) resolve(body);
      else reject(new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed: the server didn't respond"));
    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}
