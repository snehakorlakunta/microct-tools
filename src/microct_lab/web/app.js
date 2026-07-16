// microCT Segmentation Lab — single-page app (framework-free)
const API = "/api";

/* ----------------------------------------------------------------- helpers */
async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (!r.ok) throw new Error((await r.text()) || ("HTTP " + r.status));
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}
const $ = (s, r = document) => r.querySelector(s);
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
const fmtVol = v => v == null ? "—" : Number(v).toFixed(4) + " mm³";
const fmtInt = n => n == null ? "—" : Number(n).toLocaleString();
const fmtUm = v => v == null ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }) + " µm³";
const fmtDur = s => s == null ? "—" : s < 90 ? s.toFixed(0) + " s" : (s / 60).toFixed(1) + " min";
const fmtDate = d => d ? new Date(d).toLocaleString([], { year: "2-digit", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
const fmtBytes = b => { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; b = Number(b); while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return b.toFixed(i ? 1 : 0) + " " + u[i]; };
const vox = v => v == null ? "—" : Number(v).toFixed(2) + " µm";
function toast(msg, kind = "") {
  const d = h(`<div class="toast ${kind}">${esc(msg)}</div>`);
  $("#toasts").appendChild(d); setTimeout(() => d.remove(), 4200);
}
function badge(s) { return `<span class="badge ${s}"><span class="dot"></span>${s}</span>`; }
function debounce(fn, ms = 280) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const ENV_FIELDS = [
  ["host", "Machine"], ["os", "OS"], ["platform", "Platform"], ["cpu", "CPU"],
  ["physical_cores", "Physical cores"], ["logical_cores", "Logical cores"], ["ram_total_gb", "RAM (GB)"],
  ["device", "Device"], ["gpu", "GPU"], ["gpu_mem_total_gb", "GPU VRAM (GB)"], ["cuda_version", "CUDA"],
  ["torch_version", "torch"], ["nnunetv2_version", "nnU-Net"],
  ["peak_ram_mb", "Peak RAM (MB)"], ["peak_gpu_mb", "Peak VRAM (MB)"],
  ["convert_seconds", "Convert (s)"], ["predict_seconds", "Predict (s)"], ["total_seconds", "Total (s)"],
];
function kvGrid(pairs) {
  return `<div class="mcard" style="padding:12px 14px"><div class="kv">${pairs.map(p =>
    `<div class="k">${esc(p[0])}</div><div class="v">${p[1] == null || p[1] === "" ? "—" : esc(p[1])}</div>`).join("")}</div></div>`;
}

/* ------------------------------------------------------------------- state */
let VOCAB = [];            // failure-mode vocabulary
let pollTimer = null;
const clearPoll = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (window.__cine) { clearInterval(window.__cine); window.__cine = null; }
};

/* ------------------------------------------------------------------ router */
const PAGES = {
  overview: { title: "Overview", sub: "Your segmentation registry at a glance", fn: renderOverview },
  datasets: { title: "Datasets", sub: "Catalog of microCT scans", fn: renderDatasets },
  models: { title: "Models", sub: "Trained models & versions", fn: renderModels },
  runs: { title: "Runs", sub: "Processing history", fn: renderRuns },
  insights: { title: "QC & Insights", sub: "Failure modes and review status", fn: renderInsights },
  run: { title: "Run", sub: "", fn: renderRunDetail },
  compare: { title: "Compare runs", sub: "Side-by-side results", fn: renderCompare },
};
function parseHash() { const raw = (location.hash || "#/overview").replace(/^#\//, ""); const [view, arg] = raw.split("/"); return { view: view || "overview", arg }; }
async function route() {
  clearPoll();
  const { view, arg } = parseHash();
  const page = PAGES[view] || PAGES.overview;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  $("#pageTitle").textContent = page.title;
  $("#pageSub").textContent = page.sub;
  $("#pageActions").innerHTML = "";
  $("#content").innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  try { await page.fn(arg); } catch (e) { $("#content").innerHTML = `<div class="empty"><div class="big">⚠</div>${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);

/* ---------------------------------------------------------------- overview */
async function renderOverview() {
  const s = await api("/stats");
  const st = s.runs_by_status || {};
  const tiles = [
    ["Datasets", s.datasets, "#/datasets", "var(--accent)"],
    ["Models", `${s.models}`, "#/models", "var(--purple)", `${s.model_families} families`],
    ["Runs", s.runs, "#/runs", "var(--cyan)"],
    ["Succeeded", st.succeeded || 0, "#/runs", "var(--green)"],
    ["Running / Queued", (st.running || 0) + (st.queued || 0), "#/runs", "var(--amber)"],
    ["Failed", st.failed || 0, "#/runs", "var(--red)"],
  ];
  const recent = (s.recent_runs || []).map(r => `
    <tr onclick="location.hash='#/run/${r.id}'">
      <td>#${r.id}</td><td>${esc(r.dataset || "—")}</td>
      <td>${esc(r.model || "—")} <span class="ver">${esc(r.version || "")}</span></td>
      <td>${badge(r.status)}</td><td class="num">${fmtVol(r.roi_mm3)}</td>
      <td class="muted">${fmtDate(r.created_at)}</td></tr>`).join("");
  $("#content").innerHTML = `
    <div class="tiles">${tiles.map(t => `
      <a class="tile" href="${t[2]}" style="display:block">
        <div class="accentbar" style="background:${t[3]}"></div>
        <div class="k">${t[0]}</div><div class="v">${t[1]}</div>
        <div class="foot">${t[4] || ""}</div></a>`).join("")}</div>
    <div class="panel"><div class="phead"><h3>Recent runs</h3><a class="btn sm ghost" href="#/runs">View all</a></div>
      <table class="tbl"><thead><tr><th>Run</th><th>Dataset</th><th>Model</th><th>Status</th>
        <th class="right">ROI volume</th><th>Created</th></tr></thead>
        <tbody>${recent || `<tr><td colspan="6" class="muted" style="padding:22px">No runs yet — register a model, ingest datasets, then start a run.</td></tr>`}</tbody></table></div>`;
  // live refresh if anything active
  if ((st.running || 0) + (st.queued || 0) > 0) pollTimer = setInterval(() => { if (parseHash().view === "overview") renderOverview(); }, 4000);
}

/* ---------------------------------------------------------------- datasets */
let dsFilters = { q: "", study: "", scanner: "", sort: "created_at", order: "desc" };
async function renderDatasets() {
  $("#pageActions").innerHTML = `<button class="btn" id="ingestBtn">⟳ Ingest datasets</button>`;
  $("#ingestBtn").onclick = doIngest;
  const facets = await api("/datasets/facets").catch(() => ({ studies: [], scanners: [] }));
  const bar = h(`<div class="toolbar">
    <div class="search"><span class="mag">⌕</span><input class="input" id="q" placeholder="Search datasets…" value="${esc(dsFilters.q)}"></div>
    <select class="input" id="study"><option value="">All studies</option>${facets.studies.map(s => `<option ${s === dsFilters.study ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select class="input" id="scanner"><option value="">All scanners</option>${facets.scanners.map(s => `<option ${s === dsFilters.scanner ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <div class="grow"></div>
    <select class="input" id="sort">
      ${[["created_at", "Newest"], ["name", "Name"], ["voxel_size_um", "Voxel size"], ["slices", "Slices"], ["scan_date", "Scan date"]].map(o => `<option value="${o[0]}" ${o[0] === dsFilters.sort ? "selected" : ""}>${o[1]}</option>`).join("")}
    </select>
    <button class="btn sm" id="order">${dsFilters.order === "desc" ? "↓" : "↑"}</button></div>`);
  const grid = h(`<div class="grid" id="grid"></div>`);
  $("#content").innerHTML = ""; $("#content").append(bar, grid);

  const reload = async () => {
    const p = new URLSearchParams(dsFilters).toString();
    const rows = await api("/datasets?" + p);
    grid.innerHTML = rows.length ? rows.map(dsCard).join("")
      : `<div class="empty" style="grid-column:1/-1"><div class="big">▦</div>No datasets. Set <b>MICROCT_DATA_ROOT</b> and click <b>Ingest datasets</b>.</div>`;
    grid.querySelectorAll("[data-ds]").forEach(c => c.onclick = () => openDataset(+c.dataset.ds));
  };
  $("#q").oninput = debounce(e => { dsFilters.q = e.target.value; reload(); });
  $("#study").onchange = e => { dsFilters.study = e.target.value; reload(); };
  $("#scanner").onchange = e => { dsFilters.scanner = e.target.value; reload(); };
  $("#sort").onchange = e => { dsFilters.sort = e.target.value; reload(); };
  $("#order").onclick = () => { dsFilters.order = dsFilters.order === "desc" ? "asc" : "desc"; $("#order").textContent = dsFilters.order === "desc" ? "↓" : "↑"; reload(); };
  await reload();
}
function dsCard(d) {
  const thumb = d.thumbnail ? `style="background-image:url('/api/datasets/${d.id}/thumbnail')"` : "";
  return `<div class="dscard" data-ds="${d.id}">
    <div class="thumb" ${thumb}>${d.thumbnail ? "" : `<div class="void">no preview</div>`}</div>
    <div class="body">
      <div class="nm">${d.flagged ? "★ " : ""}${esc(d.name)} ${d.run_count ? `<span class="chip accent">${d.run_count} run${d.run_count > 1 ? "s" : ""}</span>` : ""}</div>
      <div class="meta">
        <span>${esc(d.study || "—")}</span>
        <span class="mono">${vox(d.voxel_size_um)}</span>
        <span class="mono">${d.width ? `${d.width}×${d.height}×${d.slices || "?"}` : (d.slices || "?") + " sl"}</span>
      </div>
      <div class="meta"><span>${esc(d.scanner || "")}</span><span>${esc(d.scan_date || "")}</span></div>
    </div></div>`;
}
async function doIngest() {
  toast("Scanning data root…");
  try { const r = await api("/ingest", { method: "POST", body: JSON.stringify({}) });
    toast(`Ingest: +${r.created.length} new, ${r.updated.length} updated`, "ok"); renderDatasets();
  } catch (e) { toast("Ingest failed: " + e.message, "err"); }
}

/* --------------------------------------------------------- dataset drawer */
async function openDataset(id) {
  const d = await api("/datasets/" + id);
  const runs = await api("/runs?dataset_id=" + id);
  const log = d.log || {};
  const metaRows = [
    ["Scanner", d.scanner], ["Voxel size", vox(d.voxel_size_um)],
    ["Dimensions", d.width ? `${d.width} × ${d.height} × ${d.slices}` : d.slices],
    ["Bit depth", d.bit_depth], ["Source", `${d.source_voltage_kv || "?"} kV / ${d.source_current_ua || "?"} µA`],
    ["Filter", d.filter], ["Scan date", d.scan_date], ["Study", d.study],
    ["Size", fmtBytes(d.size_bytes)], ["Path", `<span class="mono" style="font-size:11px">${esc(d.slices_path)}</span>`],
  ];
  const runsTbl = runs.length ? `<table class="tbl"><thead><tr><th>Run</th><th>Model</th><th>Status</th><th class="right">ROI</th><th>QC</th></tr></thead><tbody>
    ${runs.map(r => `<tr onclick="location.hash='#/run/${r.id}'"><td>#${r.id}</td>
      <td>${esc(r.model_name || "")} <span class="ver">${esc(r.model_version || "")}</span></td>
      <td>${badge(r.status)}</td><td class="num">${fmtVol(r.roi_mm3)}</td>
      <td>${qcPill(r.qc_status)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="muted" style="padding:14px">No runs yet for this dataset.</div>`;
  const m = modal(esc(d.name), `
    <div class="wrap" style="margin-bottom:6px">${d.thumbnail ? `<img src="/api/datasets/${id}/thumbnail" style="width:100%;max-height:200px;object-fit:contain;border-radius:10px;border:1px solid var(--border);background:#05070a">` : ""}</div>
    <div class="mcard"><div class="kv">${metaRows.map(r => `<div class="k">${r[0]}</div><div class="v">${r[1] ?? "—"}</div>`).join("")}</div></div>
    <div class="field"><label>Tags (comma-separated)</label><input class="input" id="dsTags" value="${esc((d.tags || []).join(", "))}"></div>
    <div class="field"><label>Notes</label><textarea class="input" id="dsNotes" rows="2">${esc(d.notes || "")}</textarea></div>
    <label class="checkline"><input type="checkbox" id="dsFlag" ${d.flagged ? "checked" : ""}> Flag this dataset</label>
    <div class="section-title" style="margin:8px 0 6px">Runs on this dataset — compare across model versions</div>
    ${runsTbl}
  `, [
    { label: "New run on this dataset", cls: "primary", fn: () => { closeModal(); openNewRun([id]); } },
    { label: "Compare runs", fn: () => { if (runs.length < 2) return toast("Need ≥2 runs to compare", "err"); closeModal(); location.hash = "#/compare/" + runs.map(x => x.id).join(","); } },
    { label: "Save", fn: async () => {
        await api("/datasets/" + id, { method: "PATCH", body: JSON.stringify({
          tags: $("#dsTags").value.split(",").map(s => s.trim()).filter(Boolean),
          notes: $("#dsNotes").value, flagged: $("#dsFlag").checked }) });
        toast("Saved", "ok"); closeModal(); if (parseHash().view === "datasets") renderDatasets();
      } },
  ]);
}
function qcPill(s) {
  const map = { pass: "succeeded", minor: "canceled", fail: "failed", unreviewed: "queued" };
  return `<span class="badge ${map[s] || "queued"}"><span class="dot"></span>${s || "unreviewed"}</span>`;
}

/* ------------------------------------------------------------------ models */
async function renderModels() {
  $("#pageActions").innerHTML = `<button class="btn primary" id="regBtn">＋ Register model</button>`;
  $("#regBtn").onclick = openRegisterModel;
  const models = await api("/models");
  if (!models.length) { $("#content").innerHTML = `<div class="empty"><div class="big">◆</div>No models yet. Click <b>Register model</b> and point it at a trained nnU-Net folder.</div>`; return; }
  const fams = {};
  models.forEach(m => { (fams[m.family || "—"] ||= []).push(m); });
  $("#content").innerHTML = Object.entries(fams).map(([fam, list]) => `
    <div class="section-title">${esc(fam)}</div>
    <div class="grid">${list.map(mCard).join("")}</div>`).join("");
}
function mCard(m) {
  return `<div class="mcard">
    <div class="top"><div><div class="fam">${esc(m.name)}</div>
      <div class="wrap" style="margin-top:5px"><span class="ver">${esc(m.version)}</span><span class="chip">${esc(m.configuration)}</span></div></div>
      <div style="text-align:right"><div class="dice">${m.cross_val_dice != null ? m.cross_val_dice.toFixed(3) : "—"}</div><div class="muted" style="font-size:11px">CV Dice</div></div></div>
    <div class="kv">
      <div class="k">Labels</div><div class="v">${esc(Object.values(m.labels || {}).join(", ") || "—")}</div>
      <div class="k">Train spacing</div><div class="v">${m.training_spacing_mm ? (m.training_spacing_mm * 1000).toFixed(1) + " µm" : "—"}</div>
      <div class="k">Train cases</div><div class="v">${m.num_training_cases ?? "—"}</div>
      <div class="k">Fingerprint</div><div class="v" style="font-size:11px">${esc(m.fingerprint || "—")}</div>
      <div class="k">Added</div><div class="v">${fmtDate(m.created_at)}</div>
    </div>
    <div class="flex" style="margin-top:12px">
      <button class="btn sm primary" onclick="openNewRun(null, ${m.id})">Run…</button>
      <a class="btn sm ghost" href="#/runs">History</a></div></div>`;
}
async function openRegisterModel() {
  const cfg = await api("/config");
  modal("Register a trained model", `
    <div class="field"><label>Model folder path</label>
      <input class="input" id="mp" placeholder="${esc(cfg.models_root)}/Dataset501_.../nnUNetTrainer__nnUNetPlans__3d_fullres">
      <div class="hint">The folder that directly contains plans.json, dataset.json, and fold_0 … fold_4.</div></div>
    <div class="field"><label>Family (optional)</label><input class="input" id="mf" placeholder="auto from dataset name"></div>
    <div class="field"><label>Version (optional)</label><input class="input" id="mv" placeholder="auto (v1, v2 …)"></div>
  `, [{ label: "Register", cls: "primary", fn: async () => {
      try { const m = await api("/models/register", { method: "POST", body: JSON.stringify({ path: $("#mp").value.trim(), family: $("#mf").value.trim() || null, version: $("#mv").value.trim() || null }) });
        toast(`Registered ${m.name}`, "ok"); closeModal(); renderModels();
      } catch (e) { toast("Register failed: " + e.message, "err"); }
    } }]);
}

/* -------------------------------------------------------------------- runs */
let runFilters = { status: "", qc_status: "", qc_tag: "" };
async function renderRuns(preset) {
  $("#pageActions").innerHTML = `<button class="btn" id="cmpSel">⇄ Compare selected</button>`;
  $("#cmpSel").onclick = () => {
    const ids = [...document.querySelectorAll(".rck:checked")].map(x => x.value);
    if (ids.length < 2) return toast("Tick ≥2 runs to compare", "err");
    location.hash = "#/compare/" + ids.join(",");
  };
  const bar = h(`<div class="toolbar">
    <select class="input" id="fstatus"><option value="">All statuses</option>${["queued", "running", "succeeded", "failed", "canceled"].map(s => `<option ${s === runFilters.status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <select class="input" id="fqc"><option value="">All QC</option>${["unreviewed", "pass", "minor", "fail"].map(s => `<option ${s === runFilters.qc_status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <select class="input" id="ftag"><option value="">Any failure mode</option>${VOCAB.map(v => `<option value="${v.key}" ${v.key === runFilters.qc_tag ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
    <div class="grow"></div></div>`);
  const wrap = h(`<div class="panel"><table class="tbl"><thead><tr><th style="width:28px"></th><th>Run</th><th>Dataset</th><th>Model</th><th>Status</th>
    <th class="right">ROI volume</th><th class="right">Duration</th><th>Machine</th><th>QC</th><th>Created</th></tr></thead><tbody id="rrows"></tbody></table></div>`);
  $("#content").innerHTML = ""; $("#content").append(bar, wrap);
  const reload = async () => {
    const p = new URLSearchParams(Object.fromEntries(Object.entries(runFilters).filter(([, v]) => v))).toString();
    const rows = await api("/runs" + (p ? "?" + p : ""));
    $("#rrows").innerHTML = rows.length ? rows.map(r => `
      <tr onclick="location.hash='#/run/${r.id}'">
        <td onclick="event.stopPropagation()"><input type="checkbox" class="rck" value="${r.id}"></td>
        <td>#${r.id}</td><td>${esc(r.dataset_name || "")}</td>
        <td>${esc(r.model_name || "")} <span class="ver">${esc(r.model_version || "")}</span></td>
        <td>${badge(r.status)}</td><td class="num">${fmtVol(r.roi_mm3)}</td><td class="num">${fmtDur(r.duration_sec)}</td>
        <td class="muted">${esc(r.host || (r.env || {}).host || "—")}</td>
        <td>${qcPill(r.qc_status)}${r.flagged ? ' <span title="flagged" style="color:var(--amber)">⚑</span>' : ""}</td>
        <td class="muted">${fmtDate(r.created_at)}</td></tr>`).join("")
      : `<tr><td colspan="10" class="muted" style="padding:22px">No runs match.</td></tr>`;
  };
  $("#fstatus").onchange = e => { runFilters.status = e.target.value; reload(); };
  $("#fqc").onchange = e => { runFilters.qc_status = e.target.value; reload(); };
  $("#ftag").onchange = e => { runFilters.qc_tag = e.target.value; reload(); };
  await reload();
  const anyActive = () => api("/stats").then(s => (s.runs_by_status.running || 0) + (s.runs_by_status.queued || 0) > 0);
  if (await anyActive()) pollTimer = setInterval(() => { if (parseHash().view === "runs") reload(); }, 4000);
}

/* -------------------------------------------------------------- run detail */
async function renderRunDetail(id) {
  const r = await api("/runs/" + id);
  $("#pageTitle").innerHTML = `Run #${r.id} — report`;
  $("#pageSub").textContent = `${r.dataset_name || ""} • ${r.model_name || ""} ${r.model_version || ""}`;
  $("#pageActions").innerHTML =
    `<button class="btn" id="cmpBtn">⇄ Compare dataset runs</button>
     <button class="btn" id="expBtn">⭳ Export report</button>
     <a class="btn ghost" href="#/runs">← All runs</a>`;
  const snap = r.model_snapshot || {}, env = r.env || {}, p = r.params || {};
  const summary = [
    ["Dataset", r.dataset_name], ["Model", r.model_name], ["Version", r.model_version], ["Status", r.status],
    ["ROI volume", fmtVol(r.roi_mm3)], ["Duration", fmtDur(r.duration_sec)],
    ["Machine", env.host || r.host || "—"], ["Device", (r.device_used || p.device || "—") + (env.gpu ? " · GPU" : "")],
  ];
  const metrics = [
    ["ROI volume", fmtVol(r.roi_mm3)], ["ROI voxels", fmtInt(r.roi_voxels)], ["ROI (µm³)", fmtUm(r.roi_um3)],
    ["Best slice", r.best_slice ?? "—"], ["Peak RAM", env.peak_ram_mb ? env.peak_ram_mb + " MB" : "—"],
    ["Model Dice (CV)", snap.cross_val_dice ? Number(snap.cross_val_dice).toFixed(3) : "—"],
  ];
  const params = [["Folds", p.folds], ["TTA", p.tta ? "on" : "off"], ["Step", p.step],
    ["Device", p.device], ["Spacing (mm)", p.spacing_mm], ["Pattern", p.pattern], ["Fingerprint", snap.fingerprint]];
  const envPairs = ENV_FIELDS.filter(f => env[f[0]] != null).map(f => [f[1], env[f[0]]]);
  $("#content").innerHTML = `
    <div class="panel"><div class="phead"><h3>Summary</h3>${badge(r.status)}</div>
      <div class="pbody"><div class="metrics" style="grid-template-columns:repeat(4,1fr)">
        ${summary.map(s => `<div class="metric"><div class="k">${s[0]}</div><div class="v" style="font-size:15px">${esc(s[1] ?? "—")}</div></div>`).join("")}
      </div></div></div>
    <div class="rd" style="margin-top:16px">
      <div>
        <div class="viewer" id="viewer"><div class="vfallback"><span class="spin"></span> preparing viewer…</div></div>
        <div class="panel" style="margin-top:14px"><div class="phead"><h3>Run log</h3>
          <button class="btn sm ghost" id="reloadLog">reload</button></div>
          <div class="pbody"><div class="logbox" id="log">loading…</div></div></div>
      </div>
      <div>
        <div class="panel"><div class="phead"><h3>Segmentation metrics</h3></div>
          <div class="pbody"><div class="metrics">${metrics.map(m => `<div class="metric"><div class="k">${m[0]}</div><div class="v">${esc(m[1])}</div></div>`).join("")}</div></div></div>
        <div class="panel" style="margin-top:14px"><div class="phead"><h3>Parameters</h3></div><div class="pbody">${kvGrid(params)}</div></div>
        <div class="panel" style="margin-top:14px"><div class="phead"><h3>Run environment — debrief</h3>${env.gpu ? `<span class="chip accent">${esc(env.gpu)}</span>` : `<span class="chip">CPU</span>`}</div>
          <div class="pbody">${envPairs.length ? kvGrid(envPairs) : `<div class="muted">The full machine/hardware debrief (CPU, RAM, GPU, versions, peak memory, per-phase timings) is captured automatically when the run executes.</div>`}</div></div>
        <div class="panel" style="margin-top:14px" id="qcPanel"></div>
      </div>
    </div>`;
  loadLog(id);
  $("#reloadLog").onclick = () => loadLog(id);
  $("#expBtn").onclick = () => exportReport(r);
  $("#cmpBtn").onclick = async () => {
    const runs = await api("/runs?dataset_id=" + r.dataset_id);
    if (runs.length < 2) return toast("Need ≥2 runs on this dataset to compare", "err");
    location.hash = "#/compare/" + runs.map(x => x.id).join(",");
  };
  renderQC(r);
  if (r.status === "succeeded") mountViewer(id);
  else if (r.status === "running" || r.status === "queued") {
    $("#viewer").innerHTML = `<div class="vfallback"><span class="spin"></span> ${r.status}… the viewer appears when the mask is ready.</div>`;
    pollTimer = setInterval(async () => {
      const u = await api("/runs/" + id);
      if (u.status !== r.status) { if (parseHash().view === "run") renderRunDetail(id); }
      else loadLog(id);
    }, 4000);
  } else {
    $("#viewer").innerHTML = `<div class="vfallback"><div class="big" style="font-size:32px">⚠</div>${r.status}. See log below.<br><span class="muted">${esc(r.error || "")}</span></div>`;
  }
}

function exportReport(r) {
  const env = r.env || {}, p = r.params || {}, snap = r.model_snapshot || {};
  const L = [`# Run #${r.id} — ${r.dataset_name || ""}`, "", "## Summary",
    `- Dataset: ${r.dataset_name || ""}`,
    `- Model: ${r.model_name || ""} (${r.model_version || ""}) · fingerprint ${snap.fingerprint || "—"}`,
    `- Status: ${r.status}`,
    `- ROI volume: ${fmtVol(r.roi_mm3)} (${fmtInt(r.roi_voxels)} voxels · ${fmtUm(r.roi_um3)})`,
    `- Duration: ${fmtDur(r.duration_sec)}`,
    `- Machine: ${env.host || r.host || "—"} · device ${r.device_used || p.device || "—"}${env.gpu ? " · " + env.gpu : ""}`,
    "", "## Parameters", ...Object.entries(p).map(([k, v]) => `- ${k}: ${v}`),
    "", "## Environment (debrief)", ...Object.entries(env).map(([k, v]) => `- ${k}: ${v}`),
    "", "## QC & failure modes",
    `- Outcome: ${r.qc_status}`,
    `- Failure modes: ${(r.qc_tags || []).join(", ") || "none"}`,
    `- Flagged for retraining: ${r.flagged ? "yes" : "no"}`,
    r.review_note ? `- Note: ${r.review_note}` : ""];
  const blob = new Blob([L.join("\n")], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `run_${r.id}_report.md`; a.click();
  toast("Report exported", "ok");
}

async function renderCompare(arg) {
  const ids = (arg || "").split(",").map(x => +x).filter(Boolean);
  if (ids.length < 2) { $("#content").innerHTML = `<div class="empty"><div class="big">⇄</div>Pick 2+ runs to compare — from a dataset's runs or the Runs page.</div>`; return; }
  const runs = await Promise.all(ids.map(i => api("/runs/" + i)));
  $("#pageSub").textContent = runs.map(r => "#" + r.id).join("  vs  ");
  $("#pageActions").innerHTML = `<a class="btn ghost" href="#/runs">← All runs</a>`;
  const base = runs[0].roi_mm3;
  const previews = runs.map(r => `<div style="flex:1;min-width:210px">
    <div class="flex" style="justify-content:space-between;margin-bottom:6px"><b>#${r.id}</b><span class="ver">${esc(r.model_version || "")}</span></div>
    ${r.status === "succeeded" ? `<img src="/api/runs/${r.id}/preview.png" style="width:100%;border-radius:8px;border:1px solid var(--border);background:#05070a">`
      : `<div class="viewer" style="min-height:150px"><div class="vfallback">${r.status}</div></div>`}
    <div class="mono" style="margin-top:6px">${fmtVol(r.roi_mm3)}${base && r.roi_mm3 && r !== runs[0]
      ? ` <span style="color:${r.roi_mm3 >= base ? "var(--green)" : "var(--red)"}">(${((r.roi_mm3 - base) / base * 100).toFixed(1)}%)</span>` : ""}</div>
    <a class="btn sm ghost" href="#/run/${r.id}" style="margin-top:6px">Open report</a></div>`).join("");
  const rows = [
    ["Model", r => `${esc(r.model_name || "")} <span class="ver">${esc(r.model_version || "")}</span>`],
    ["Fingerprint", r => `<span class="mono" style="font-size:11px">${esc((r.model_snapshot || {}).fingerprint || "—")}</span>`],
    ["Status", r => badge(r.status)],
    ["ROI volume", r => fmtVol(r.roi_mm3)], ["ROI voxels", r => fmtInt(r.roi_voxels)],
    ["Duration", r => fmtDur(r.duration_sec)],
    ["Machine", r => esc((r.env || {}).host || r.host || "—")],
    ["Device", r => esc(r.device_used || (r.params || {}).device || "—")],
    ["GPU", r => esc((r.env || {}).gpu || "—")],
    ["Peak RAM (MB)", r => esc((r.env || {}).peak_ram_mb ?? "—")],
    ["Folds", r => esc((r.params || {}).folds ?? "—")], ["TTA", r => (r.params || {}).tta ? "on" : "off"],
    ["QC", r => qcPill(r.qc_status)], ["Failure modes", r => esc((r.qc_tags || []).join(", ") || "—")],
  ];
  $("#content").innerHTML = `
    <div class="panel"><div class="phead"><h3>Previews</h3><span class="muted">Δ ROI vs #${runs[0].id}</span></div>
      <div class="pbody"><div class="wrap" style="align-items:flex-start">${previews}</div></div></div>
    <div class="panel" style="margin-top:16px"><div class="phead"><h3>Metric comparison</h3></div>
      <table class="tbl"><thead><tr><th>Metric</th>${runs.map(r => `<th>#${r.id}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr style="cursor:default"><td class="muted">${row[0]}</td>${runs.map(r => `<td>${row[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
async function loadLog(id) { try { const t = await api("/runs/" + id + "/log.txt"); const box = $("#log"); if (box) { box.textContent = t; box.scrollTop = box.scrollHeight; } } catch { } }

async function loadNiivue() {
  if (window.__nvmod) return window.__nvmod;
  for (const src of ["/vendor/niivue.js", "https://esm.sh/@niivue/niivue"]) {
    try { const m = await import(src); if (m && m.Niivue) { window.__nvmod = m; return m; } } catch (e) { }
  }
  return null;
}
async function mountViewer(id) {
  const box = $("#viewer");
  const mod = await loadNiivue();
  if (!mod) { // graceful fallback: preview PNG
    box.innerHTML = `<div class="vfallback"><img src="/api/runs/${id}/preview.png" alt="preview">
      <div class="muted">Interactive viewer unavailable (NiiVue didn't load). Showing the preview overlay.<br>
      Vendor NiiVue into <span class="mono">web/vendor/niivue.js</span> for offline use.</div></div>`;
    return;
  }
  box.innerHTML = `<div class="vwrap">
    <div class="vrail">
      <button class="vbtn" data-m="mpr" title="Multiplanar (MPR)">▦</button>
      <button class="vbtn" data-m="ax" title="Axial">A</button>
      <button class="vbtn" data-m="cor" title="Coronal">C</button>
      <button class="vbtn" data-m="sag" title="Sagittal">S</button>
      <button class="vbtn" data-m="3d" title="3D render">⬡</button>
      <div class="vsep"></div>
      <div class="vopac" title="Mask opacity"><input type="range" id="op" min="0" max="1" step="0.05" value="0.5"><span>α</span></div>
      <div class="vrot" id="vrot" style="display:none">
        <button class="vbtn xs" data-r="u" title="rotate up">▲</button>
        <div><button class="vbtn xs" data-r="l" title="rotate left">◄</button><button class="vbtn xs" data-r="r" title="rotate right">►</button></div>
        <button class="vbtn xs" data-r="d" title="rotate down">▼</button>
      </div>
      <div class="vsep"></div>
      <button class="vbtn" id="vreset" title="Reset view">⟲</button>
      <button class="vbtn" id="vmax" title="Maximize (F)">⤢</button>
      <a class="vbtn" href="/api/runs/${id}/mask.nii.gz" download title="Download full-res mask">⭳</a>
    </div>
    <div class="vstage">
      <canvas id="gl"></canvas>
      <div class="cine" id="cinebar" style="display:none">
        <button class="btn sm" id="cinePrev" title="previous slice (←)">⏮</button>
        <button class="btn sm primary" id="cinePlay" title="play/pause (space)">▶</button>
        <button class="btn sm" id="cineNext" title="next slice (→)">⏭</button>
        <input type="range" id="cineSlider" min="0" max="0" value="0">
        <span class="muted mono" id="cineLabel">–</span>
        <label class="muted" style="font-size:11px">fps <select id="cineFps" class="input"><option>6</option><option selected>12</option><option>20</option><option>30</option></select></label>
      </div>
    </div>
  </div>`;
  try {
    const nv = new mod.Niivue({ backColor: [0.02, 0.03, 0.05, 1], show3Dcrosshair: true, crosshairColor: [1, 0.6, 0, 0.6] });
    nv.attachTo("gl");
    // Downsampled copies for the viewer — full-res volumes overflow browser WebGL buffers.
    await nv.loadVolumes([
      { url: `/api/runs/${id}/view_input.nii.gz` },
      { url: `/api/runs/${id}/view_mask.nii.gz`, colormap: "red", opacity: 0.5, cal_min: 0.5, cal_max: 1 },
    ]);
    window.__nv = nv;
    setTimeout(() => { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }, 60);
    const SL = { mpr: nv.sliceTypeMultiplanar, ax: nv.sliceTypeAxial, cor: nv.sliceTypeCoronal, sag: nv.sliceTypeSagittal, "3d": nv.sliceTypeRender };
    let az = 180, el = 15, cur = "mpr", idx = 0;
    const stopCine = () => {
      if (window.__cine) { clearInterval(window.__cine); window.__cine = null; }
      const pb = box.querySelector("#cinePlay"); if (pb) pb.innerHTML = "▶ Play";
    };
    const axisInfo = (m) => {
      const d = (nv.volumes[0] && nv.volumes[0].dims) || [3, 1, 1, 1];
      if (m === "ax") return { i: 2, n: d[3] };   // step along k
      if (m === "cor") return { i: 1, n: d[2] };  // step along j
      if (m === "sag") return { i: 0, n: d[1] };  // step along i
      return null;
    };
    const gotoSlice = (ai, i) => {
      i = ((i % ai.n) + ai.n) % ai.n;
      try {
        const p = (nv.scene && nv.scene.crosshairPos) ? Array.from(nv.scene.crosshairPos) : [0.5, 0.5, 0.5];
        p[ai.i] = (i + 0.5) / ai.n; nv.scene.crosshairPos = p; nv.drawScene();
      } catch (e) { }
      return i;
    };
    const setSlice = (i) => {
      const ai = axisInfo(cur); if (!ai) return;
      idx = gotoSlice(ai, i);
      box.querySelector("#cineSlider").value = idx;
      box.querySelector("#cineLabel").textContent = `${idx + 1} / ${ai.n}`;
    };
    const updateCine = (m) => {
      const bar = box.querySelector("#cinebar"), ai = axisInfo(m);
      if (!ai) { stopCine(); bar.style.display = "none"; return; }
      bar.style.display = "flex";
      const sl = box.querySelector("#cineSlider"); sl.max = ai.n - 1;
      idx = Math.min(idx, ai.n - 1); setSlice(idx);
    };
    const setMode = (m) => {
      cur = m; stopCine();
      try { nv.setSliceType(SL[m]); } catch { }
      box.querySelectorAll(".vrail [data-m]").forEach(b => b.classList.toggle("active", b.dataset.m === m));
      const rot = box.querySelector("#vrot"); if (rot) rot.style.display = m === "3d" ? "flex" : "none";
      updateCine(m);
    };
    box.querySelectorAll(".vrail [data-m]").forEach(b => b.onclick = () => setMode(b.dataset.m));
    box.querySelector("#op").oninput = e => { try { nv.setOpacity(1, +e.target.value); } catch { } };
    box.querySelectorAll("#vrot [data-r]").forEach(b => b.onclick = () => {
      const d = b.dataset.r;
      az += d === "l" ? -20 : d === "r" ? 20 : 0;
      el += d === "u" ? 20 : d === "d" ? -20 : 0;
      try { nv.setRenderAzimuthElevation(az, el); } catch { }
    });
    box.querySelector("#vreset").onclick = () => { az = 180; el = 15; try { nv.setRenderAzimuthElevation(az, el); } catch { } setMode("mpr"); };
    box.querySelector("#cinePrev").onclick = () => { stopCine(); setSlice(idx - 1); };
    box.querySelector("#cineNext").onclick = () => { stopCine(); setSlice(idx + 1); };
    box.querySelector("#cineSlider").oninput = e => { stopCine(); setSlice(+e.target.value); };
    box.querySelector("#cinePlay").onclick = () => {
      if (window.__cine) { stopCine(); return; }
      const fps = +box.querySelector("#cineFps").value || 12;
      box.querySelector("#cinePlay").innerHTML = "⏸ Pause";
      window.__cine = setInterval(() => {
        if (!document.getElementById("gl")) { stopCine(); return; }
        setSlice(idx + 1);
      }, 1000 / fps);
    };
    const toggleMax = () => {
      const on = box.classList.toggle("vmax");
      box.querySelector("#vmax").innerHTML = on ? "⤡" : "⤢";
      box.querySelector("#vmax").title = on ? "Restore (Esc)" : "Maximize (F)";
      document.body.style.overflow = on ? "hidden" : "";
      setTimeout(() => { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }, 60);
    };
    box.querySelector("#vmax").onclick = toggleMax;
    const keyh = (e) => {
      if (!document.getElementById("gl")) { document.removeEventListener("keydown", keyh); return; }
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      const cineOn = box.querySelector("#cinebar").style.display !== "none";
      if (e.key === "f" || e.key === "F") toggleMax();
      else if (e.key === "Escape" && box.classList.contains("vmax")) toggleMax();
      else if (e.key === " " && cineOn) { e.preventDefault(); box.querySelector("#cinePlay").click(); }
      else if (e.key === "ArrowLeft" && cineOn) box.querySelector("#cinePrev").click();
      else if (e.key === "ArrowRight" && cineOn) box.querySelector("#cineNext").click();
    };
    document.addEventListener("keydown", keyh);
    setMode("mpr");
  } catch (e) {
    box.innerHTML = `<div class="vfallback"><img src="/api/runs/${id}/preview.png"><div class="muted">Viewer error: ${esc(e.message)}</div></div>`;
  }
}

/* --------------------------------------------------- QC / failure-mode panel */
function renderQC(r) {
  const panel = $("#qcPanel");
  const sel = new Set(r.qc_tags || []);
  const statusBtns = ["pass", "minor", "fail"].map(s =>
    `<button class="btn sm ${r.qc_status === s ? "primary" : "ghost"}" data-st="${s}">${s}</button>`).join("");
  panel.innerHTML = `
    <div class="phead"><h3>QC & failure modes</h3>${qcPill(r.qc_status)}</div>
    <div class="pbody">
      <div class="field"><label>Outcome</label><div class="wrap" id="qcStatus">${statusBtns}</div></div>
      <div class="field"><label>Failure modes <span class="muted">(click to toggle — used to find similar cases later)</span></label>
        <div class="wrap" id="qcTags">${VOCAB.map(v => `<span class="chip ${sel.has(v.key) ? "accent" : ""}" data-tag="${v.key}" style="cursor:pointer">${esc(v.label)}</span>`).join("")}</div></div>
      <div class="field"><label>Note</label><textarea class="input" id="qcNote" rows="2" placeholder="what went wrong / what to study…">${esc(r.review_note || "")}</textarea></div>
      <label class="checkline"><input type="checkbox" id="qcFlag" ${r.flagged ? "checked" : ""}> ⚑ Flag for retraining (candidate for the training set)</label>
      <div class="flex" style="margin-top:6px"><button class="btn primary" id="qcSave">Save review</button>
        <a class="btn ghost" href="#/insights">View all failure modes →</a></div>
    </div>`;
  let status = r.qc_status;
  panel.querySelectorAll("[data-st]").forEach(b => b.onclick = () => {
    status = b.dataset.st; panel.querySelectorAll("[data-st]").forEach(x => x.classList.toggle("primary", x === b));
    panel.querySelectorAll("[data-st]").forEach(x => x.classList.toggle("ghost", x !== b));
  });
  panel.querySelectorAll("[data-tag]").forEach(c => c.onclick = () => {
    const k = c.dataset.tag; if (sel.has(k)) { sel.delete(k); c.classList.remove("accent"); } else { sel.add(k); c.classList.add("accent"); }
  });
  $("#qcSave").onclick = async () => {
    await api("/runs/" + r.id + "/review", { method: "POST", body: JSON.stringify({
      qc_status: status || "pass", qc_tags: [...sel], review_note: $("#qcNote").value, flagged: $("#qcFlag").checked }) });
    toast("Review saved", "ok"); renderRunDetail(r.id);
  };
}

/* -------------------------------------------------------------- insights */
async function renderInsights() {
  const f = await api("/runs/facets");
  const total = Object.values(f.qc_status || {}).reduce((a, b) => a + b, 0);
  const maxc = Math.max(1, ...(f.failure_modes.map(m => m.count)));
  const statusTiles = ["pass", "minor", "fail", "unreviewed"].map(s =>
    `<div class="tile"><div class="accentbar" style="background:${{ pass: "var(--green)", minor: "var(--amber)", fail: "var(--red)", unreviewed: "var(--faint)" }[s]}"></div>
      <div class="k">${s}</div><div class="v">${f.qc_status[s] || 0}</div></div>`).join("");
  const bars = f.failure_modes.length ? f.failure_modes.map(m => `
    <div style="margin:9px 0;cursor:pointer" onclick="location.hash='#/runs';runFilters={status:'',qc_status:'',qc_tag:'${m.key}'};setTimeout(()=>document.querySelector('#ftag')&&(document.querySelector('#ftag').value='${m.key}'),50)">
      <div class="flex" style="justify-content:space-between"><span>${esc(m.label)}</span><span class="mono muted">${m.count}</span></div>
      <div style="height:9px;background:var(--panel3);border-radius:6px;margin-top:4px;overflow:hidden">
        <div style="height:100%;width:${(m.count / maxc * 100).toFixed(0)}%;background:linear-gradient(90deg,var(--accent),var(--purple))"></div></div></div>`).join("")
    : `<div class="muted" style="padding:14px">No failure modes tagged yet. Open a run and use the QC panel to label failure modes.</div>`;
  const flagged = await api("/runs?flagged=true");
  $("#content").innerHTML = `
    <div class="section-title">Review status (${total} runs)</div>
    <div class="tiles">${statusTiles}</div>
    <div class="rd">
      <div class="panel"><div class="phead"><h3>Failure modes</h3><span class="muted">click a bar to see those cases</span></div>
        <div class="pbody">${bars}</div></div>
      <div class="panel"><div class="phead"><h3>⚑ Flagged for retraining</h3><span class="chip accent">${flagged.length}</span></div>
        <table class="tbl"><tbody>${flagged.length ? flagged.map(r => `<tr onclick="location.hash='#/run/${r.id}'">
          <td>#${r.id}</td><td>${esc(r.dataset_name || "")}</td><td>${esc(r.model_version || "")}</td>
          <td>${(r.qc_tags || []).length ? (r.qc_tags || []).length + " modes" : ""}</td></tr>`).join("")
        : `<tr><td class="muted" style="padding:16px">Nothing flagged yet.</td></tr>`}</tbody></table></div>
    </div>`;
}

/* ------------------------------------------------------------- new-run modal */
window.openNewRun = async function (datasetIds = null, modelId = null) {
  const [models, datasets, cfg] = await Promise.all([api("/models"), api("/datasets"), api("/config")]);
  if (!models.length) return toast("Register a model first", "err");
  if (!datasets.length) return toast("Ingest datasets first", "err");
  const sel = new Set(datasetIds || []);
  const modelOpts = models.map(m => `<option value="${m.id}" ${m.id === modelId ? "selected" : ""}>${esc(m.family || m.name)} — ${esc(m.version)} ${m.cross_val_dice ? `(Dice ${m.cross_val_dice.toFixed(3)})` : ""}</option>`).join("");
  modal("New segmentation run", `
    <div class="field"><label>Model &amp; version</label><select class="input" id="nrModel">${modelOpts}</select></div>
    <div class="field"><label>Datasets</label>
      <div class="pick" id="nrPick">${datasets.map(d => `<label class="opt"><input type="checkbox" value="${d.id}" ${sel.has(d.id) ? "checked" : ""}> ${esc(d.name)} <span class="muted mono" style="margin-left:auto">${vox(d.voxel_size_um)}</span></label>`).join("")}</div></div>
    <div class="wrap">
      <div class="field" style="flex:1"><label>Folds</label><input class="input" id="nrFolds" value="0"><div class="hint">"0" fast · "0 1 2 3 4" ensemble</div></div>
      <div class="field" style="flex:1"><label>Device</label><select class="input" id="nrDev"><option value="auto" ${cfg.default_device === "auto" ? "selected" : ""}>auto</option><option>cuda</option><option>cpu</option></select></div>
    </div>
    <div class="wrap">
      <div class="field" style="flex:1"><label>Step (overlap)</label><input class="input" id="nrStep" value="0.5"></div>
      <div class="field" style="flex:1"><label style="margin-top:26px"><input type="checkbox" id="nrTta"> Test-time augmentation</label></div>
    </div>
  `, [{ label: "Queue run(s)", cls: "primary", fn: async () => {
      const ids = [...document.querySelectorAll("#nrPick input:checked")].map(x => +x.value);
      if (!ids.length) return toast("Pick at least one dataset", "err");
      try { const runs = await api("/runs", { method: "POST", body: JSON.stringify({
          dataset_ids: ids, model_id: +$("#nrModel").value, folds: $("#nrFolds").value.trim(),
          tta: $("#nrTta").checked, step: parseFloat($("#nrStep").value), device: $("#nrDev").value }) });
        toast(`Queued ${runs.length} run${runs.length > 1 ? "s" : ""}`, "ok"); closeModal(); location.hash = "#/runs";
      } catch (e) { toast("Failed: " + e.message, "err"); }
    } }]);
};

/* ---------------------------------------------------------------- modal core */
function modal(title, bodyHtml, actions = []) {
  closeModal();
  const ov = h(`<div class="overlay"><div class="modal">
    <div class="mhead"><h3>${title}</h3><button class="btn sm ghost" id="mx">✕</button></div>
    <div class="mbody">${bodyHtml}</div>
    <div class="mfoot"></div></div></div>`);
  const foot = ov.querySelector(".mfoot");
  actions.forEach(a => { const b = h(`<button class="btn ${a.cls || "ghost"}">${a.label}</button>`); b.onclick = a.fn; foot.appendChild(b); });
  foot.appendChild(h(`<button class="btn ghost" id="mcancel">Close</button>`));
  ov.querySelector("#mx").onclick = closeModal;
  ov.querySelector("#mcancel").onclick = closeModal;
  ov.onclick = e => { if (e.target === ov) closeModal(); };
  $("#modalRoot").appendChild(ov);
  return ov;
}
function closeModal() { $("#modalRoot").innerHTML = ""; }

/* -------------------------------------------------------------------- boot */
async function boot() {
  $("#newRunBtn").onclick = () => openNewRun();
  try { const c = await api("/config"); $("#sysbox").innerHTML =
    `<b>Storage</b>
     <div class="row"><span>data</span><span title="${esc(c.data_root)}">${esc(c.data_root)}</span></div>
     <div class="row"><span>results</span><span title="${esc(c.results_root)}">${esc(c.results_root)}</span></div>
     <div class="row"><span>device</span><span>${esc(c.default_device)}</span></div>`; } catch { }
  try { VOCAB = (await api("/runs/vocab")).failure_modes; } catch { VOCAB = []; }
  route();
}
boot();
