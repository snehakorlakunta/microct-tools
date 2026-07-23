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
  if (window.__clcine) { clearInterval(window.__clcine); window.__clcine = null; }
};

// ---- configurable run-report panels (persisted) ----
const PANEL_KEYS = ["summary", "viewer", "metrics", "parameters", "environment", "qc", "log"];
const PANEL_TITLES = {
  summary: "Summary", viewer: "Viewer", metrics: "Segmentation metrics",
  parameters: "Parameters", environment: "Run environment", qc: "QC & failure modes", log: "Run log",
};
let panelShown = new Set(PANEL_KEYS);     // persisted: which panels are visible
let panelCollapsed = new Set();           // persisted: which cards are collapsed
function loadLayout() {
  try { const s = JSON.parse(localStorage.getItem("mlab_shown")); panelShown = Array.isArray(s) ? new Set(s) : new Set(PANEL_KEYS); } catch { panelShown = new Set(PANEL_KEYS); }
  try { const c = JSON.parse(localStorage.getItem("mlab_collapsed")); panelCollapsed = Array.isArray(c) ? new Set(c) : new Set(); } catch { panelCollapsed = new Set(); }
}
function saveShown() { try { localStorage.setItem("mlab_shown", JSON.stringify([...panelShown])); } catch { } }
function saveCollapsed() { try { localStorage.setItem("mlab_collapsed", JSON.stringify([...panelCollapsed])); } catch { } }
function wrapCard(key, headExtra, bodyHtml, opts = {}) {
  const canCollapse = opts.collapsible !== false;
  const collapsed = canCollapse && panelCollapsed.has(key);
  const chev = canCollapse ? `<button class="pico" data-collapse="${key}" title="${collapsed ? "expand" : "collapse"}">${collapsed ? "▸" : "▾"}</button>` : "";
  return `<div class="panel rpanel ${collapsed ? "collapsed" : ""}" data-panel="${key}">
    <div class="phead"><h3>${PANEL_TITLES[key]}</h3>
      <div class="pctl">${headExtra || ""}${chev}
        <button class="pico" data-close="${key}" title="hide panel">✕</button>
      </div></div>
    <div class="pbody"${opts.flush ? ' style="padding:0"' : ""}>${bodyHtml}</div></div>`;
}
function fitReport() {
  const rs = document.querySelector(".report-split");
  if (!rs) return;
  const top = rs.getBoundingClientRect().top;   // viewport-relative
  rs.style.height = Math.max(340, window.innerHeight - top - 14) + "px";
  const nv = window.__nv; if (nv) { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }
}
function initSplitter() {
  const sp = document.getElementById("splitter");
  const side = document.getElementById("repSide");
  const cont = document.querySelector(".report-split");
  if (!sp || !side || !cont) return;
  sp.addEventListener("mousedown", (e) => {
    e.preventDefault();
    document.body.style.userSelect = "none";
    const rect = cont.getBoundingClientRect();
    const mm = (ev) => {
      let w = rect.right - ev.clientX;
      w = Math.max(280, Math.min(rect.width - 300, w));
      side.style.width = w + "px";
    };
    const mu = () => {
      document.removeEventListener("mousemove", mm);
      document.removeEventListener("mouseup", mu);
      document.body.style.userSelect = "";
      try { localStorage.setItem("mlab_sidew", parseInt(side.style.width) || 400); } catch { }
      const nv = window.__nv; if (nv) { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }
    };
    document.addEventListener("mousemove", mm);
    document.addEventListener("mouseup", mu);
  });
}

/* ------------------------------------------------------------------ router */
const PAGES = {
  overview: { title: "Overview", sub: "Your segmentation registry at a glance", fn: renderOverview },
  projects: { title: "Projects", sub: "Projects → experiments → datasets & analyses", fn: renderProjects },
  project: { title: "Project", sub: "", fn: renderProjectDetail },
  datasets: { title: "Datasets", sub: "Catalog of microCT & omics datasets", fn: renderDatasets },
  models: { title: "Models", sub: "Trained models & versions", fn: renderModels },
  runs: { title: "Runs", sub: "Processing history", fn: renderRuns },
  timeline: { title: "Timeline", sub: "Everything, newest first", fn: renderTimeline },
  insights: { title: "QC & Insights", sub: "Failure modes and review status", fn: renderInsights },
  run: { title: "Run", sub: "", fn: renderRunDetail },
  compare: { title: "Compare runs", sub: "Side-by-side results", fn: renderCompare },
  dataset: { title: "Dataset", sub: "", fn: renderDatasetView },
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
window.addEventListener("resize", () => { if (document.querySelector(".report-split")) fitReport(); });

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
let dsFilters = { q: "", study: "", scanner: "", type: "", tag: "", sort: "created_at", order: "desc" };
let dsView = "grid";
async function renderDatasets() {
  $("#pageActions").innerHTML = `<button class="btn" id="ingestBtn">⟳ Ingest datasets</button>`;
  $("#ingestBtn").onclick = doIngest;
  const facets = await api("/datasets/facets").catch(() => ({ studies: [], scanners: [], types: [], organisms: [], tags: [] }));
  const bar = h(`<div class="toolbar">
    <div class="search"><span class="mag">⌕</span><input class="input" id="q" placeholder="Search datasets…" value="${esc(dsFilters.q)}"></div>
    <select class="input" id="type"><option value="">All types</option>${(facets.types || []).map(s => `<option ${s === dsFilters.type ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select class="input" id="study"><option value="">All studies</option>${facets.studies.map(s => `<option ${s === dsFilters.study ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select class="input" id="tag"><option value="">Any tag</option>${(facets.tags || []).map(s => `<option ${s === dsFilters.tag ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <div class="grow"></div>
    <select class="input" id="sort">
      ${[["created_at", "Newest"], ["name", "Name"], ["voxel_size_um", "Voxel size"], ["slices", "Slices"], ["scan_date", "Scan date"], ["type", "Type"]].map(o => `<option value="${o[0]}" ${o[0] === dsFilters.sort ? "selected" : ""}>${o[1]}</option>`).join("")}
    </select>
    <button class="btn sm" id="order">${dsFilters.order === "desc" ? "↓" : "↑"}</button>
    <button class="btn sm" id="viewToggle" title="Toggle grid / organization tree">${dsView === "grid" ? "🌳 Tree" : "▦ Grid"}</button></div>`);
  const grid = h(`<div id="grid"></div>`);
  $("#content").innerHTML = ""; $("#content").append(bar, grid);

  const reload = async () => {
    if (dsView === "tree") {
      const tax = await api("/datasets/taxonomy");
      grid.className = "";
      grid.innerHTML = tax.types.length ? tax.types.map(t => `
        <div class="panel" style="margin-bottom:12px"><div class="phead"><h3>${TYPE_ICON[t.type] || "◆"} ${esc(t.type)}</h3></div>
          <div class="pbody">${t.organisms.map(o => `
            <div class="treeset"><div class="treeset-head"><span class="tset-name">${esc(o.organism)} <span class="muted">(${o.datasets.length})</span></span></div>
              <div class="treeds">${o.datasets.map(d => `<div class="dsrow" data-ds="${d.id}"><span class="dsrow-ic">${TYPE_ICON[t.type] || "◆"}</span><span class="dsrow-nm">${esc(d.name)}</span><span class="muted mono">${d.slices ? d.slices + " sl" : ""}${d.run_count ? " · " + d.run_count + " run" + (d.run_count > 1 ? "s" : "") : ""}</span></div>`).join("")}</div>
            </div>`).join("")}</div></div>`).join("")
        : `<div class="empty"><div class="big">🌳</div>No datasets to organize yet.</div>`;
    } else {
      const p = new URLSearchParams(dsFilters).toString();
      const rows = await api("/datasets?" + p);
      grid.className = "grid";
      grid.innerHTML = rows.length ? rows.map(dsCard).join("")
        : `<div class="empty" style="grid-column:1/-1"><div class="big">▦</div>No datasets. Set <b>MICROCT_DATA_ROOT</b> and click <b>Ingest datasets</b>.</div>`;
    }
    grid.querySelectorAll("[data-ds]").forEach(c => c.onclick = () => openDataset(+c.dataset.ds));
  };
  $("#q").oninput = debounce(e => { dsFilters.q = e.target.value; reload(); });
  $("#type").onchange = e => { dsFilters.type = e.target.value; reload(); };
  $("#study").onchange = e => { dsFilters.study = e.target.value; reload(); };
  $("#tag").onchange = e => { dsFilters.tag = e.target.value; reload(); };
  $("#sort").onchange = e => { dsFilters.sort = e.target.value; reload(); };
  $("#order").onclick = () => { dsFilters.order = dsFilters.order === "desc" ? "asc" : "desc"; $("#order").textContent = dsFilters.order === "desc" ? "↓" : "↑"; reload(); };
  $("#viewToggle").onclick = () => { dsView = dsView === "grid" ? "tree" : "grid"; $("#viewToggle").textContent = dsView === "grid" ? "🌳 Tree" : "▦ Grid"; reload(); };
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
      <div class="wrap" style="margin-top:9px">
        <button class="btn sm" onclick="event.stopPropagation();location.hash='#/dataset/${d.id}'" title="View the raw scan">◉ Visualize</button>
        <button class="btn sm ghost" onclick="event.stopPropagation();openNewRun([${d.id}])" title="Segment this dataset">▶ Run</button>
      </div>
    </div></div>`;
}
async function doIngest() {
  toast("Scanning data root…");
  try { const r = await api("/ingest", { method: "POST", body: JSON.stringify({}) });
    toast(`Ingest: +${r.created.length} new, ${r.updated.length} updated`, "ok"); renderDatasets();
  } catch (e) { toast("Ingest failed: " + e.message, "err"); }
}

/* --------------------------------------------------------- dataset drawer */
const DATASET_TYPES = ["uct", "scrna", "spatial", "omics", "other"];
async function openDataset(id) {
  const [d, runs, exps, sets] = await Promise.all([
    api("/datasets/" + id), api("/runs?dataset_id=" + id),
    api("/experiments").catch(() => []), api("/sets").catch(() => [])]);
  const expName = Object.fromEntries(exps.map(e => [e.id, e.name]));
  const metaRows = [
    ["Scanner", d.scanner], ["Voxel size", vox(d.voxel_size_um)],
    ["Dimensions", d.width ? `${d.width} × ${d.height} × ${d.slices}` : d.slices],
    ["Bit depth", d.bit_depth], ["Source", `${d.source_voltage_kv || "?"} kV / ${d.source_current_ua || "?"} µA`],
    ["Filter", d.filter], ["Scan date", d.scan_date],
    ["Size", fmtBytes(d.size_bytes)], ["Path", `<span class="mono" style="font-size:11px">${esc(d.slices_path)}</span>`],
  ];
  const setOpts = `<option value="">— unassigned —</option>` + sets.map(s =>
    `<option value="${s.id}" ${s.id === d.set_id ? "selected" : ""}>${esc(expName[s.experiment_id] || "exp")} / ${esc(s.name)}</option>`).join("");
  const typeOpts = DATASET_TYPES.map(t => `<option value="${t}" ${t === d.type ? "selected" : ""}>${t}</option>`).join("");
  const runsTbl = runs.length ? `<table class="tbl"><thead><tr><th>Run</th><th>Model</th><th>Status</th><th class="right">ROI</th><th>QC</th></tr></thead><tbody>
    ${runs.map(r => `<tr onclick="location.hash='#/run/${r.id}'"><td>#${r.id}</td>
      <td>${esc(r.model_name || "")} <span class="ver">${esc(r.model_version || "")}</span></td>
      <td>${badge(r.status)}</td><td class="num">${fmtVol(r.roi_mm3)}</td>
      <td>${qcPill(r.qc_status)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="muted" style="padding:14px">No runs yet for this dataset.</div>`;
  modal(esc(d.name), `
    <div class="wrap" style="margin-bottom:6px">${d.thumbnail ? `<img src="/api/datasets/${id}/thumbnail" style="width:100%;max-height:200px;object-fit:contain;border-radius:10px;border:1px solid var(--border);background:#05070a">` : ""}</div>
    <div class="mcard"><div class="kv">${metaRows.map(r => `<div class="k">${r[0]}</div><div class="v">${r[1] ?? "—"}</div>`).join("")}</div></div>
    <div class="wrap">
      <div class="field" style="flex:2"><label>Name</label><input class="input" id="dsName" value="${esc(d.name)}"></div>
      <div class="field" style="flex:1"><label>Type</label><select class="input" id="dsType">${typeOpts}</select></div>
    </div>
    <div class="wrap">
      <div class="field" style="flex:1"><label>Organism</label><input class="input" id="dsOrg" value="${esc(d.organism || "")}" placeholder="Mouse, Rat, …"></div>
      <div class="field" style="flex:1"><label>Study</label><input class="input" id="dsStudy" value="${esc(d.study || "")}"></div>
    </div>
    <div class="field"><label>Assign to set (experiment / set)</label><select class="input" id="dsSet">${setOpts}</select>
      <div class="hint">Manage projects, experiments & sets in the Projects tab.</div></div>
    <div class="field"><label>Tags (comma-separated)</label><input class="input" id="dsTags" value="${esc((d.tags || []).join(", "))}"></div>
    <div class="field"><label>Notes</label><textarea class="input" id="dsNotes" rows="2">${esc(d.notes || "")}</textarea></div>
    <label class="checkline"><input type="checkbox" id="dsFlag" ${d.flagged ? "checked" : ""}> Flag this dataset</label>
    <div class="section-title" style="margin:8px 0 6px">Runs on this dataset — compare across model versions</div>
    ${runsTbl}
  `, [
    { label: "New run", cls: "primary", fn: () => { closeModal(); openNewRun([id]); } },
    { label: "Visualize", fn: () => { closeModal(); location.hash = "#/dataset/" + id; } },
    { label: "📂 Open folder", fn: async () => {
        try { const r = await api("/system/open-folder", { method: "POST", body: JSON.stringify({ dataset_id: id }) }); toast("Opened " + r.opened, "ok"); }
        catch (e) { toast("Open failed: " + e.message, "err"); } } },
    { label: "Delete", cls: "danger", fn: () => confirmDeleteDataset(d) },
    { label: "Save", fn: async () => {
        const body = {
          name: $("#dsName").value.trim(), type: $("#dsType").value,
          organism: $("#dsOrg").value.trim(), study: $("#dsStudy").value.trim(),
          tags: $("#dsTags").value.split(",").map(s => s.trim()).filter(Boolean),
          notes: $("#dsNotes").value, flagged: $("#dsFlag").checked };
        const setv = $("#dsSet").value;
        if (setv) body.set_id = +setv; else body.clear_set = true;
        await api("/datasets/" + id, { method: "PATCH", body: JSON.stringify(body) });
        toast("Saved", "ok"); closeModal(); if (parseHash().view === "datasets") renderDatasets();
      } },
  ]);
}
function confirmDeleteDataset(d) {
  const hasRuns = d.run_count > 0;
  modal("Delete dataset", `<p>Delete <b>${esc(d.name)}</b>?</p>
    ${hasRuns ? `<p class="muted" style="font-size:12px;margin-top:8px">This dataset has <b>${d.run_count}</b> run(s). Its runs are provenance records and are never deleted — the dataset will be <b>archived</b> instead (hidden but recoverable).</p>`
      : `<p class="muted" style="font-size:12px;margin-top:8px">No runs — the dataset record will be removed. Files on disk are not touched.</p>`}`,
    [{ label: hasRuns ? "Archive" : "Delete", cls: "danger", fn: async () => {
        try { await api("/datasets/" + d.id, { method: "DELETE" });
          toast(hasRuns ? "Archived" : "Deleted", "ok"); closeModal();
          if (parseHash().view === "datasets") renderDatasets();
        } catch (e) { toast("Failed: " + e.message, "err"); } } }]);
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
      <button class="btn sm ghost" onclick='openRenameModel(${JSON.stringify({ id: m.id, name: m.name, family: m.family, description: m.description || "" })})'>✎ Rename</button>
      <button class="btn sm ghost" onclick='openRegisterModel(${JSON.stringify(m.family || m.name)})' title="Register another version in this family">＋ Version</button>
      <a class="btn sm ghost" href="#/runs">History</a></div></div>`;
}
window.openRenameModel = function (m) {
  modal("Rename model", `
    <div class="field"><label>Display name</label><input class="input" id="rmName" value="${esc(m.name)}">
      <div class="hint">Independent of the folder name — this is what shows in the UI.</div></div>
    <div class="field"><label>Description</label><textarea class="input" id="rmDesc" rows="2">${esc(m.description || "")}</textarea></div>
  `, [{ label: "Save", cls: "primary", fn: async () => {
      try { await api("/models/" + m.id, { method: "PATCH", body: JSON.stringify({ name: $("#rmName").value.trim(), description: $("#rmDesc").value }) });
        toast("Renamed", "ok"); closeModal(); renderModels();
      } catch (e) { toast("Rename failed: " + e.message, "err"); } } }]);
};
window.openRegisterModel = async function (familyPreset = null) {
  const cfg = await api("/config");
  const fam = typeof familyPreset === "string" ? familyPreset : "";
  modal(fam ? `Register a new version of “${esc(fam)}”` : "Register a trained model", `
    <div class="field"><label>Model folder path</label>
      <input class="input" id="mp" placeholder="${esc(cfg.models_root)}/Dataset501_.../nnUNetTrainer__nnUNetPlans__3d_fullres">
      <div class="hint">The folder that directly contains plans.json, dataset.json, and fold_0 … fold_4.</div></div>
    <div class="field"><label>Name (optional)</label><input class="input" id="mn" placeholder="custom display name"></div>
    <div class="field"><label>Family (optional)</label><input class="input" id="mf" value="${esc(fam)}" placeholder="auto from dataset name"><div class="hint">Reuse a family to add v2, v3, … of the same model.</div></div>
    <div class="field"><label>Version (optional)</label><input class="input" id="mv" placeholder="auto (v1, v2 …)"></div>
  `, [
    { label: "Scan models root", fn: async () => {
        try { const r = await api("/system/discover-models", { method: "POST", body: JSON.stringify({}) });
          toast(`Discovered ${r.registered.length} model(s), ${r.skipped} skipped`, "ok"); closeModal(); renderModels();
        } catch (e) { toast("Scan failed: " + e.message, "err"); } } },
    { label: "Register", cls: "primary", fn: async () => {
      try { const m = await api("/models/register", { method: "POST", body: JSON.stringify({ path: $("#mp").value.trim(), name: $("#mn").value.trim() || null, family: $("#mf").value.trim() || null, version: $("#mv").value.trim() || null }) });
        toast(`Registered ${m.name}`, "ok"); closeModal(); renderModels();
      } catch (e) { toast("Register failed: " + e.message, "err"); }
    } }]);
};

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
    <th class="right">ROI volume</th><th class="right">Duration</th><th>Machine</th><th>QC</th><th>Created</th><th></th></tr></thead><tbody id="rrows"></tbody></table></div>`);
  $("#content").innerHTML = ""; $("#content").append(bar, wrap);
  const reload = async () => {
    const p = new URLSearchParams(Object.fromEntries(Object.entries(runFilters).filter(([, v]) => v))).toString();
    const rows = await api("/runs" + (p ? "?" + p : ""));
    const byId = Object.fromEntries(rows.map(x => [String(x.id), x]));
    const acts = r => {
      const stop = ["running", "queued", "canceling"].includes(r.status)
        ? `<button class="ico danger" title="Stop run" data-stop="${r.id}">■</button>` : "";
      const arch = ["succeeded", "failed", "canceled"].includes(r.status)
        ? `<button class="ico danger" title="Archive run (runs are never deleted)" data-arch="${r.id}">🗄</button>` : "";
      return stop + arch;
    };
    $("#rrows").innerHTML = rows.length ? rows.map(r => `
      <tr onclick="location.hash='#/run/${r.id}'">
        <td onclick="event.stopPropagation()"><input type="checkbox" class="rck" value="${r.id}"></td>
        <td>#${r.id}</td><td>${esc(r.dataset_name || "")}</td>
        <td>${esc(r.model_name || "")} <span class="ver">${esc(r.model_version || "")}</span></td>
        <td>${badge(r.status)}</td><td class="num">${fmtVol(r.roi_mm3)}</td><td class="num">${fmtDur(r.duration_sec)}</td>
        <td class="muted">${esc(r.host || (r.env || {}).host || "—")}</td>
        <td>${qcPill(r.qc_status)}${r.flagged ? ' <span title="flagged" style="color:var(--amber)">⚑</span>' : ""}</td>
        <td class="muted">${fmtDate(r.created_at)}</td>
        <td class="rowact" onclick="event.stopPropagation()">${acts(r)}</td></tr>`).join("")
      : `<tr><td colspan="11" class="muted" style="padding:22px">No runs match.</td></tr>`;
    $("#rrows").querySelectorAll("[data-stop]").forEach(b => b.onclick = async (e) => {
      e.stopPropagation(); b.disabled = true;
      try { await api("/runs/" + b.dataset.stop + "/cancel", { method: "POST" }); toast("Stopping run…", "ok"); reload(); }
      catch (err) { b.disabled = false; toast("Stop failed: " + err.message, "err"); }
    });
    $("#rrows").querySelectorAll("[data-arch]").forEach(b => b.onclick = (e) => {
      e.stopPropagation(); confirmArchiveRun(byId[b.dataset.arch] || { id: b.dataset.arch }, reload);
    });
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
  const canStop = ["running", "queued"].includes(r.status);
  const terminal = ["succeeded", "failed", "canceled"].includes(r.status);
  $("#pageTitle").innerHTML = `Run #${r.id} — report`;
  $("#pageSub").textContent = `${r.dataset_name || ""} • ${r.model_name || ""} ${r.model_version || ""}`;
  $("#pageActions").innerHTML =
    `<button class="btn" id="cmpBtn">⇄ Compare dataset runs</button>
     ${r.status === "succeeded" ? `<button class="btn" id="bmpBtn" title="Save the mask as one BMP per slice, in the run's results folder">🖼 Mask BMPs</button>` : ""}
     <button class="btn" id="expBtn">⭳ Export report</button>
     ${canStop ? `<button class="btn danger" id="stopBtn">■ Stop run</button>` : ""}
     ${terminal && !r.archived ? `<button class="btn danger" id="archBtn" title="Runs are never deleted — only archived">🗄 Archive</button>` : ""}
     ${r.archived ? `<button class="btn ghost" id="unarchBtn">⇤ Unarchive</button>` : ""}
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
  const summaryBody = `<div class="metrics" style="grid-template-columns:repeat(4,1fr)">
    ${summary.map(s => `<div class="metric"><div class="k">${s[0]}</div><div class="v" style="font-size:15px">${esc(s[1] ?? "—")}</div></div>`).join("")}</div>`;
  const metricsBody = `<div class="metrics">${metrics.map(m => `<div class="metric"><div class="k">${m[0]}</div><div class="v">${esc(m[1])}</div></div>`).join("")}</div>`;
  const envBody = envPairs.length ? kvGrid(envPairs)
    : `<div class="muted">The machine/hardware debrief (CPU, RAM, GPU, versions, peak memory, timings) is captured automatically when the run executes.</div>`;

  const chips = PANEL_KEYS.map(k => `<button class="pchip ${panelShown.has(k) ? "on" : ""}" data-toggle="${k}">${PANEL_TITLES[k]}</button>`).join("");
  const bar = `<div class="panelbar"><span class="muted" style="font-size:12px">Panels</span>${chips}<div class="grow"></div><button class="pchip" id="resetLayout">Reset layout</button></div>`;

  // Build ALL panels once. Show/hide is a CSS display toggle, so toggling a panel
  // never tears down/reloads the viewer or re-renders the other panels.
  const leftHtml = wrapCard("viewer", "", `<div class="viewer" id="viewer"><div class="vfallback"><span class="spin"></span> preparing viewer…</div></div>`, { flush: true, collapsible: false });
  const right = [
    wrapCard("summary", badge(r.status), summaryBody),
    wrapCard("metrics", "", metricsBody),
    wrapCard("parameters", "", kvGrid(params)),
    wrapCard("environment", env.gpu ? `<span class="chip accent">${esc(env.gpu)}</span>` : `<span class="chip">CPU</span>`, envBody),
    wrapCard("qc", "", `<div id="qcInner"></div>`),
    wrapCard("log", `<button class="btn sm ghost" id="reloadLog">reload</button>`, `<div class="logbox" id="log"><span class="spin"></span></div>`),
  ].join("");

  const active = ["running", "queued", "canceling"].includes(r.status);
  const progHtml = active ? `<div class="runprog" id="runProg">
       <div class="rp-head"><span class="rp-phase">preparing…</span><span class="rp-elapsed muted"></span></div>
       <div class="rp-track"><div class="rp-fill" style="width:0%"></div></div>
       <div class="rp-sub muted"></div></div>` : "";
  $("#content").innerHTML = bar + progHtml +
    `<div class="report-split">
       <div class="report-left" id="repLeft">${leftHtml}</div>
       <div class="splitter" id="splitter"></div>
       <div class="report-side" id="repSide">${right}</div>
     </div>`;
  const wrap = $("#content");

  // While a run is undergoing there's no mask to show, so the viewer is hidden by
  // default (a spinner + progress bar take its place). The user can still reveal it
  // with the Viewer panel chip; that clears the session-only hide for this run.
  let forceHideViewer = active;
  const viewerVisible = () => panelShown.has("viewer") && !forceHideViewer;

  const applyPanelVisibility = () => {
    PANEL_KEYS.forEach(k => {
      const on = k === "viewer" ? viewerVisible() : panelShown.has(k);
      const el = wrap.querySelector(`[data-panel="${k}"]`); if (el) el.style.display = on ? "" : "none";
    });
    wrap.querySelectorAll(".pchip[data-toggle]").forEach(c => {
      const k = c.dataset.toggle;
      c.classList.toggle("on", k === "viewer" ? viewerVisible() : panelShown.has(k));
    });
    const hasLeft = viewerVisible();
    const hasRight = ["summary", "metrics", "parameters", "environment", "qc", "log"].some(k => panelShown.has(k));
    const rl = $("#repLeft"), rs = $("#repSide"), sp = $("#splitter");
    if (rl) rl.style.display = hasLeft ? "" : "none";
    if (sp) sp.style.display = (hasLeft && hasRight) ? "" : "none";
    if (rs) {
      rs.style.display = hasRight ? "" : "none";
      const w = Math.max(280, parseInt(localStorage.getItem("mlab_sidew")) || 400);
      if (hasLeft) { rs.style.flex = "0 0 auto"; rs.style.width = w + "px"; } else { rs.style.flex = "1"; rs.style.width = ""; }
    }
    fitReport();
  };

  // Viewer mounts once (lazily on first show); toggling other panels never remounts it.
  let viewerMounted = false;
  const ensureViewer = () => {
    if (viewerMounted) { if (window.__nv) { try { window.__nv.resizeListener(); } catch { } } return; }
    if (!viewerVisible()) return;
    viewerMounted = true;
    if (r.status === "succeeded") mountViewer([
      { url: `/api/runs/${id}/view_input.nii.gz` },
      { url: `/api/runs/${id}/view_mask.nii.gz`, colormap: "red", opacity: 0.5, cal_min: 0.5, cal_max: 1 },
    ], { hasMask: true, previewUrl: `/api/runs/${id}/preview.png`, downloadUrl: `/api/runs/${id}/mask.nii.gz` });
    else if (r.status === "running" || r.status === "queued") {
      $("#viewer").innerHTML = `<div class="vfallback"><span class="spin"></span> ${r.status}… the viewer appears when the mask is ready.</div>`;
      // progress + status polling is driven by the dedicated poller below.
    } else {
      $("#viewer").innerHTML = `<div class="vfallback"><div class="big" style="font-size:32px">⚠</div>${r.status}. See log below.<br><span class="muted">${esc(r.error || "")}</span></div>`;
    }
  };

  wrap.querySelectorAll(".pchip[data-toggle]").forEach(b => b.onclick = () => {
    const k = b.dataset.toggle;
    if (k === "viewer" && forceHideViewer) { forceHideViewer = false; panelShown.add(k); }
    else { panelShown.has(k) ? panelShown.delete(k) : panelShown.add(k); }
    saveShown(); applyPanelVisibility(); ensureViewer();
  });
  wrap.querySelectorAll("[data-close]").forEach(b => b.onclick = () => {
    panelShown.delete(b.dataset.close); saveShown(); applyPanelVisibility();
  });
  wrap.querySelectorAll("[data-collapse]").forEach(b => b.onclick = () => {
    const k = b.dataset.collapse, card = b.closest(".panel");
    const c = card.classList.toggle("collapsed");
    c ? panelCollapsed.add(k) : panelCollapsed.delete(k);
    b.textContent = c ? "▸" : "▾"; b.title = c ? "expand" : "collapse"; saveCollapsed();
  });
  $("#resetLayout").onclick = () => {
    panelShown = new Set(PANEL_KEYS); panelCollapsed = new Set();
    saveShown(); saveCollapsed(); try { localStorage.removeItem("mlab_sidew"); } catch { } renderRunDetail(id);
  };
  $("#expBtn").onclick = () => exportReport(r);
  $("#cmpBtn").onclick = async () => {
    const runs = await api("/runs?dataset_id=" + r.dataset_id);
    if (runs.length < 2) return toast("Need ≥2 runs on this dataset to compare", "err");
    location.hash = "#/compare/" + runs.map(x => x.id).join(",");
  };
  const bmpBtn = $("#bmpBtn");
  if (bmpBtn) {
    const fmtSize = b => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : Math.round(b / 1e6) + " MB";
    api("/runs/" + id + "/bmp_status").then(s => {
      if (s && s.exists) { bmpBtn.innerHTML = `🖼 Mask BMPs ✓ ${s.count}`; bmpBtn.title = `${s.count} slices · ${fmtSize(s.bytes)}\n${s.dir}`; }
    }).catch(() => { });
    bmpBtn.onclick = async () => {
      const orig = bmpBtn.innerHTML;
      bmpBtn.disabled = true; bmpBtn.innerHTML = `<span class="spin"></span> writing…`;
      try {
        const info = await api("/runs/" + id + "/export_bmp", { method: "POST" });
        bmpBtn.innerHTML = `🖼 Mask BMPs ✓ ${info.count}`;
        bmpBtn.title = `${info.count} slices · ${fmtSize(info.bytes)}\n${info.dir}`;
        toast(info.cached ? `Mask BMPs already present — ${info.count} slices in ${info.dir}`
          : `Saved ${info.count} mask BMPs (${fmtSize(info.bytes)}) → ${info.dir}`, "ok");
      } catch (e) {
        bmpBtn.innerHTML = orig; toast("BMP export failed: " + e.message, "err");
      } finally { bmpBtn.disabled = false; }
    };
  }

  const stopBtn = $("#stopBtn");
  if (stopBtn) stopBtn.onclick = async () => {
    stopBtn.disabled = true; stopBtn.textContent = "stopping…";
    try { await api("/runs/" + id + "/cancel", { method: "POST" }); toast("Stopping run…", "ok"); renderRunDetail(id); }
    catch (e) { stopBtn.disabled = false; stopBtn.textContent = "■ Stop run"; toast("Stop failed: " + e.message, "err"); }
  };
  const archBtn = $("#archBtn");
  if (archBtn) archBtn.onclick = () => confirmArchiveRun(r, () => { location.hash = "#/runs"; });
  const unarchBtn = $("#unarchBtn");
  if (unarchBtn) unarchBtn.onclick = async () => {
    try { await api("/runs/" + id + "/unarchive", { method: "POST" }); toast("Unarchived", "ok"); renderRunDetail(id); }
    catch (e) { toast("Failed: " + e.message, "err"); }
  };

  initSplitter();
  const main = document.querySelector(".main"); if (main) main.scrollTop = 0;
  renderQC(r);                                   // populated once (harmless if hidden)
  loadLog(id); { const rl = $("#reloadLog"); if (rl) rl.onclick = () => loadLog(id); }
  applyPanelVisibility();
  ensureViewer();
  setTimeout(fitReport, 80);

  if (active) {
    const PHASE = { starting: "Starting", queued: "Queued", converting: "Converting slices → volume",
      loading: "Loading model", predicting: "Segmenting — nnU-Net inference",
      finalizing: "Finalizing — mask, preview, BMPs", canceling: "Stopping run",
      succeeded: "Complete", failed: "Failed", canceled: "Canceled" };
    const paintProg = (p) => {
      const el = $("#runProg"); if (!el || !p) return;
      el.querySelector(".rp-phase").textContent = (PHASE[p.phase] || p.phase || "Working")
        + (p.percent != null ? ` — ${p.percent}%` : "");
      const fill = el.querySelector(".rp-fill");
      if (p.determinate && p.percent != null) { el.classList.remove("indet"); fill.style.width = p.percent + "%"; }
      else { el.classList.add("indet"); }
      const sub = []; if (p.detail) sub.push(p.detail);
      if (p.eta_sec != null) sub.push("~" + fmtDur(p.eta_sec) + " left");
      el.querySelector(".rp-sub").textContent = sub.join("  ·  ");
      el.querySelector(".rp-elapsed").textContent = p.elapsed_sec != null ? fmtDur(p.elapsed_sec) + " elapsed" : "";
    };
    const tick = async () => {
      if (parseHash().view !== "run") return;
      let p = null;
      try { p = await api("/runs/" + id + "/progress"); } catch { }
      if (p) {
        paintProg(p);
        if (["succeeded", "failed", "canceled"].includes(p.status)) { renderRunDetail(id); return; }
      }
      if (panelShown.has("log")) loadLog(id);
    };
    tick();
    pollTimer = setInterval(tick, 3000);
  }
}

function confirmArchiveRun(r, after) {
  const body = `<p>Archive <b>run #${r.id}</b>${r.dataset_name ? " · " + esc(r.dataset_name) : ""}${r.model_version ? ` <span class="ver">${esc(r.model_version)}</span>` : ""}?</p>
    <p class="muted" style="margin-top:8px;font-size:12px">Runs are the immutable provenance record and are <b>never deleted</b>. Archiving hides it from the default lists; its result files stay on disk and you can unarchive it any time.</p>`;
  modal("Archive run", body, [{
    label: "Archive", cls: "danger", fn: async () => {
      try {
        await api(`/runs/${r.id}/archive`, { method: "POST" });
        closeModal(); toast(`Archived run #${r.id}`, "ok");
        if (after) after();
      } catch (e) { toast("Archive failed: " + e.message, "err"); }
    }
  }]);
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
  const succ = runs.filter(r => r.status === "succeeded");
  const canLink = succ.length >= 2;
  const top = canLink
    ? `<div class="clviewer" id="cmpViewer"><div class="vfallback" style="height:60vh"><span class="spin"></span> loading linked viewer…</div></div>`
    : `<div class="panel"><div class="phead"><h3>Previews</h3><span class="muted">Δ ROI vs #${runs[0].id}</span></div>
        <div class="pbody"><div class="wrap" style="align-items:flex-start">${previews}</div></div></div>`;
  $("#content").innerHTML = top +
    `<div class="panel" style="margin-top:16px"><div class="phead"><h3>Metric comparison</h3>${canLink ? `<span class="muted">interactive linked view above · scroll/drag one pane to move both</span>` : ""}</div>
      <table class="tbl"><thead><tr><th>Metric</th>${runs.map(r => `<th>#${r.id}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(row => `<tr style="cursor:default"><td class="muted">${row[0]}</td>${runs.map(r => `<td>${row[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  if (canLink) mountLinkedCompare(succ.slice(0, 2));
}

async function mountLinkedCompare(runs) {
  const mod = await loadNiivue();
  const box = document.getElementById("cmpViewer");
  if (!box) return;
  if (!mod) { box.innerHTML = `<div class="vfallback" style="height:200px"><div class="muted">Interactive viewer unavailable; use the table below.</div></div>`; return; }
  const [A, B] = runs;
  box.innerHTML = `
    <div class="clbar">
      <div class="vmodes" id="clModes">
        <button class="btn sm" data-m="mpr">MPR</button>
        <button class="btn sm" data-m="ax">Axial</button>
        <button class="btn sm" data-m="cor">Coronal</button>
        <button class="btn sm" data-m="sag">Sagittal</button>
        <button class="btn sm" data-m="3d">3D</button>
      </div>
      <button class="btn sm" id="clZin" title="zoom in">＋</button>
      <button class="btn sm" id="clZout" title="zoom out">－</button>
      <button class="btn sm" id="clPan" title="Pan mode (P) — then drag either image">✋</button>
      <button class="btn sm" id="clReset" title="reset view">⟲</button>
      <label class="checkline" style="font-size:12px"><input type="checkbox" id="clSync" checked> sync panes</label>
      <div class="grow"></div>
      <label class="muted" style="font-size:11px">α A<input type="range" id="clOpA" min="0" max="1" step="0.05" value="0.5" style="width:64px;vertical-align:middle"></label>
      <label class="muted" style="font-size:11px">α B<input type="range" id="clOpB" min="0" max="1" step="0.05" value="0.5" style="width:64px;vertical-align:middle"></label>
      <button class="btn sm" id="clMax" title="maximize">⤢</button>
    </div>
    <div class="clcine" id="clCine" style="display:none">
      <button class="btn sm" id="clPrev">⏮</button>
      <button class="btn sm primary" id="clPlay">▶</button>
      <button class="btn sm" id="clNext">⏭</button>
      <input type="range" id="clSlider" min="0" max="0" value="0">
      <span class="muted mono" id="clLabel">–</span>
      <label class="muted" style="font-size:11px">fps <select id="clFps" class="input"><option>6</option><option selected>12</option><option>20</option></select></label>
    </div>
    <div class="clpanes">
      <div class="clpane"><div class="cltag">#${A.id} <span class="ver">${esc(A.model_version || "")}</span></div><canvas id="glA"></canvas></div>
      <div class="clpane"><div class="cltag">#${B.id} <span class="ver">${esc(B.model_version || "")}</span></div><canvas id="glB"></canvas></div>
    </div>`;
  try {
    const mk = () => new mod.Niivue({ backColor: [0.02, 0.03, 0.05, 1], show3Dcrosshair: true, crosshairColor: [1, 0.6, 0, 0.6] });
    const nvA = mk(), nvB = mk();
    nvA.attachTo("glA"); nvB.attachTo("glB");
    await nvA.loadVolumes([{ url: `/api/runs/${A.id}/view_input.nii.gz` }, { url: `/api/runs/${A.id}/view_mask.nii.gz`, colormap: "red", opacity: 0.5, cal_min: 0.5, cal_max: 1 }]);
    await nvB.loadVolumes([{ url: `/api/runs/${B.id}/view_input.nii.gz` }, { url: `/api/runs/${B.id}/view_mask.nii.gz`, colormap: "red", opacity: 0.5, cal_min: 0.5, cal_max: 1 }]);
    window.__nvA = nvA; window.__nvB = nvB; window.__nv = nvA;
    const both = [nvA, nvB];
    const SL = { mpr: nvA.sliceTypeMultiplanar, ax: nvA.sliceTypeAxial, cor: nvA.sliceTypeCoronal, sag: nvA.sliceTypeSagittal, "3d": nvA.sliceTypeRender };
    let cur = "mpr", idx = 0, zoom = 1, syncing = true, guard = false, panOn = false;
    const axisN = (m) => { const d = (nvA.volumes[0] && nvA.volumes[0].dims) || [3, 1, 1, 1]; return m === "ax" ? { i: 2, n: d[3] } : m === "cor" ? { i: 1, n: d[2] } : m === "sag" ? { i: 0, n: d[1] } : null; };
    const stopCine = () => { if (window.__clcine) { clearInterval(window.__clcine); window.__clcine = null; } const pb = document.getElementById("clPlay"); if (pb) pb.innerHTML = "▶"; };
    const gotoBoth = (ai, i) => { i = ((i % ai.n) + ai.n) % ai.n; guard = true; both.forEach(nv => { try { const p = (nv.scene && nv.scene.crosshairPos) ? Array.from(nv.scene.crosshairPos) : [0.5, 0.5, 0.5]; p[ai.i] = (i + 0.5) / ai.n; nv.scene.crosshairPos = p; nv.drawScene(); } catch { } }); guard = false; return i; };
    const setSlice = (i) => { const ai = axisN(cur); if (!ai) return; idx = gotoBoth(ai, i); const sl = document.getElementById("clSlider"), lb = document.getElementById("clLabel"); if (sl) sl.value = idx; if (lb) lb.textContent = `${idx + 1} / ${ai.n}`; };
    const updateCine = (m) => { const ai = axisN(m), bar = document.getElementById("clCine"); if (!ai) { bar.style.display = "none"; stopCine(); return; } bar.style.display = "flex"; document.getElementById("clSlider").max = ai.n - 1; idx = Math.min(idx, ai.n - 1); setSlice(idx); };
    const setMode = (m) => { cur = m; stopCine(); both.forEach(nv => { try { nv.setSliceType(SL[m]); } catch { } }); box.querySelectorAll("#clModes .btn").forEach(b => b.classList.toggle("primary", b.dataset.m === m)); updateCine(m); };
    const setPan = (on) => { panOn = on; const pb = document.getElementById("clPan"); if (pb) pb.classList.toggle("primary", on); both.forEach(nv => { try { const dm = nv.dragModes || {}; nv.opts.dragMode = on ? (dm.pan ?? 3) : (dm.contrast ?? 1); } catch { } }); };
    const applyZoom = (z) => { zoom = Math.max(0.4, Math.min(10, z)); if (zoom > 1.01 && !panOn) setPan(true); both.forEach(nv => { try { if (nv.scene && nv.scene.pan2Dxyzmm) { nv.scene.pan2Dxyzmm[3] = zoom; nv.drawScene(); } } catch { } }); };
    const mirror = (from, to) => { if (!syncing || guard) return; guard = true; try { to.scene.crosshairPos = Array.from(from.scene.crosshairPos); to.drawScene(); } catch { } guard = false; };
    nvA.onLocationChange = () => mirror(nvA, nvB);
    nvB.onLocationChange = () => mirror(nvB, nvA);
    box.querySelectorAll("#clModes .btn").forEach(b => b.onclick = () => setMode(b.dataset.m));
    document.getElementById("clZin").onclick = () => applyZoom(zoom * 1.25);
    document.getElementById("clZout").onclick = () => applyZoom(zoom / 1.25);
    document.getElementById("clPan").onclick = () => setPan(!panOn);
    document.getElementById("clReset").onclick = () => { zoom = 1; setPan(false); both.forEach(nv => { try { if (nv.scene && nv.scene.pan2Dxyzmm) nv.scene.pan2Dxyzmm.set([0, 0, 0, 1]); } catch { } }); setMode("mpr"); };
    document.getElementById("clSync").onchange = e => { syncing = e.target.checked; };
    document.getElementById("clOpA").oninput = e => { try { nvA.setOpacity(1, +e.target.value); } catch { } };
    document.getElementById("clOpB").oninput = e => { try { nvB.setOpacity(1, +e.target.value); } catch { } };
    document.getElementById("clPrev").onclick = () => { stopCine(); setSlice(idx - 1); };
    document.getElementById("clNext").onclick = () => { stopCine(); setSlice(idx + 1); };
    document.getElementById("clSlider").oninput = e => { stopCine(); setSlice(+e.target.value); };
    document.getElementById("clPlay").onclick = () => { if (window.__clcine) { stopCine(); return; } const fps = +document.getElementById("clFps").value || 12; document.getElementById("clPlay").innerHTML = "⏸"; window.__clcine = setInterval(() => { if (!document.getElementById("glA")) { stopCine(); return; } setSlice(idx + 1); }, 1000 / fps); };
    document.getElementById("clMax").onclick = () => { const on = box.classList.toggle("vmax"); document.body.style.overflow = on ? "hidden" : ""; document.getElementById("clMax").innerHTML = on ? "⤡" : "⤢"; setTimeout(() => { both.forEach(nv => { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }); }, 60); };
    const clkey = (e) => {
      if (!document.getElementById("glA")) { document.removeEventListener("keydown", clkey); return; }
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (e.key === "p" || e.key === "P") setPan(!panOn);
      else if (e.key === "+" || e.key === "=") applyZoom(zoom * 1.25);
      else if (e.key === "-" || e.key === "_") applyZoom(zoom / 1.25);
      else if (e.key === "f" || e.key === "F") document.getElementById("clMax").click();
      else if (e.key === "Escape" && box.classList.contains("vmax")) document.getElementById("clMax").click();
    };
    document.addEventListener("keydown", clkey);
    setMode("mpr");
    setTimeout(() => { both.forEach(nv => { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }); }, 90);
  } catch (e) {
    box.innerHTML = `<div class="vfallback" style="height:200px"><div class="muted">Viewer error: ${esc(e.message)}</div></div>`;
  }
}
async function loadLog(id) { try { const t = await api("/runs/" + id + "/log.txt"); const box = $("#log"); if (box) { box.textContent = t; box.scrollTop = box.scrollHeight; } } catch { } }

async function loadNiivue() {
  if (window.__nvmod) return window.__nvmod;
  for (const src of ["/vendor/niivue.js", "https://esm.sh/@niivue/niivue"]) {
    try { const m = await import(src); if (m && m.Niivue) { window.__nvmod = m; return m; } } catch (e) { }
  }
  return null;
}
async function renderDatasetView(id) {
  const d = await api("/datasets/" + id);
  $("#pageTitle").textContent = d.name;
  $("#pageSub").textContent = `${d.study || ""} · ${vox(d.voxel_size_um)} · ${d.width || "?"}×${d.height || "?"}×${d.slices || "?"} · raw scan`;
  $("#pageActions").innerHTML = `<button class="btn" id="dvRun">Run segmentation</button><a class="btn ghost" href="#/datasets">← Datasets</a>`;
  $("#content").innerHTML =
    `<div class="report-split">
       <div class="report-left" id="repLeft">
         <div class="panel rpanel" data-panel="viewer">
           <div class="phead"><h3>Dataset viewer — raw scan (no segmentation)</h3></div>
           <div class="pbody" style="padding:0">
             <div class="viewer" id="viewer"><div class="vfallback"><span class="spin"></span> building a viewable volume from the slice stack… (first time can take a few seconds)</div></div>
           </div>
         </div>
       </div>
     </div>`;
  $("#dvRun").onclick = () => openNewRun([id]);
  const main = document.querySelector(".main"); if (main) main.scrollTop = 0;
  fitReport(); setTimeout(fitReport, 80);
  mountViewer([{ url: `/api/datasets/${id}/view_volume.nii.gz` }],
    { hasMask: false, previewUrl: `/api/datasets/${id}/thumbnail` });
}

async function mountViewer(volumes, opts = {}) {
  const box = $("#viewer");
  const mod = await loadNiivue();
  const prev = opts.previewUrl;
  if (!mod) { // graceful fallback
    box.innerHTML = prev
      ? `<div class="vfallback"><img src="${prev}" alt="preview"><div class="muted">Interactive viewer unavailable (NiiVue didn't load); showing the preview.</div></div>`
      : `<div class="vfallback"><div class="muted">Interactive viewer unavailable (NiiVue didn't load).</div></div>`;
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
      <button class="vbtn" id="zin" title="Zoom in (+ or Ctrl+scroll)">＋</button>
      <button class="vbtn" id="zout" title="Zoom out (− or Ctrl+scroll)">－</button>
      <button class="vbtn" id="pan" title="Pan mode (P) — then drag the image">✋</button>
      <div class="vsep"></div>
      ${opts.hasMask ? `<div class="vopac" title="Mask opacity"><input type="range" id="op" min="0" max="1" step="0.05" value="0.5"><span>α</span></div>` : ""}
      <div class="vrot" id="vrot" style="display:none">
        <button class="vbtn xs" data-r="u" title="rotate up">▲</button>
        <div><button class="vbtn xs" data-r="l" title="rotate left">◄</button><button class="vbtn xs" data-r="r" title="rotate right">►</button></div>
        <button class="vbtn xs" data-r="d" title="rotate down">▼</button>
      </div>
      <div class="vsep"></div>
      <button class="vbtn" id="vreset" title="Reset view">⟲</button>
      <button class="vbtn" id="vmax" title="Maximize (F)">⤢</button>
      ${opts.downloadUrl ? `<a class="vbtn" href="${opts.downloadUrl}" download title="Download full-res file">⭳</a>` : ""}
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
    // Downsampled volumes (full-res would overflow browser WebGL buffers).
    await nv.loadVolumes(volumes);
    window.__nv = nv;
    setTimeout(() => { try { nv.resizeListener(); } catch { } try { nv.drawScene(); } catch { } }, 60);
    const SL = { mpr: nv.sliceTypeMultiplanar, ax: nv.sliceTypeAxial, cor: nv.sliceTypeCoronal, sag: nv.sliceTypeSagittal, "3d": nv.sliceTypeRender };
    let az = 180, el = 15, cur = "mpr", idx = 0, zoom = 1, panOn = false;
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
    { const opEl = box.querySelector("#op"); if (opEl) opEl.oninput = e => { try { nv.setOpacity(1, +e.target.value); } catch { } }; }
    box.querySelectorAll("#vrot [data-r]").forEach(b => b.onclick = () => {
      const d = b.dataset.r;
      az += d === "l" ? -20 : d === "r" ? 20 : 0;
      el += d === "u" ? 20 : d === "d" ? -20 : 0;
      try { nv.setRenderAzimuthElevation(az, el); } catch { }
    });
    const applyZoom = (z) => {
      zoom = Math.max(0.4, Math.min(10, z));
      if (zoom > 1.01 && !panOn) setPan(true);
      try { if (nv.scene && nv.scene.pan2Dxyzmm) { nv.scene.pan2Dxyzmm[3] = zoom; nv.drawScene(); } } catch { }
    };
    const setPan = (on) => {
      panOn = on;
      const pb = box.querySelector("#pan"); if (pb) pb.classList.toggle("active", on);
      try { const dm = nv.dragModes || {}; nv.opts.dragMode = on ? (dm.pan ?? 3) : (dm.contrast ?? 1); } catch { }
    };
    box.querySelector("#zin").onclick = () => applyZoom(zoom * 1.25);
    box.querySelector("#zout").onclick = () => applyZoom(zoom / 1.25);
    box.querySelector("#pan").onclick = () => setPan(!panOn);
    box.querySelector("#gl").addEventListener("wheel", (e) => {
      if (e.ctrlKey) { e.preventDefault(); e.stopPropagation(); applyZoom(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)); }
    }, { passive: false, capture: true });
    box.querySelector("#vreset").onclick = () => {
      az = 180; el = 15; zoom = 1; setPan(false);
      try { nv.setRenderAzimuthElevation(az, el); } catch { }
      try { if (nv.scene && nv.scene.pan2Dxyzmm) { nv.scene.pan2Dxyzmm.set([0, 0, 0, 1]); nv.drawScene(); } } catch { }
      setMode("mpr");
    };
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
      else if (e.key === "+" || e.key === "=") applyZoom(zoom * 1.25);
      else if (e.key === "-" || e.key === "_") applyZoom(zoom / 1.25);
      else if (e.key === "0") box.querySelector("#vreset").click();
      else if (e.key === "p" || e.key === "P") setPan(!panOn);
    };
    document.addEventListener("keydown", keyh);
    setMode("mpr");
  } catch (e) {
    box.innerHTML = prev
      ? `<div class="vfallback"><img src="${prev}"><div class="muted">Viewer error: ${esc(e.message)}</div></div>`
      : `<div class="vfallback"><div class="muted">Viewer error: ${esc(e.message)}</div></div>`;
  }
}

/* --------------------------------------------------- QC / failure-mode panel */
function renderQC(r) {
  const panel = $("#qcInner");
  if (!panel) return;
  const sel = new Set(r.qc_tags || []);
  const statusBtns = ["pass", "minor", "fail"].map(s =>
    `<button class="btn sm ${r.qc_status === s ? "primary" : "ghost"}" data-st="${s}">${s}</button>`).join("");
  panel.innerHTML = `
      <div class="field"><label>Outcome ${qcPill(r.qc_status)}</label><div class="wrap" id="qcStatus">${statusBtns}</div></div>
      <div class="field"><label>Failure modes <span class="muted">(click to toggle — used to find similar cases later)</span></label>
        <div class="wrap" id="qcTags">${VOCAB.map(v => `<span class="chip ${sel.has(v.key) ? "accent" : ""}" data-tag="${v.key}" style="cursor:pointer">${esc(v.label)}</span>`).join("")}</div></div>
      <div class="field"><label>Note</label><textarea class="input" id="qcNote" rows="2" placeholder="what went wrong / what to study…">${esc(r.review_note || "")}</textarea></div>
      <label class="checkline"><input type="checkbox" id="qcFlag" ${r.flagged ? "checked" : ""}> ⚑ Flag for retraining (candidate for the training set)</label>
      <div class="flex" style="margin-top:6px"><button class="btn primary" id="qcSave">Save review</button>
        <a class="btn ghost" href="#/insights">View all failure modes →</a></div>`;
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
  const [models, datasets, cfg, compute] = await Promise.all([
    api("/models"), api("/datasets"), api("/config"),
    api("/system/compute").catch(() => null)]);
  if (!models.length) return toast("Register a model first", "err");
  if (!datasets.length) return toast("Ingest datasets first", "err");
  const sel = new Set(datasetIds || []);
  const modelOpts = models.map(m => `<option value="${m.id}" ${m.id === modelId ? "selected" : ""}>${esc(m.family || m.name)} — ${esc(m.version)} ${m.cross_val_dice ? `(Dice ${m.cross_val_dice.toFixed(3)})` : ""}</option>`).join("");
  // Pre-run compute readout: what this machine offers + what `auto` would pick.
  const rec = compute ? compute.recommended_device : null;
  const gpuLine = compute && compute.gpus && compute.gpus.length
    ? compute.gpus.map(g => `${esc(g.name)}${g.memory_total_mb ? ` · ${(g.memory_total_mb / 1024).toFixed(0)} GB` : ""}`).join(", ")
    : "none detected";
  const computeBox = compute ? `<div class="computebox">
      <div class="cb-row"><span class="cb-k">This machine</span><span>${esc(compute.host || "—")}</span></div>
      <div class="cb-row"><span class="cb-k">CPU</span><span>${esc(compute.cpu || "—")} · ${compute.logical_cores || "?"} threads${compute.ram_total_gb ? ` · ${compute.ram_total_gb} GB RAM` : ""}</span></div>
      <div class="cb-row"><span class="cb-k">GPU</span><span>${gpuLine}</span></div>
      <div class="cb-row"><span class="cb-k">auto → </span><span><span class="chip ${rec === "cuda" ? "accent" : ""}">${esc(rec || "?")}</span></span></div>
    </div>` : `<div class="hint">Compute probe unavailable.</div>`;
  const devList = (compute && compute.devices) || ["auto", "cuda", "cpu"];
  const devOpts = devList.map(dv => `<option value="${dv}" ${dv === cfg.default_device ? "selected" : ""}>${dv}${dv === "auto" && rec ? ` (→ ${rec})` : ""}</option>`).join("");
  modal("New segmentation run", `
    <div class="field"><label>Model &amp; version</label><select class="input" id="nrModel">${modelOpts}</select></div>
    <div class="field"><label>Compute available</label>${computeBox}</div>
    <div class="field"><label>Datasets</label>
      <div class="pick" id="nrPick">${datasets.map(d => `<label class="opt"><input type="checkbox" value="${d.id}" ${sel.has(d.id) ? "checked" : ""}> ${esc(d.name)} <span class="muted mono" style="margin-left:auto">${vox(d.voxel_size_um)}</span></label>`).join("")}</div></div>
    <div class="wrap">
      <div class="field" style="flex:1"><label>Folds</label><input class="input" id="nrFolds" value="0"><div class="hint">"0" fast · "0 1 2 3 4" ensemble</div></div>
      <div class="field" style="flex:1"><label>Device</label><select class="input" id="nrDev">${devOpts}</select></div>
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

/* ---------------------------------------------------------------- helpers2 */
const tagsPills = (tags) => (tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join(" ");
const parseTags = (s) => (s || "").split(",").map(x => x.trim()).filter(Boolean);
const TYPE_ICON = { uct: "🦴", scrna: "🧬", spatial: "🗺", omics: "🧪", other: "◆" };

/* ---------------------------------------------------------------- projects */
async function renderProjects() {
  $("#pageActions").innerHTML = `<button class="btn primary" id="newProj">＋ New project</button>`;
  $("#newProj").onclick = () => openProjectForm();
  const projects = await api("/projects");
  if (!projects.length) {
    $("#content").innerHTML = `<div class="empty"><div class="big">▣</div>No projects yet. Click <b>New project</b> to group experiments, datasets, and analyses.</div>`;
    return;
  }
  $("#content").innerHTML = `<div class="grid">${projects.map(p => `
    <div class="mcard" style="cursor:pointer" onclick="location.hash='#/project/${p.id}'">
      <div class="top"><div><div class="fam">${esc(p.name)}</div>
        <div class="wrap" style="margin-top:6px">${tagsPills(p.tags)}</div></div></div>
      <div class="metrics" style="grid-template-columns:repeat(3,1fr);margin-top:10px">
        <div class="metric"><div class="k">Experiments</div><div class="v">${p.experiment_count}</div></div>
        <div class="metric"><div class="k">Datasets</div><div class="v">${p.dataset_count}</div></div>
        <div class="metric"><div class="k">Analyses</div><div class="v">${p.analysis_count}</div></div>
      </div>
      ${p.description ? `<div class="muted" style="margin-top:8px;font-size:12px">${esc(p.description)}</div>` : ""}
    </div>`).join("")}</div>`;
}

function openProjectForm(p = null) {
  modal(p ? "Edit project" : "New project", `
    <div class="field"><label>Name</label><input class="input" id="pfName" value="${esc(p ? p.name : "")}"></div>
    <div class="field"><label>Description</label><textarea class="input" id="pfDesc" rows="2">${esc(p ? p.description || "" : "")}</textarea></div>
    <div class="field"><label>Tags (comma-separated)</label><input class="input" id="pfTags" value="${esc(p ? (p.tags || []).join(", ") : "")}"></div>
  `, [{ label: p ? "Save" : "Create", cls: "primary", fn: async () => {
      const body = { name: $("#pfName").value.trim(), description: $("#pfDesc").value, tags: parseTags($("#pfTags").value) };
      if (!body.name) return toast("Name required", "err");
      try {
        if (p) { await api("/projects/" + p.id, { method: "PATCH", body: JSON.stringify(body) }); toast("Saved", "ok"); closeModal(); renderProjectDetail(p.id); }
        else { const np = await api("/projects", { method: "POST", body: JSON.stringify(body) }); toast("Created", "ok"); closeModal(); location.hash = "#/project/" + np.id; }
      } catch (e) { toast("Failed: " + e.message, "err"); }
    } }]);
}

async function renderProjectDetail(id) {
  const tree = await api("/projects/" + id + "/tree");
  $("#pageTitle").textContent = tree.name;
  $("#pageSub").textContent = tree.description || "";
  $("#pageActions").innerHTML =
    `<button class="btn" id="pdEdit">✎ Edit</button>
     <button class="btn primary" id="pdAddExp">＋ Experiment</button>
     <button class="btn" id="pdAddAnalysis">＋ Analysis</button>
     <a class="btn ghost" href="#/projects">← Projects</a>`;
  $("#pdEdit").onclick = () => openProjectForm({ id, name: tree.name, description: tree.description, tags: tree.tags });
  $("#pdAddExp").onclick = () => openExperimentForm(id);
  $("#pdAddAnalysis").onclick = () => openAnalysisForm({ project_id: id });

  const expHtml = tree.experiments.map(e => {
    const setHtml = e.sets.map(s => `
      <div class="treeset">
        <div class="treeset-head">
          <span class="tset-name">▸ ${esc(s.name)} <span class="muted">(${s.datasets.length})</span></span>
          <span class="wrap">${tagsPills(s.tags)}
            <button class="btn sm ghost" data-addds="${s.id}">＋ datasets</button>
            <button class="btn sm ghost" data-editset="${s.id}" data-setname="${esc(s.name)}" data-setexp="${e.id}">✎</button>
            <button class="btn sm ghost danger" data-delset="${s.id}">✕</button></span>
        </div>
        <div class="treeds">${s.datasets.length ? s.datasets.map(dsRow).join("")
          : `<div class="muted" style="padding:6px 10px;font-size:12px">No datasets — click “＋ datasets”.</div>`}</div>
      </div>`).join("");
    const directHtml = e.datasets.length ? `<div class="treeds">${e.datasets.map(dsRow).join("")}</div>` : "";
    const anaHtml = e.analyses.length ? `<div class="wrap" style="margin-top:6px">${e.analyses.map(a =>
      `<button class="chip accent" style="cursor:pointer" onclick="openAnalysisView(${a.id})">📊 ${esc(a.title)}</button>`).join(" ")}</div>` : "";
    return `<div class="panel" style="margin-bottom:14px">
      <div class="phead">
        <h3>${TYPE_ICON[e.type] || "◆"} ${esc(e.name)} <span class="chip">${esc(e.type)}</span></h3>
        <div class="wrap">
          <button class="btn sm" data-stats="${e.id}">📈 Stats</button>
          <button class="btn sm" data-export="${e.id}">⭳ Export</button>
          <button class="btn sm ghost" data-addset="${e.id}">＋ Set</button>
          <button class="btn sm ghost" data-editexp="${e.id}" data-expname="${esc(e.name)}" data-exptype="${esc(e.type)}">✎</button>
          <button class="btn sm ghost danger" data-delexp="${e.id}">✕</button>
        </div>
      </div>
      <div class="pbody">${setHtml || ""}${directHtml}${anaHtml || (setHtml ? "" : `<div class="muted" style="font-size:12px">Empty experiment — add a set or datasets.</div>`)}</div>
    </div>`;
  }).join("");
  const projAna = tree.analyses.length ? `<div class="panel"><div class="phead"><h3>Project analyses</h3></div>
    <div class="pbody"><div class="wrap">${tree.analyses.map(a =>
      `<button class="chip accent" style="cursor:pointer" onclick="openAnalysisView(${a.id})">📊 ${esc(a.title)}</button>`).join(" ")}</div></div></div>` : "";

  $("#content").innerHTML = `
    <div class="wrap" style="margin-bottom:12px">${tagsPills(tree.tags)}</div>
    ${expHtml || `<div class="empty"><div class="big">🧪</div>No experiments yet. Click <b>＋ Experiment</b>.</div>`}
    ${projAna}`;

  // wire experiment/set actions
  const c = $("#content");
  c.querySelectorAll("[data-addset]").forEach(b => b.onclick = () => openSetForm(+b.dataset.addset));
  c.querySelectorAll("[data-editexp]").forEach(b => b.onclick = () => openExperimentForm(id, { id: +b.dataset.editexp, name: b.dataset.expname, type: b.dataset.exptype }));
  c.querySelectorAll("[data-delexp]").forEach(b => b.onclick = () => confirmDel("experiments", +b.dataset.delexp, "experiment", () => renderProjectDetail(id)));
  c.querySelectorAll("[data-addds]").forEach(b => b.onclick = () => openAssignDatasets(+b.dataset.addds, id));
  c.querySelectorAll("[data-editset]").forEach(b => b.onclick = () => openSetForm(+b.dataset.setexp, { id: +b.dataset.editset, name: b.dataset.setname }));
  c.querySelectorAll("[data-delset]").forEach(b => b.onclick = () => confirmDel("sets", +b.dataset.delset, "set", () => renderProjectDetail(id)));
  c.querySelectorAll("[data-stats]").forEach(b => b.onclick = () => openExperimentStats(+b.dataset.stats));
  c.querySelectorAll("[data-export]").forEach(b => b.onclick = () => exportExperiment(+b.dataset.export));
  c.querySelectorAll("[data-ds]").forEach(el => el.onclick = () => openDataset(+el.dataset.ds));
}

function dsRow(d) {
  return `<div class="dsrow" data-ds="${d.id}">
    <span class="dsrow-ic">${TYPE_ICON[d.type] || "◆"}</span>
    <span class="dsrow-nm">${esc(d.name)}</span>
    <span class="muted mono">${d.slices ? d.slices + " sl" : ""} ${d.voxel_size_um ? "· " + vox(d.voxel_size_um) : ""}</span>
    ${(d.tags || []).length ? `<span class="wrap" style="margin-left:auto">${tagsPills(d.tags)}</span>` : ""}
  </div>`;
}

function openExperimentForm(projectId, e = null) {
  const types = ["uct", "scrna", "spatial", "omics", "other"];
  modal(e ? "Edit experiment" : "New experiment", `
    <div class="field"><label>Name</label><input class="input" id="efName" value="${esc(e ? e.name : "")}" placeholder="Experiment_002"></div>
    <div class="field"><label>Type</label><select class="input" id="efType">${types.map(t => `<option ${e && e.type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
  `, [{ label: e ? "Save" : "Create", cls: "primary", fn: async () => {
      const body = { name: $("#efName").value.trim(), type: $("#efType").value };
      if (!body.name) return toast("Name required", "err");
      try {
        if (e) await api("/experiments/" + e.id, { method: "PATCH", body: JSON.stringify(body) });
        else await api("/experiments", { method: "POST", body: JSON.stringify({ ...body, project_id: projectId }) });
        toast("Saved", "ok"); closeModal(); renderProjectDetail(projectId);
      } catch (err) { toast("Failed: " + err.message, "err"); }
    } }]);
}

function openSetForm(experimentId, s = null) {
  modal(s ? "Edit set" : "New set", `
    <div class="field"><label>Name</label><input class="input" id="sfName" value="${esc(s ? s.name : "")}" placeholder="Set1 [R13 treated]"></div>
  `, [{ label: s ? "Save" : "Create", cls: "primary", fn: async () => {
      const body = { name: $("#sfName").value.trim() };
      if (!body.name) return toast("Name required", "err");
      try {
        if (s) await api("/sets/" + s.id, { method: "PATCH", body: JSON.stringify(body) });
        else await api("/sets", { method: "POST", body: JSON.stringify({ ...body, experiment_id: experimentId }) });
        toast("Saved", "ok"); closeModal();
        // reload the project the experiment belongs to
        const exp = (await api("/experiments")).find(x => x.id === experimentId);
        renderProjectDetail(exp ? exp.project_id : (parseHash().arg));
      } catch (err) { toast("Failed: " + err.message, "err"); }
    } }]);
}

async function openAssignDatasets(setId, projectId) {
  const [unassigned, sets] = await Promise.all([
    api("/datasets?unassigned=true"), api("/sets")]);
  const current = await api("/datasets?set_id=" + setId);
  const pool = [...current, ...unassigned];
  if (!pool.length) { toast("No unassigned datasets to add. Ingest first.", "err"); return; }
  modal("Assign datasets to set", `
    <div class="hint" style="margin-bottom:8px">Tick datasets to include in this set. Unticking removes them from the set (the dataset itself is kept).</div>
    <div class="pick" id="adPick">${pool.map(d => `<label class="opt"><input type="checkbox" value="${d.id}" ${d.set_id === setId ? "checked" : ""}> ${TYPE_ICON[d.type] || "◆"} ${esc(d.name)} <span class="muted mono" style="margin-left:auto">${vox(d.voxel_size_um)}</span></label>`).join("")}</div>
  `, [{ label: "Save", cls: "primary", fn: async () => {
      const checked = new Set([...document.querySelectorAll("#adPick input:checked")].map(x => +x.value));
      try {
        for (const d of pool) {
          const want = checked.has(d.id);
          if (want && d.set_id !== setId) await api("/datasets/" + d.id, { method: "PATCH", body: JSON.stringify({ set_id: setId }) });
          else if (!want && d.set_id === setId) await api("/datasets/" + d.id, { method: "PATCH", body: JSON.stringify({ clear_set: true }) });
        }
        toast("Updated set", "ok"); closeModal(); renderProjectDetail(projectId);
      } catch (e) { toast("Failed: " + e.message, "err"); }
    } }]);
}

async function openExperimentStats(expId) {
  let st;
  try { st = await api("/experiments/" + expId + "/stats"); }
  catch (e) { return toast("Stats failed: " + e.message, "err"); }
  const grp = st.groups.map(g => {
    const s = g.stats;
    return `<tr><td>${esc(g.set)}</td><td class="num">${s.n}</td>
      <td class="num">${s.mean != null ? s.mean.toFixed(4) : "—"}</td>
      <td class="num">${s.sd != null ? s.sd.toFixed(4) : "—"}</td>
      <td class="num">${s.min != null ? s.min.toFixed(4) : "—"}</td>
      <td class="num">${s.max != null ? s.max.toFixed(4) : "—"}</td></tr>`;
  }).join("");
  const cmp = st.comparison ? `<div class="mcard" style="margin-top:10px"><div class="kv">
      <div class="k">Comparison</div><div class="v">${esc(st.comparison.a)} vs ${esc(st.comparison.b)}</div>
      <div class="k">Mean difference (mm³)</div><div class="v">${st.comparison.mean_diff != null ? st.comparison.mean_diff.toFixed(4) : "—"}</div>
      <div class="k">Test</div><div class="v">${esc(st.comparison.test || "—")}</div>
      <div class="k">p-value</div><div class="v">${st.comparison.p_value != null ? st.comparison.p_value.toExponential(2) : (st.comparison.note || "—")}</div>
      ${st.comparison.mannwhitney_p != null ? `<div class="k">Mann–Whitney p</div><div class="v">${st.comparison.mannwhitney_p.toExponential(2)}</div>` : ""}
    </div></div>` : `<div class="muted" style="margin-top:8px;font-size:12px">Add a second set with successful runs to get a comparison.</div>`;
  modal(`Statistics — ${esc(st.experiment)}`, `
    <div class="hint" style="margin-bottom:6px">Metric: ROI volume (mm³), from each dataset's latest successful run.</div>
    <table class="tbl"><thead><tr><th>Set</th><th class="right">n</th><th class="right">mean</th><th class="right">sd</th><th class="right">min</th><th class="right">max</th></tr></thead>
      <tbody>${grp || `<tr><td colspan="6" class="muted">No sets.</td></tr>`}</tbody></table>
    ${cmp}`, []);
}

async function exportExperiment(expId) {
  toast("Building export…");
  try {
    const r = await api("/experiments/" + expId + "/export", { method: "POST", body: JSON.stringify({}) });
    const name = r.path.split(/[\\/]/).pop();
    const a = document.createElement("a");
    a.href = "/api/experiments/" + expId + "/export/download?name=" + encodeURIComponent(name);
    a.click();
    toast(`Export ready (${fmtBytes(r.bytes)})`, "ok");
  } catch (e) { toast("Export failed: " + e.message, "err"); }
}

function openAnalysisForm(ctx) {
  modal("New analysis", `
    <div class="field"><label>Title</label><input class="input" id="afTitle" placeholder="R13 vs CTL"></div>
    <div class="field"><label>Type / comparison</label><input class="input" id="afType" placeholder="e.g. treated vs control"></div>
    <div class="field"><label>Files folder (relative to Analyses root)</label><input class="input" id="afPath" placeholder="Project_1/Exp002_R13_vs_CTL"><div class="hint">Where the R code + figure images live on the NAS. Browsed read-only through the app.</div></div>
    <div class="field"><label>Description</label><textarea class="input" id="afDesc" rows="2"></textarea></div>
  `, [{ label: "Create", cls: "primary", fn: async () => {
      const body = { title: $("#afTitle").value.trim(), type: $("#afType").value.trim(), files_relpath: $("#afPath").value.trim() || null, description: $("#afDesc").value, ...ctx };
      if (!body.title) return toast("Title required", "err");
      try { await api("/analyses", { method: "POST", body: JSON.stringify(body) }); toast("Analysis added", "ok"); closeModal();
        if (ctx.project_id) renderProjectDetail(ctx.project_id);
      } catch (e) { toast("Failed: " + e.message, "err"); }
    } }]);
}

window.openAnalysisView = async function (id) {
  const a = await api("/analyses/" + id);
  const files = await api("/analyses/" + id + "/files").catch(() => ({ exists: false, figures: [], files: [] }));
  const figs = files.figures.length ? `<div class="figgrid">${files.figures.map(f =>
    `<a class="figcard" href="${f.url}" target="_blank"><img src="${f.url}" loading="lazy"><div class="figname">${esc(f.name)}</div></a>`).join("")}</div>`
    : `<div class="muted" style="font-size:12px">No figure images found${files.exists ? " in this folder" : " — folder not found on this machine"}.</div>`;
  const code = files.files.length ? `<div class="section-title" style="margin-top:10px">Files</div>
    <table class="tbl"><tbody>${files.files.map(f => `<tr><td class="mono">${esc(f.name)}</td><td class="num muted">${fmtBytes(f.size)}</td></tr>`).join("")}</tbody></table>` : "";
  modal(`📊 ${esc(a.title)}`, `
    ${a.type ? `<div class="chip accent" style="margin-bottom:6px">${esc(a.type)}</div>` : ""}
    ${a.description ? `<p class="muted">${esc(a.description)}</p>` : ""}
    <div class="muted mono" style="font-size:11px;margin:6px 0">${esc(files.folder || a.files_relpath || "")}</div>
    <div class="section-title">Figures</div>${figs}${code}
  `, [{ label: "Delete analysis", cls: "danger", fn: () => confirmDel("analyses", id, "analysis", () => { closeModal(); if (a.project_id) renderProjectDetail(a.project_id); else route(); }) }]);
};

function confirmDel(kind, id, label, after) {
  modal(`Delete ${label}`, `<p>Delete this ${label}? ${kind === "analyses" ? "Only the record is removed — files on the NAS are untouched." : "Datasets and runs are never deleted; they are only unlinked."}</p>`,
    [{ label: "Delete", cls: "danger", fn: async () => {
        try { await api("/" + kind + "/" + id, { method: "DELETE" }); toast("Deleted", "ok"); closeModal(); if (after) after(); }
        catch (e) { toast("Failed: " + e.message, "err"); } } }]);
}

/* ---------------------------------------------------------------- timeline */
async function renderTimeline() {
  const { events } = await api("/timeline");
  if (!events.length) { $("#content").innerHTML = `<div class="empty"><div class="big">◔</div>Nothing yet.</div>`; return; }
  const ICON = { dataset: "▦", run: "▶", project: "▣" };
  const go = { dataset: e => openDataset(e.id), run: e => location.hash = "#/run/" + e.id, project: e => location.hash = "#/project/" + e.id };
  let lastDay = "";
  const rows = events.map(e => {
    const day = e.at ? new Date(e.at).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "—";
    const head = day !== lastDay ? `<div class="tl-day">${esc(day)}</div>` : "";
    lastDay = day;
    return `${head}<div class="tl-item" data-kind="${e.kind}" data-id="${e.id}">
      <span class="tl-ic ${e.kind}">${ICON[e.kind] || "•"}</span>
      <span class="tl-title">${esc(e.title)}</span>
      <span class="tl-detail">${esc(e.detail || "")}${e.dataset ? " · " + esc(e.dataset) : ""}</span>
      <span class="tl-time muted">${e.at ? new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
    </div>`;
  }).join("");
  $("#content").innerHTML = `<div class="timeline">${rows}</div>`;
  $("#content").querySelectorAll(".tl-item").forEach(el => el.onclick = () => {
    const ev = events.find(x => x.kind === el.dataset.kind && String(x.id) === el.dataset.id);
    if (ev && go[ev.kind]) go[ev.kind](ev);
  });
}

/* -------------------------------------------------------------------- boot */
async function boot() {
  loadLayout();
  $("#newRunBtn").onclick = () => openNewRun();
  const nt = $("#navToggle");
  if (nt) nt.onclick = () => {
    const a = document.querySelector(".app");
    const hid = a.classList.toggle("navhidden");
    try { localStorage.setItem("mlab_navhide", hid ? "1" : "0"); } catch { }
  };
  try { if (localStorage.getItem("mlab_navhide") === "1") document.querySelector(".app").classList.add("navhidden"); } catch { }
  try {
    const c = await api("/config");
    const comp = await api("/system/compute").catch(() => null);
    const dev = comp ? comp.recommended_device : c.default_device;
    const gpu = comp && comp.gpus && comp.gpus.length ? comp.gpus[0].name : (dev === "cuda" ? "GPU" : "CPU");
    $("#sysbox").innerHTML =
    `<b>Storage</b>
     <div class="row"><span>data</span><span title="${esc(c.data_root)}">${esc(c.data_root)}</span></div>
     <div class="row"><span>results</span><span title="${esc(c.results_root)}">${esc(c.results_root)}</span></div>
     ${c.nas_root ? `<div class="row"><span>NAS</span><span title="${esc(c.nas_root)}">${esc(c.nas_root)}</span></div>` : ""}
     <div class="row"><span>compute</span><span title="${esc(gpu)}">${esc(dev)} · ${esc(gpu)}</span></div>`;
  } catch { }
  try { VOCAB = (await api("/runs/vocab")).failure_modes; } catch { VOCAB = []; }
  route();
}
boot();
