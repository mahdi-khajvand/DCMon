const API = "";
let token = localStorage.getItem("dc_token") || "";
let sessionStartedAt = Date.now();
let me = null;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];


function classifyDeviceRole(name, model) {
  const s = `${name || ""} ${model || ""}`.toLowerCase();
  if (/firewall|asa-|palo|fortigate|checkpoint/.test(s)) return "firewall";
  if (/\bsw[-_ ]|switch|catalyst|nexus|2960|3850|9200|core-/.test(s)) return "switch";
  if (/router|srx|isr|asr|mikrotik|\bgate\b|edge-/.test(s)) return "router";
  if (/srv|server|proliant|poweredge|dl\d|r\d{3}/.test(s)) return "server";
  if (/san|storage|tape|storeever|nas|netapp|emc/.test(s)) return "storage";
  if (/patch\s*panel|patch-panel|pp-/.test(s)) return "patch";
  if (/cable\s*manag|cmanager|cable-management/.test(s)) return "cable";
  if (/power|ups|pdu|ats|protect/.test(s)) return "power";
  if (/\bkvm\b/.test(s)) return "kvm";
  return "other";
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status === 401) {
    logout(false);
    throw new Error("نشست منقضی شد");
  }
  if (!res.ok) {
    let msg = res.statusText;
    if (data) {
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = data.detail.map((x) => x.msg || JSON.stringify(x)).join(" | ");
      else if (data.message) msg = data.message;
    }
    throw new Error(msg);
  }
  return data;
}

function logout(show = true) {
  token = "";
  me = null;
  localStorage.removeItem("dc_token");
  if (show) {
    $("#appView").classList.add("hidden");
    $("#loginView").classList.remove("hidden");
  }
}

function showSetupModal(show) {
  const m = document.getElementById("setupModal");
  if (!m) return;
  m.classList.toggle("hidden", !show);
}

async function enterSetupMode() {
  try {
    const res = await fetch("/api/setup/enter", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "ورود راه‌اندازی ناموفق");
    token = data.token;
    localStorage.setItem("dc_token", token);
    me = { user: data.user, role: data.role, setup_mode: true, needs_setup: true };
    $("#loginView").classList.add("hidden");
    $("#appView").classList.remove("hidden");
    $("#userChip").textContent = `راه‌اندازی · admin`;
    showWelcomeSplash();
    setTimeout(() => showSetupModal(true), 600);
  } catch (e) {
    toast(e.message || String(e), true);
  }
}

async function checkSetupAndInitLogin() {
  try {
    const res = await fetch("/api/setup/status");
    const data = await res.json();
    const needs = !!(data && data.needs_setup);
    const form = document.getElementById("loginFormFields");
    const setupWrap = document.getElementById("setupEnterWrap");
    const sub = document.getElementById("loginSub");
    if (needs) {
      if (form) form.classList.add("hidden");
      if (setupWrap) setupWrap.classList.remove("hidden");
      if (sub) sub.textContent = "FIRST-TIME SETUP";
      // ورود خودکار بدون کلیک (طبق درخواست: بدون یوزر و پسورد)
      await enterSetupMode();
    } else {
      if (form) form.classList.remove("hidden");
      if (setupWrap) setupWrap.classList.add("hidden");
      if (sub) sub.textContent = "AUTHENTICATE TO INITIALIZE SESSION";
    }
  } catch (e) {
    // اگر API در دسترس نبود، فرم عادی
  }
}

async function doLogin() {
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#loginUser").value.trim(), password: $("#loginPass").value }),
    });
    token = data.token;
    localStorage.setItem("dc_token", token);
    me = data;
    $("#loginView").classList.add("hidden");
    $("#appView").classList.remove("hidden");
    $("#userChip").textContent = `${data.user} · ${data.role}`;
    sessionStartedAt = Date.now();
    showWelcomeSplash();
  } catch (e) {
    toast(e.message, true);
  }
}

function hero(badge, title, sub) {
  return `<div class="hero"><div class="eyebrow">${badge}</div><h2>${title}</h2><p>${sub}</p></div>`;
}

function showPage(name) {
  $$(".page").forEach((p) => p.classList.add("hidden"));
  const page = $(`#page-${name}`);
  if (page) page.classList.remove("hidden");
  $$("#nav button[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  // expand parent group of active item
  const activeBtn = $(`#nav button[data-page="${name}"]`);
  if (activeBtn) {
    const group = activeBtn.closest(".nav-group");
    if (group) group.classList.remove("collapsed");
  }
  const map = {
    dashboard: loadDashboard,
    dcs: loadDcs,
    racks: loadRacks,
    devices: loadDevices,
    placements: loadPlacements,
    rackview: loadRackView,
    dndPlace: loadDndPlace,
    topology: loadTopology,
    monDevices: loadMonDevices,
    monPerf: loadMonPerf,
    monAlerts: loadMonAlerts,
    monTraps: loadMonTraps,
    discovery: loadDiscovery,
    addMon: loadAddMon,
    users: loadUsers,
    bale: loadBale,
    storage: loadStorage,
  };
  if (map[name]) map[name]();
}


/* Dashboard — inventory-focused overview */
async function loadDashboard() {
  const el = $("#page-dashboard");
  try {
    const [s, elev, racksList, devicesList] = await Promise.all([
      api("/api/summary"),
      api("/api/topology?mode=elevation").catch(() => ({ racks: [], devices: [], links: [] })),
      api("/api/racks").catch(() => []),
      api("/api/devices").catch(() => []),
    ]);
    const usedU = Math.max(0, (s.used_units != null ? s.used_units : (s.total_units || 0) - (s.free_units || 0)));
    const totalU = s.total_units || 1;
    const freeU = s.free_units != null ? s.free_units : Math.max(0, totalU - usedU);
    const usePct = Math.round((usedU / Math.max(1, totalU)) * 100);
    const connTypes = s.connections_by_type || {};
    const rackCount = s.racks || (elev.racks || []).length || 0;
    const deviceCount = s.devices || (devicesList || []).length || 0;
    const topoLinks = s.connections || (elev.links || []).length || 0;
    const placements = s.placements || 0;
    const dcList = s.dc_racks || [];
    const dcCount = dcList.length || 0;

    // per-rack utilization from racks + placements summary if available
    const rackRows = (racksList || []).slice(0, 8).map((r) => {
      const name = r.rack_name || r.name || `R${r.rack_id}`;
      const tu = +r.total_units || 42;
      return { name, total: tu, id: r.rack_id };
    });

    el.innerHTML = `
      ${hero("NOC OVERVIEW", "Dashboard", "خلاصه زیرساخت · ظرفیت رک · توپولوژی · دیتاسنترها")}
      <div class="dash-kpis">
        <div class="kpi-card kpi-purple">
          <div class="kpi-ring" style="--p:${Math.min(100, usePct)}">
            <span>${usePct}%</span>
          </div>
          <div>
            <div class="kpi-label">ظرفیت یونیت</div>
            <div class="kpi-sub">${usedU}U استفاده · ${freeU}U آزاد</div>
          </div>
        </div>
        <div class="kpi-card kpi-cyan">
          <div class="kpi-num">${rackCount}</div>
          <div>
            <div class="kpi-label">رک‌ها</div>
            <div class="kpi-sub">${dcCount} دیتاسنتر · ${placements} جای‌گذاری</div>
          </div>
        </div>
        <div class="kpi-card kpi-green">
          <div class="kpi-num">${deviceCount}</div>
          <div>
            <div class="kpi-label">دستگاه‌ها</div>
            <div class="kpi-sub">${totalU}U ظرفیت کل</div>
          </div>
        </div>
        <div class="kpi-card kpi-amber">
          <div class="kpi-num">${topoLinks}</div>
          <div>
            <div class="kpi-label">اتصالات</div>
            <div class="kpi-sub">${Object.keys(connTypes).length ? Object.keys(connTypes).join(" · ") : "توپولوژی"}</div>
          </div>
        </div>
      </div>

      <div class="dash-grid">
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Capacity (Rack Units)</h3>
          <div id="dashCapChart" class="dash-chart"></div>
          <div class="dash-cap-bar">
            <div class="dash-cap-fill" style="width:${usePct}%"></div>
          </div>
          <div class="dash-cap-legend">
            <span>Used ${usedU}U (${usePct}%)</span>
            <span>Free ${freeU}U</span>
          </div>
        </div>
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Inventory Mix</h3>
          <div id="dashInvChart" class="dash-chart"></div>
        </div>
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Link Types</h3>
          <div id="dashLinkChart" class="dash-chart"></div>
        </div>
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Datacenters</h3>
          <div id="dashDcChart" class="dash-chart"></div>
        </div>
      </div>

      <div class="dash-grid-2">
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Rack Capacity Snapshot</h3>
          <div class="dash-device-list">
            ${rackRows.length ? rackRows.map((r) => `
              <div class="dash-dev-row">
                <div class="dash-dev-left">
                  <span class="dash-dev-dot up"></span>
                  <div>
                    <div class="dash-dev-name">${r.name}</div>
                    <div class="muted mono" style="font-size:.7rem">ID ${r.id}</div>
                  </div>
                </div>
                <div class="dash-dev-metrics">
                  <span class="badge badge-muted">${r.total}U</span>
                </div>
              </div>`).join("") : `<p class="muted">رکی ثبت نشده</p>`}
          </div>
        </div>
        <div class="panel dash-panel">
          <h3><span class="dot"></span> Quick Access</h3>
          <div class="dash-alert-list" style="gap:10px">
            <div class="dash-alert ok" style="cursor:default">
              <div class="dash-alert-sev">DC</div>
              <div><div class="dash-alert-msg">${dcCount} دیتاسنتر</div><div class="muted" style="font-size:.72rem">${rackCount} رک · ${deviceCount} دستگاه</div></div>
            </div>
            <div class="dash-alert ok" style="cursor:default">
              <div class="dash-alert-sev">CAP</div>
              <div><div class="dash-alert-msg">${usePct}% اشغال یونیت</div><div class="muted" style="font-size:.72rem">${freeU}U آزاد از ${totalU}U</div></div>
            </div>
            <div class="dash-alert ok" style="cursor:default">
              <div class="dash-alert-sev">TOPO</div>
              <div><div class="dash-alert-msg">${topoLinks} اتصال</div><div class="muted" style="font-size:.72rem">${placements} جای‌گذاری فعال</div></div>
            </div>
          </div>
          <div class="dash-quick">
            <button class="btn btn-ghost btn-sm" data-go="dcs">Datacenters</button>
            <button class="btn btn-ghost btn-sm" data-go="racks">Racks</button>
            <button class="btn btn-ghost btn-sm" data-go="rackview">Rack View</button>
            <button class="btn btn-ghost btn-sm" data-go="topology">Topology</button>
            <button class="btn btn-ghost btn-sm" data-go="placements">Placement</button>
          </div>
        </div>
      </div>
    `;

    el.querySelectorAll("[data-go]").forEach((b) => {
      b.onclick = () => {
        const page = b.getAttribute("data-go");
        const nav = document.querySelector(`[data-page="${page}"]`);
        if (nav) nav.click();
      };
    });

    if (window.Plotly) {
      const dark = { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#94a3b8", family: "Vazirmatn, Inter, sans-serif", size: 11 },
        margin: { t: 10, b: 30, l: 40, r: 10 }, showlegend: false };

      Plotly.newPlot("dashCapChart", [{
        type: "indicator",
        mode: "gauge+number",
        value: usePct,
        number: { suffix: "%", font: { size: 28, color: "#e2e8f0" } },
        gauge: {
          axis: { range: [0, 100], tickcolor: "#475569" },
          bar: { color: "#c084fc" },
          bgcolor: "rgba(15,23,42,.5)",
          borderwidth: 0,
          steps: [
            { range: [0, 50], color: "rgba(52,211,153,.15)" },
            { range: [50, 80], color: "rgba(251,191,36,.15)" },
            { range: [80, 100], color: "rgba(248,113,113,.15)" },
          ],
        },
        title: { text: "Unit utilization", font: { size: 12, color: "#64748b" } },
      }], { ...dark, height: 220, margin: { t: 40, b: 10, l: 20, r: 20 } }, { displayModeBar: false, responsive: true });

      Plotly.newPlot("dashInvChart", [{
        type: "pie",
        labels: ["Racks", "Devices", "Placements", "Links"],
        values: [rackCount || 1, deviceCount || 1, placements || 1, topoLinks || 1],
        hole: 0.62,
        marker: { colors: ["#e9d5ff", "#a78bfa", "#34d399", "#fbbf24"] },
        textinfo: "label+value",
        textfont: { size: 11 },
      }], { ...dark, height: 220 }, { displayModeBar: false, responsive: true });

      const ltLabels = Object.keys(connTypes).length ? Object.keys(connTypes) : (topoLinks ? ["links"] : ["—"]);
      const ltValues = Object.keys(connTypes).length ? ltLabels.map((k) => connTypes[k]) : [topoLinks || 0];
      Plotly.newPlot("dashLinkChart", [{
        type: "bar",
        x: ltLabels.map((x) => String(x).toUpperCase()),
        y: ltValues,
        marker: {
          color: ltLabels.map((t) => {
            const x = String(t).toLowerCase();
            if (x.includes("fiber")) return "#4ade80";
            if (x.includes("copper")) return "#fb923c";
            if (x.includes("power")) return "#facc15";
            return "#60a5fa";
          }),
        },
      }], { ...dark, height: 220 }, { displayModeBar: false, responsive: true });

      if (dcList.length) {
        Plotly.newPlot("dashDcChart", [{
          type: "bar",
          orientation: "h",
          y: dcList.map((d) => d.name),
          x: dcList.map((d) => d.racks),
          marker: { color: "#e9d5ff" },
        }], { ...dark, height: 220, margin: { t: 10, b: 30, l: 80, r: 10 } }, { displayModeBar: false, responsive: true });
      } else {
        Plotly.newPlot("dashDcChart", [{
          type: "bar",
          x: ["Racks", "Devices", "Links"],
          y: [rackCount, deviceCount, topoLinks],
          marker: { color: ["#c084fc", "#a78bfa", "#34d399"] },
        }], { ...dark, height: 220 }, { displayModeBar: false, responsive: true });
      }
    }
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
    toast(e.message, true);
  }
}

/* DCs + Visual Map */
async function loadDcs() {
  const el = $("#page-dcs");
  try {
    const data = await api("/api/datacenters");
    const dcs = data.datacenters || [];
    const links = data.links || [];
    const dcOpts = dcs.map((d) => `<option value="${d.datacenter_id}">${d.datacenter_name} (#${d.datacenter_id})</option>`).join("")
      || '<option value="">—</option>';

    el.innerHTML = `
      ${hero("DATACENTER", "Overview & Interconnect", "مدیریت سایت‌ها · لینک‌های DCI · نقشه ویژوال")}

      <div class="dc-sections">
        <!-- Section 1: Manage DCs -->
        <div class="panel dc-section">
          <h3><span class="dot"></span> مدیریت دیتاسنترها</h3>
          <p class="muted" style="margin:0 0 10px;font-size:.78rem">ساخت و حذف سایت‌های دیتاسنتر</p>
          <div class="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Location</th><th></th></tr></thead>
          <tbody>${dcs.map((d) => `<tr>
            <td>${d.datacenter_id}</td><td>${d.datacenter_name}</td><td>${d.location || ""}</td>
            <td><button class="btn btn-danger btn-sm btn-del" data-deldc="${d.datacenter_id}" title="حذف">حذف</button></td>
          </tr>`).join("") || "<tr><td colspan=4 class=muted>هنوز دیتاسنتری نیست</td></tr>"}</tbody></table></div>
          <div style="margin-top:12px" class="grid-2">
            <label class="field"><span>Name</span><input id="dcName" placeholder="مثلاً setad" /></label>
            <label class="field"><span>Location</span><input id="dcLoc" placeholder="مثلاً floor 3" /></label>
          </div>
          <button class="btn btn-primary" id="dcAdd" style="margin-top:8px">＋ افزودن دیتاسنتر</button>
        </div>

        <!-- Section 2: DCI Links CRUD -->
        <div class="panel dc-section">
          <h3><span class="dot"></span> ارتباطات دیتاسنترها (DCI)</h3>
          <p class="muted" style="margin:0 0 10px;font-size:.78rem">ساخت و حذف لینک بین سایت‌ها</p>
          <div class="table-wrap"><table><thead><tr><th>ID</th><th>Src</th><th>Dst</th><th>Type</th><th>Gbps</th><th>Status</th><th></th></tr></thead>
          <tbody>${links.map((l) => `<tr>
            <td>${l.link_id}</td><td>${l.source_dc_id}</td><td>${l.destination_dc_id}</td>
            <td>${l.link_type}</td><td>${l.capacity_gbps}</td><td>${l.status}</td>
            <td><button class="btn btn-danger btn-sm" data-dellink="${l.link_id}">حذف</button></td>
          </tr>`).join("") || "<tr><td colspan=7 class=muted>لینکی ثبت نشده</td></tr>"}</tbody></table></div>
          <div class="grid-2" style="margin-top:12px">
            <label class="field"><span>Source DC</span><select id="lkSrc">${dcOpts}</select></label>
            <label class="field"><span>Dest DC</span><select id="lkDst">${dcOpts}</select></label>
            <label class="field"><span>Type</span><input id="lkType" value="Dark Fiber" /></label>
            <label class="field"><span>Capacity Gbps</span><input id="lkCap" type="number" value="100" /></label>
          </div>
          <button class="btn btn-primary" id="lkAdd" style="margin-top:8px">＋ افزودن لینک</button>
        </div>
      </div>

      <!-- Section 3: Visual Map -->
      <div class="panel dc-map-panel" id="dcMapPanel">
        <div class="dc-map-toolbar">
          <div class="dc-map-title">
            <span class="dc-map-live"></span>
            <h3 style="margin:0">نقشه ویژوال ارتباطات</h3>
          </div>
          <div class="dc-map-actions">
            <label class="dc-map-btn" title="آپلود تصویر پس‌زمینه (نقشه شهر و...)">
              🖼️ بک‌گراند
              <input type="file" id="dcMapBgFile" accept="image/jpeg,image/png,image/webp" hidden />
            </label>
            <button type="button" class="dc-map-btn" id="dcMapBgClear" title="حذف بک‌گراند">✕ بک‌گراند</button>
            <label class="dc-map-opacity" title="شفافیت بک‌گراند">
              <span>Opacity</span>
              <input type="range" id="dcMapOpacity" min="0" max="100" value="45" />
            </label>
            <button type="button" class="dc-map-btn" id="dcMapResetView" title="بازنشانی نما">↺ نما</button>
            <button type="button" class="dc-map-btn primary" id="dcMapFullscreen" title="تمام‌صفحه">⛶ تمام‌صفحه</button>
          </div>
        </div>
        <div class="dc-map-stage" id="dcMapStage">
          <div class="dc-map-world" id="dcMapWorld">
            <div class="dc-map-bg" id="dcMapBg"></div>
            <svg class="dc-map-svg" id="dcMapSvg" viewBox="0 0 1000 1000" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
              <defs>
                <linearGradient id="dcLinkGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stop-color="#38bdf8"/>
                  <stop offset="50%" stop-color="#a78bfa"/>
                  <stop offset="100%" stop-color="#22d3ee"/>
                </linearGradient>
                <filter id="dcGlow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="3" result="blur"/>
                  <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
                </filter>
              </defs>
              <g id="dcMapLinks"></g>
              <g id="dcMapParticles"></g>
            </svg>
            <div class="dc-map-nodes" id="dcMapNodes"></div>
            <div class="dc-link-tooltip hidden" id="dcLinkTooltip"></div>
          </div>
        </div>
      </div>`;

    $("#dcAdd").onclick = async () => {
      try {
        await api("/api/datacenters", { method: "POST", body: JSON.stringify({ datacenter_name: $("#dcName").value, location: $("#dcLoc").value }) });
        toast("ثبت شد"); loadDcs();
      } catch (e) { toast(e.message, true); }
    };
    $("#lkAdd").onclick = async () => {
      try {
        await api("/api/dci-links", {
          method: "POST",
          body: JSON.stringify({
            source_dc_id: +$("#lkSrc").value,
            destination_dc_id: +$("#lkDst").value,
            link_type: $("#lkType").value,
            capacity_gbps: +$("#lkCap").value,
          }),
        });
        toast("لینک ثبت شد"); loadDcs();
      } catch (e) { toast(e.message, true); }
    };
    el.onclick = async (ev) => {
      const linkBtn = ev.target.closest("[data-dellink]");
      if (linkBtn) {
        try {
          await api(`/api/dci-links/${linkBtn.dataset.dellink}`, { method: "DELETE" });
          loadDcs();
        } catch (e) { toast(e.message, true); }
        return;
      }
      const dcBtn = ev.target.closest("[data-deldc]");
      if (dcBtn) {
        if (!confirm("دیتاسنتر و رک‌های زیرمجموعه حذف شوند؟")) return;
        try {
          await api(`/api/datacenters/${dcBtn.dataset.deldc}`, { method: "DELETE" });
          toast("دیتاسنتر حذف شد");
          loadDcs();
        } catch (e) { toast(e.message, true); }
      }
    };

    // Visual map init
    initDcVisualMap(dcs, links);
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

function initDcVisualMap(dcs, links) {
  const stage = document.getElementById("dcMapStage");
  const world = document.getElementById("dcMapWorld");
  const nodesEl = document.getElementById("dcMapNodes");
  const linksG = document.getElementById("dcMapLinks");
  const particlesG = document.getElementById("dcMapParticles");
  const bgEl = document.getElementById("dcMapBg");
  const panel = document.getElementById("dcMapPanel");
  if (!stage || !world || !nodesEl) return;

  // state
  let scale = 1;
  let panX = 0, panY = 0;
  let panning = false;
  let panStart = { x: 0, y: 0, px: 0, py: 0 };
  let dragging = null; // { id, el, startX, startY, origLeft, origTop }
  let saveTimer = null;
  const nodePos = {}; // id -> {x,y} percent
  const nodeEls = {};

  // auto-layout if no positions
  function autoLayout(list) {
    const n = list.length || 1;
    const cx = 50, cy = 50, r = Math.min(38, 12 + n * 4);
    list.forEach((d, i) => {
      const id = String(d.datacenter_id);
      if (nodePos[id] && nodePos[id].x != null) return;
      const a = (i / n) * Math.PI * 2 - Math.PI / 2;
      nodePos[id] = {
        x: cx + Math.cos(a) * r,
        y: cy + Math.sin(a) * r,
      };
    });
  }

  // load saved map settings
  (async () => {
    try {
      const map = await api("/api/datacenters/map");
      (map.nodes || []).forEach((n) => {
        if (n.x != null && n.y != null) {
          nodePos[String(n.datacenter_id)] = { x: +n.x, y: +n.y };
        }
      });
      if (map.background) {
        bgEl.style.backgroundImage = `url(${map.background}?t=${Date.now()})`;
        bgEl.classList.add("has-img");
      }
      const op = Math.round((map.bg_opacity != null ? map.bg_opacity : 0.45) * 100);
      const opInput = document.getElementById("dcMapOpacity");
      if (opInput) opInput.value = op;
      bgEl.style.opacity = String((map.bg_opacity != null ? map.bg_opacity : 0.45));
    } catch (e) { /* ignore */ }
    autoLayout(dcs);
    renderNodes();
    renderLinks();
    startParticles();
  })();

  function applyTransform() {
    world.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
  }

  function renderNodes() {
    nodesEl.innerHTML = "";
    dcs.forEach((d) => {
      const id = String(d.datacenter_id);
      const pos = nodePos[id] || { x: 50, y: 50 };
      const node = document.createElement("div");
      node.className = "dc-node";
      node.dataset.id = id;
      node.style.left = pos.x + "%";
      node.style.top = pos.y + "%";
      node.innerHTML = `
        <div class="dc-node-ring"></div>
        <div class="dc-node-core">
          <span class="dc-node-icon">🏢</span>
        </div>
        <div class="dc-node-label">
          <b>${(d.datacenter_name || "DC").replace(/</g, "&lt;")}</b>
          <small>#${id}${d.location ? " · " + String(d.location).replace(/</g, "&lt;") : ""}</small>
        </div>`;
      nodesEl.appendChild(node);
      nodeEls[id] = node;

      node.addEventListener("pointerdown", (ev) => {
        if (ev.button !== 0) return;
        ev.preventDefault();
        ev.stopPropagation();
        node.setPointerCapture(ev.pointerId);
        dragging = {
          id,
          el: node,
          startX: ev.clientX,
          startY: ev.clientY,
          origX: pos.x,
          origY: pos.y,
        };
        node.classList.add("dragging");
      });
    });
  }

  function pctFromEvent(ev) {
    const rect = world.getBoundingClientRect();
    // account for current scale: world is scaled, rect is transformed
    const x = ((ev.clientX - rect.left) / rect.width) * 100;
    const y = ((ev.clientY - rect.top) / rect.height) * 100;
    return {
      x: Math.max(2, Math.min(98, x)),
      y: Math.max(2, Math.min(98, y)),
    };
  }

  function renderLinks() {
    if (!linksG) return;
    linksG.innerHTML = "";
    const tip = document.getElementById("dcLinkTooltip");
    links.forEach((l, idx) => {
      const a = nodePos[String(l.source_dc_id)];
      const b = nodePos[String(l.destination_dc_id)];
      if (!a || !b) return;
      const x1 = a.x * 10, y1 = a.y * 10, x2 = b.x * 10, y2 = b.y * 10;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2 - 30 - (idx % 3) * 15;
      const d = `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;

      // hit area (invisible, easier hover)
      const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
      hit.setAttribute("d", d);
      hit.setAttribute("class", "dc-link-hit");
      hit.setAttribute("data-link", l.link_id);
      linksG.appendChild(hit);

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "dc-link-path");
      path.setAttribute("data-link", l.link_id);
      path.setAttribute("filter", "url(#dcGlow)");
      linksG.appendChild(path);

      const tipHtml =
        "<b>" + (l.link_type || "Link") + "</b>" +
        "<span>" + (l.capacity_gbps != null ? l.capacity_gbps + " Gbps" : "") + "</span>" +
        (l.status ? "<span class='st'>" + l.status + "</span>" : "");

      const showTip = (ev) => {
        if (!tip) return;
        tip.innerHTML = tipHtml;
        tip.classList.remove("hidden");
        path.classList.add("hot");
        hit.classList.add("hot");
        // position relative to world
        const rect = world.getBoundingClientRect();
        const x = ((ev.clientX - rect.left) / rect.width) * 100;
        const y = ((ev.clientY - rect.top) / rect.height) * 100;
        tip.style.left = x + "%";
        tip.style.top = y + "%";
      };
      const hideTip = () => {
        if (tip) tip.classList.add("hidden");
        path.classList.remove("hot");
        hit.classList.remove("hot");
      };
      hit.addEventListener("pointerenter", showTip);
      hit.addEventListener("pointermove", showTip);
      hit.addEventListener("pointerleave", hideTip);
    });
  }

  let particleAnim = null;
  function startParticles() {
    if (particleAnim) cancelAnimationFrame(particleAnim);
    const particles = [];
    links.forEach((l, idx) => {
      for (let i = 0; i < 3; i++) {
        particles.push({ linkIdx: idx, t: Math.random(), speed: 0.003 + Math.random() * 0.004 });
      }
    });

    function frame() {
      if (!particlesG || !document.body.contains(stage)) return;
      particlesG.innerHTML = "";
      particles.forEach((p) => {
        const l = links[p.linkIdx];
        if (!l) return;
        const a = nodePos[String(l.source_dc_id)];
        const b = nodePos[String(l.destination_dc_id)];
        if (!a || !b) return;
        p.t += p.speed;
        if (p.t > 1) p.t = 0;
        const t = p.t;
        // quadratic bezier
        const x1 = a.x * 10, y1 = a.y * 10, x2 = b.x * 10, y2 = b.y * 10;
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2 - 30 - (p.linkIdx % 3) * 15;
        const x = (1 - t) * (1 - t) * x1 + 2 * (1 - t) * t * mx + t * t * x2;
        const y = (1 - t) * (1 - t) * y1 + 2 * (1 - t) * t * my + t * t * y2;
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        c.setAttribute("cx", x);
        c.setAttribute("cy", y);
        c.setAttribute("r", 3.5);
        c.setAttribute("class", "dc-particle");
        particlesG.appendChild(c);
      });
      particleAnim = requestAnimationFrame(frame);
    }
    particleAnim = requestAnimationFrame(frame);
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const positions = Object.keys(nodePos).map((id) => ({
        datacenter_id: +id,
        x: nodePos[id].x,
        y: nodePos[id].y,
      }));
      try {
        await api("/api/datacenters/layout", {
          method: "POST",
          body: JSON.stringify({ positions }),
        });
      } catch (e) { /* silent */ }
    }, 400);
  }

  // pointer move / up on stage
  stage.addEventListener("pointermove", (ev) => {
    if (dragging) {
      const pct = pctFromEvent(ev);
      nodePos[dragging.id] = { x: pct.x, y: pct.y };
      dragging.el.style.left = pct.x + "%";
      dragging.el.style.top = pct.y + "%";
      renderLinks();
      return;
    }
    if (panning) {
      panX = panStart.px + (ev.clientX - panStart.x);
      panY = panStart.py + (ev.clientY - panStart.y);
      applyTransform();
    }
  });
  stage.addEventListener("pointerup", (ev) => {
    if (dragging) {
      dragging.el.classList.remove("dragging");
      dragging = null;
      scheduleSave();
    }
    panning = false;
    stage.classList.remove("panning");
  });
  stage.addEventListener("pointerleave", () => {
    if (dragging) {
      dragging.el.classList.remove("dragging");
      dragging = null;
      scheduleSave();
    }
    panning = false;
    stage.classList.remove("panning");
  });
  stage.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest(".dc-node")) return;
    if (ev.button !== 0) return;
    panning = true;
    stage.classList.add("panning");
    panStart = { x: ev.clientX, y: ev.clientY, px: panX, py: panY };
  });

  // zoom
  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const delta = ev.deltaY > 0 ? 0.9 : 1.1;
    const next = Math.max(0.4, Math.min(3, scale * delta));
    scale = next;
    applyTransform();
  }, { passive: false });

  // toolbar
  const bgFile = document.getElementById("dcMapBgFile");
  if (bgFile) {
    bgFile.onchange = async () => {
      const f = bgFile.files && bgFile.files[0];
      if (!f) return;
      try {
        const fd = new FormData();
        fd.append("file", f);
        const token = localStorage.getItem("dc_token") || "";
        const headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        const res = await fetch("/api/datacenters/map/background", { method: "POST", headers, body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "upload failed");
        bgEl.style.backgroundImage = `url(${data.url}?t=${Date.now()})`;
        bgEl.classList.add("has-img");
        toast("بک‌گراند تنظیم شد");
      } catch (e) { toast(e.message, true); }
      bgFile.value = "";
    };
  }
  const bgClear = document.getElementById("dcMapBgClear");
  if (bgClear) {
    bgClear.onclick = async () => {
      try {
        await api("/api/datacenters/map/background", { method: "DELETE" });
        bgEl.style.backgroundImage = "";
        bgEl.classList.remove("has-img");
        toast("بک‌گراند حذف شد");
      } catch (e) { toast(e.message, true); }
    };
  }
  const opInput = document.getElementById("dcMapOpacity");
  if (opInput) {
    let opTimer = null;
    opInput.oninput = () => {
      const v = (+opInput.value) / 100;
      bgEl.style.opacity = String(v);
      clearTimeout(opTimer);
      opTimer = setTimeout(async () => {
        try {
          await api("/api/datacenters/map/opacity", {
            method: "POST",
            body: JSON.stringify({ bg_opacity: v }),
          });
        } catch (e) { /* ignore */ }
      }, 300);
    };
  }
  const resetBtn = document.getElementById("dcMapResetView");
  if (resetBtn) {
    resetBtn.onclick = () => {
      scale = 1; panX = 0; panY = 0;
      applyTransform();
    };
  }
  const fsBtn = document.getElementById("dcMapFullscreen");
  if (fsBtn) {
    fsBtn.onclick = () => {
      panel.classList.toggle("dc-map-fs");
      document.body.classList.toggle("dc-map-fs-active", panel.classList.contains("dc-map-fs"));
      fsBtn.textContent = panel.classList.contains("dc-map-fs") ? "✕ خروج از تمام‌صفحه" : "⛶ تمام‌صفحه";
      // resize handled by CSS
    };
  }

  // ESC exit fullscreen
  const onKey = (ev) => {
    if (ev.key === "Escape" && panel.classList.contains("dc-map-fs")) {
      panel.classList.remove("dc-map-fs");
      document.body.classList.remove("dc-map-fs-active");
      if (fsBtn) fsBtn.textContent = "⛶ تمام‌صفحه";
    }
  };
  document.addEventListener("keydown", onKey);
}

/* Racks */
async function loadRacks() {
  const el = $("#page-racks");
  try {
    const racks = await api("/api/racks");
    const dcs = (await api("/api/datacenters")).datacenters || [];
    el.innerHTML = `
      ${hero("RACK MANAGEMENT", "Inventory — Racks", "Create and manage racks")}
      <div class="panel">
        <div class="grid-2">
          <label class="field"><span>Rack name</span><input id="rkName" placeholder="R-01" /></label>
          <label class="field"><span>Datacenter</span>
            <select id="rkDc">${dcs.map((d) => `<option value="${d.datacenter_id}">${d.datacenter_name}</option>`).join("") || '<option value="1">1</option>'}</select>
          </label>
          <label class="field"><span>Total units</span><input id="rkU" type="number" value="42" /></label>
          <label class="field"><span>Description</span><input id="rkDesc" /></label>
        </div>
        <button class="btn btn-primary" id="rkAdd">Commit rack</button>
      </div>
      <div class="panel">
        <h3><span class="dot"></span> Racks <span class="muted" style="font-weight:600;font-size:.8rem">· ${racks.length}</span></h3>
        <div class="rk-grid">
          ${racks.length ? racks.map((r) => {
            const u = +r.total_units || 42;
            const slots = Math.min(12, Math.max(6, Math.round(u / 4)));
            const bars = Array.from({ length: slots }, (_, i) => {
              // decorative occupancy pattern
              const filled = i % 3 !== 1;
              return `<i class="${filled ? "on" : ""}"></i>`;
            }).join("");
            const dcName = (dcs.find((d) => String(d.datacenter_id) === String(r.datacenter_id)) || {}).datacenter_name || r.datacenter_id;
            return `
            <article class="rk-card" data-rack-id="${r.rack_id}">
              <div class="rk-mini">
                <div class="rk-mini-top"></div>
                <div class="rk-mini-body">${bars}</div>
                <div class="rk-mini-bot"></div>
              </div>
              <div class="rk-info">
                <div class="rk-name">${r.rack_name || "Rack"}</div>
                <div class="rk-meta">
                  <span class="rk-pill">${u}U</span>
                  <span class="rk-pill soft">DC · ${dcName}</span>
                  <span class="rk-pill soft mono">#${r.rack_id}</span>
                </div>
                ${r.description ? `<div class="rk-desc">${r.description}</div>` : `<div class="rk-desc muted">بدون توضیح</div>`}
                <button type="button" class="btn btn-danger btn-sm btn-del rk-del" data-delrack="${r.rack_id}">حذف رک</button>
              </div>
            </article>`;
          }).join("") : `<p class="muted">رکی ثبت نشده</p>`}
        </div>
      </div>`;
    el.querySelectorAll("[data-delrack]").forEach((b) => {
      b.onclick = async (ev) => {
        ev.stopPropagation();
        if (!confirm("این رک، placementها و اتصالات مرتبط حذف شوند؟")) return;
        try {
          await api(`/api/racks/${b.dataset.delrack}`, { method: "DELETE" });
          toast("رک حذف شد");
          loadRacks();
        } catch (e) { toast(e.message, true); }
      };
    });
    $("#rkAdd").onclick = async () => {
      try {
        const res = await api("/api/racks", {
          method: "POST",
          body: JSON.stringify({
            rack_name: $("#rkName").value,
            datacenter_id: +$("#rkDc").value,
            total_units: +$("#rkU").value,
            description: $("#rkDesc").value,
          }),
        });
        toast(res.message); loadRacks();
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Devices */
async function loadDevices() {
  const el = $("#page-devices");
  try {
    const devices = await api("/api/devices");
    const dcs = (await api("/api/datacenters")).datacenters || [];
    el.innerHTML = `
      ${hero("DEVICE MANAGEMENT", "Inventory — Devices", "Register assets")}
      <div class="panel">
        <div class="grid-3">
          <label class="field"><span>Name</span><input id="dvName" /></label>
          <label class="field"><span>Model</span><input id="dvModel" /></label>
          <label class="field"><span>Units</span><input id="dvU" type="number" value="1" /></label>
          <label class="field"><span>Net ports</span><input id="dvNet" type="number" value="24" /></label>
          <label class="field"><span>Power ports</span><input id="dvPwr" type="number" value="2" /></label>
          <label class="field"><span>Fiber ports</span><input id="dvFib" type="number" value="0" /></label>
          <label class="field"><span>DC</span>
            <select id="dvDc">${dcs.map((d) => `<option value="${d.datacenter_id}">${d.datacenter_name}</option>`).join("") || '<option value="1">1</option>'}</select>
          </label>
          <label class="field"><span>Description</span><input id="dvDesc" /></label>
        </div>
        <button class="btn btn-primary" id="dvAdd">Commit device</button>
      </div>
      <div class="panel"><h3><span class="dot"></span> Devices</h3>
        <div class="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Model</th><th>U</th><th>Ports</th><th>DC</th><th></th></tr></thead>
        <tbody>${devices.map((d) => `<tr>
          <td>${d.device_id}</td><td>${d.device_name}</td><td>${d.model || ""}</td>
          <td>${d.units_used}</td><td>N${d.network_ports}/P${d.power_ports}/F${d.fiber_ports}</td><td>${d.datacenter_id || ""}</td>
          <td><button class="btn btn-danger btn-sm btn-del" data-deldev="${d.device_id}" title="حذف">حذف</button></td>
        </tr>`).join("") || "<tr><td colspan=7 class=muted>Empty</td></tr>"}</tbody></table></div>
      </div>`;
    $("#dvAdd").onclick = async () => {
      try {
        const res = await api("/api/devices", {
          method: "POST",
          body: JSON.stringify({
            device_name: $("#dvName").value,
            model: $("#dvModel").value,
            units_used: +$("#dvU").value,
            network_ports: +$("#dvNet").value,
            power_ports: +$("#dvPwr").value,
            fiber_ports: +$("#dvFib").value,
            description: $("#dvDesc").value,
            datacenter_id: +$("#dvDc").value,
          }),
        });
        toast(res.message); loadDevices();
      } catch (e) { toast(e.message, true); }
    };
    el.querySelectorAll("[data-deldev]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("این دستگاه و اتصالات مرتبط حذف شوند؟")) return;
        try {
          await api(`/api/devices/${b.dataset.deldev}`, { method: "DELETE" });
          toast("حذف شد");
          loadDevices();
        } catch (e) { toast(e.message, true); }
      };
    });
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Placements */
async function loadPlacements() {
  const el = $("#page-placements");
  try {
    let racks = await api("/api/racks");
    let devices = await api("/api/devices");
    let placements = await api("/api/placements");
    let activeRackId = racks.length ? +racks[0].rack_id : null;
    let leftTab = "racks"; // racks | devices

    el.innerHTML = `
      ${hero("PLACEMENT", "Rack Placement", "اول رک را انتخاب کنید، بعد دستگاه‌ها را روی یونیت‌ها بکشید")}
      <div class="place-studio">
        <aside class="place-side panel">
          <div class="place-tabs">
            <button type="button" class="place-tab active" data-tab="racks">رک‌ها</button>
            <button type="button" class="place-tab" data-tab="devices">دستگاه‌ها</button>
          </div>
          <div id="placeSideList" class="place-side-list"></div>
        </aside>
        <section class="place-canvas panel">
          <div id="placeCanvasHead" class="place-canvas-head"></div>
          <div id="placeCanvasBody" class="place-canvas-body"></div>
        </section>
      </div>`;

    const refreshData = async () => {
      racks = await api("/api/racks");
      devices = await api("/api/devices");
      placements = await api("/api/placements");
    };

    const renderSide = () => {
      const box = $("#placeSideList");
      if (!box) return;
      if (leftTab === "racks") {
        box.innerHTML = racks.length
          ? racks
              .map((r) => {
                const count = placements.filter((p) => +p.rack_id === +r.rack_id).length;
                const active = +r.rack_id === +activeRackId ? " active" : "";
                return `<div class="place-rack-item${active}" draggable="true" data-rack="${r.rack_id}">
                  <div class="place-rack-ico">▦</div>
                  <div>
                    <div class="place-item-title">${r.rack_name}</div>
                    <div class="place-item-sub">${r.total_units}U · ${count} device · #${r.rack_id}</div>
                  </div>
                </div>`;
              })
              .join("")
          : `<p class="muted" style="padding:12px">اول از Assets → Racks یک رک بسازید</p>`;

        box.querySelectorAll(".place-rack-item").forEach((item) => {
          item.onclick = () => {
            activeRackId = +item.dataset.rack;
            renderSide();
            renderCanvas();
          };
          item.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("application/x-rack", String(item.dataset.rack));
            e.dataTransfer.setData("text/plain", "rack:" + item.dataset.rack);
            e.dataTransfer.effectAllowed = "copy";
          });
        });
      } else {
        const placedIds = new Set(placements.map((p) => +p.device_id));
        const free = devices.filter((d) => !placedIds.has(+d.device_id));
        const busy = devices.filter((d) => placedIds.has(+d.device_id));
        box.innerHTML = `
          <div class="place-side-label">آزاد (${free.length})</div>
          ${
            free.length
              ? free
                  .map(
                    (d) => `<div class="place-dev-item" draggable="true" data-device="${d.device_id}" data-units="${d.units_used || 1}">
                <div class="place-item-title">${d.device_name}</div>
                <div class="place-item-sub">${d.model || "—"} · ${d.units_used || 1}U</div>
              </div>`
                  )
                  .join("")
              : `<p class="muted" style="padding:8px 4px;font-size:.8rem">دستگاه آزادی نیست</p>`
          }
          <div class="place-side-label" style="margin-top:12px">مستقرشده (${busy.length})</div>
          ${busy
            .slice(0, 40)
            .map((d) => {
              const p = placements.find((x) => +x.device_id === +d.device_id);
              return `<div class="place-dev-item placed">
                <div class="place-item-title">${d.device_name}</div>
                <div class="place-item-sub">rack ${p ? p.rack_id : "?"} · U${p ? p.start_unit + "-" + p.end_unit : ""}</div>
              </div>`;
            })
            .join("")}`;

        box.querySelectorAll(".place-dev-item[draggable]").forEach((item) => {
          item.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("text/plain", String(item.dataset.device));
            e.dataTransfer.setData("application/x-device", String(item.dataset.device));
            e.dataTransfer.setData("application/x-units", String(item.dataset.units || 1));
            e.dataTransfer.effectAllowed = "move";
            item.classList.add("dragging");
          });
          item.addEventListener("dragend", () => item.classList.remove("dragging"));
        });
      }
    };

    const renderCanvas = () => {
      const head = $("#placeCanvasHead");
      const body = $("#placeCanvasBody");
      if (!head || !body) return;
      if (!activeRackId) {
        head.innerHTML = `<div class="muted">یک رک از سمت چپ انتخاب یا درگ کنید</div>`;
        body.innerHTML = `<div class="place-drop-hint" id="placeDropZone">رک را اینجا رها کنید</div>`;
        wireCanvasDrop();
        return;
      }
      const rack = racks.find((r) => +r.rack_id === +activeRackId);
      if (!rack) {
        head.innerHTML = `<div class="muted">رک یافت نشد</div>`;
        body.innerHTML = "";
        return;
      }
      const total = +rack.total_units || 42;
      const placedOnRack = placements.filter((p) => +p.rack_id === +activeRackId);
      const unitMap = {};
      for (const p of placedOnRack) {
        const dev = devices.find((d) => +d.device_id === +p.device_id);
        for (let u = +p.start_unit; u <= +p.end_unit; u++) {
          unitMap[u] = {
            ...p,
            device_name: (dev && dev.device_name) || p.device_id,
            units_used: (dev && dev.units_used) || 1,
          };
        }
      }
      head.innerHTML = `
        <div>
          <div class="place-canvas-title">${rack.rack_name}</div>
          <div class="muted" style="font-size:.78rem">${total}U · ${placedOnRack.length} device · #${rack.rack_id}</div>
        </div>
        <div class="muted" style="font-size:.75rem">دستگاه را از تب Devices روی یونیت خالی رها کنید</div>`;

      let slots = "";
      for (let u = total; u >= 1; u--) {
        const occ = unitMap[u];
        if (occ && +occ.start_unit === u) {
          const span = +occ.end_unit - +occ.start_unit + 1;
          const role = classifyDeviceRole(occ.device_name, "");
          slots += `<div class="place-slot filled role-${role}" data-unit="${u}" data-filled="1" style="min-height:${Math.max(40, span * 26)}px">
            <div class="place-slot-u">U${occ.start_unit}–${occ.end_unit}</div>
            <div class="place-slot-name">${occ.device_name}</div>
            <button type="button" class="btn btn-ghost btn-sm" data-unplace="${occ.placement_id}">برداشتن</button>
          </div>`;
        } else if (occ) {
          continue;
        } else {
          slots += `<div class="place-slot empty u-${u % 2}" data-unit="${u}" data-filled="0">
            <div class="place-slot-u">U${u}</div>
            <div class="place-slot-hint">خالی — رها کنید</div>
          </div>`;
        }
      }
      body.innerHTML = `<div class="place-rack-frame" id="placeDropZone">${slots}</div>`;
      wireSlots();
      wireCanvasDrop();
    };

    const wireCanvasDrop = () => {
      const zone = $("#placeDropZone");
      if (!zone) return;
      zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("drag-over");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        const rackData = e.dataTransfer.getData("application/x-rack");
        if (rackData) {
          activeRackId = +rackData;
          renderSide();
          renderCanvas();
          toast("رک روی بوم قرار گرفت");
        }
      });
    };

    const wireSlots = () => {
      $$(".place-slot").forEach((slot) => {
        slot.addEventListener("dragover", (e) => {
          if (slot.dataset.filled === "1") return;
          e.preventDefault();
          slot.classList.add("drag-over");
        });
        slot.addEventListener("dragleave", () => slot.classList.remove("drag-over"));
        slot.addEventListener("drop", async (e) => {
          e.preventDefault();
          e.stopPropagation();
          slot.classList.remove("drag-over");
          if (slot.dataset.filled === "1") {
            toast("این یونیت اشغال است", true);
            return;
          }
          const rackFrom = e.dataTransfer.getData("application/x-rack");
          if (rackFrom) {
            activeRackId = +rackFrom;
            renderSide();
            renderCanvas();
            return;
          }
          const deviceId = +(e.dataTransfer.getData("application/x-device") || e.dataTransfer.getData("text/plain"));
          const dragUnits = +(e.dataTransfer.getData("application/x-units") || 1);
          if (!deviceId || String(e.dataTransfer.getData("text/plain") || "").startsWith("rack:")) {
            return;
          }
          const dropUnit = +slot.dataset.unit;
          let startUnit = dropUnit;
          if (dragUnits > 1) {
            startUnit = dropUnit - dragUnits + 1;
            if (startUnit < 1) startUnit = 1;
          }
          try {
            const res = await api("/api/placements/move", {
              method: "POST",
              body: JSON.stringify({
                rack_id: activeRackId,
                device_id: deviceId,
                start_unit: startUnit,
              }),
            });
            toast(res.message || "ذخیره شد");
            await refreshData();
            renderSide();
            renderCanvas();
          } catch (err) {
            toast(err.message || "خطا", true);
          }
        });
      });
      $$("[data-unplace]").forEach((btn) => {
        btn.onclick = async () => {
          try {
            await api(`/api/placements/${btn.dataset.unplace}`, { method: "DELETE" });
            toast("برداشته شد");
            await refreshData();
            renderSide();
            renderCanvas();
          } catch (e) {
            toast(e.message, true);
          }
        };
      });
    };

    $$(".place-tab").forEach((t) => {
      t.onclick = () => {
        leftTab = t.dataset.tab;
        $$(".place-tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === leftTab));
        renderSide();
      };
    });

    renderSide();
    renderCanvas();
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

async function loadRackView() {
  const el = $("#page-rackview");
  try {
    const racks = await api("/api/racks");
    el.innerHTML = `
      ${hero("LIVE RACK", "Rack Detail View", "Unit map of selected rack")}
      <div class="panel rv-controls">
        <label class="field"><span>Select rack</span>
          <select id="rvRack">${racks.map((r) => `<option value="${r.rack_id}">${r.rack_name} (${r.rack_id})</option>`).join("")}</select>
        </label>
        <button class="btn btn-primary" id="rvLoad">Show units</button>
      </div>
      <div class="rv-layout" id="rvOut">
        <div class="rv-photo panel">
          <div class="rv-photo-placeholder muted">یک رک انتخاب کنید</div>
        </div>
        <div class="rv-units panel">
          <p class="muted">نقشه یونیت اینجا نمایش داده می‌شود</p>
        </div>
      </div>`;

    const render = async () => {
      try {
        const rid = $("#rvRack").value;
        const [data, img] = await Promise.all([
          api(`/api/racks/${rid}/units`),
          api(`/api/racks/${rid}/image`).catch(() => ({ found: false })),
        ]);
        const hasPhoto = img && img.found && img.url;
        const photoHtml = hasPhoto
          ? `<div class="rv-photo-tools">
               <button type="button" class="btn btn-ghost btn-sm" id="rvZoomIn" title="بزرگ">＋</button>
               <button type="button" class="btn btn-ghost btn-sm" id="rvZoomOut" title="کوچک">－</button>
               <button type="button" class="btn btn-ghost btn-sm" id="rvZoomReset" title="بازنشانی">۱۰۰٪</button>
               <span class="muted" style="font-size:.72rem" id="rvZoomLbl">100%</span>
             </div>
             <div class="rv-photo-viewport" id="rvPhotoVp">
               <img class="rv-rack-img" id="rvRackImg" src="${img.url}?t=${Date.now()}" alt="${data.rack.rack_name}" draggable="false" />
             </div>`
          : `<div class="rv-photo-placeholder">
               <div style="font-size:2rem;margin-bottom:8px">🗄️</div>
               <div>عکس رک پیدا نشد</div>
             </div>`;

        const photoActions = `
          <div class="rv-photo-actions">
            <label class="rv-upload-btn" title="آپلود تصویر">
              <input type="file" id="rvUploadInput" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" hidden />
              <span>⬆ آپلود تصویر</span>
            </label>
            ${hasPhoto ? `<button type="button" class="rv-delete-btn" id="rvDeleteImg" title="حذف تصویر">✕ حذف</button>` : ""}
          </div>`;

        // group multi-U devices into visual slots
        const totalU = +data.rack.total_units || 42;
        const groups = [];
        let i = 0;
        const units = data.units || [];
        while (i < units.length) {
          const u = units[i];
          if (u.status !== "USED") {
            groups.push({ kind: "empty", unit: u.unit, units: 1 });
            i++;
            continue;
          }
          // merge consecutive same range/name
          let j = i + 1;
          while (
            j < units.length &&
            units[j].status === "USED" &&
            units[j].device_name === u.device_name &&
            units[j].range === u.range
          ) {
            j++;
          }
          const span = j - i;
          groups.push({
            kind: "device",
            unitTop: u.unit,
            unitBot: units[j - 1].unit,
            units: span,
            name: u.device_name,
            model: u.model || "",
            range: u.range || "",
          });
          i = j;
        }

        const U_PX = 28;
        const roleOf = (name, model) => classifyDeviceRole(name, model);
        const roleIcon = {
          switch: "▣", router: "◈", server: "▤", storage: "▦",
          firewall: "⬡", patch: "≣", cable: "≈", power: "⚡", kvm: "⌨", other: "▪",
        };

        // collapse long empty runs for readability (show summary strip)
        const displayGroups = [];
        let ei = 0;
        while (ei < groups.length) {
          if (groups[ei].kind !== "empty") {
            displayGroups.push(groups[ei]);
            ei++;
            continue;
          }
          let ej = ei;
          while (ej < groups.length && groups[ej].kind === "empty") ej++;
          const run = ej - ei;
          const topU = groups[ei].unit;
          const botU = groups[ej - 1].unit;
          displayGroups.push({
            kind: "empty",
            unit: topU,
            unitBot: botU,
            units: run,
            collapsed: run >= 3,
          });
          ei = ej;
        }

        const baysHtml = displayGroups.map((g) => {
          if (g.kind === "empty") {
            if (g.collapsed) {
              const h = Math.min(56, 18 + g.units * 4);
              return `<div class="rv2-empty rv2-empty-run" style="height:${h}px">
                <span class="rv2-empty-label">EMPTY · U${g.unitBot}–${g.unit} · ${g.units}U</span>
              </div>`;
            }
            return `<div class="rv2-empty" style="height:${U_PX}px"><span>EMPTY · U${g.unit}</span></div>`;
          }
          const role = roleOf(g.name, g.model);
          const h = Math.max(U_PX, g.units * U_PX - 4);
          const label = g.name;
          return `<div class="rv2-dev role-${role}" style="height:${h}px" title="${g.name} · ${g.model} · U${g.range}">
            <span class="rv2-icon">${roleIcon[role] || "▪"}</span>
            <div class="rv2-meta">
              <div class="rv2-name">${label}</div>
              <div class="rv2-sub">${g.model ? g.model + " · " : ""}U${g.range} · ${role}</div>
            </div>
            <span class="rv2-u">U${g.range || g.unitTop}</span>
          </div>`;
        }).join("");

        const usedCount = groups.filter((g) => g.kind === "device").length;
        const usedU = groups.filter((g) => g.kind === "device").reduce((s, g) => s + g.units, 0);
        const fillPct = Math.round((usedU / totalU) * 100);

        $("#rvOut").innerHTML = `
          <div class="rv-visual panel">
            <div class="rv-visual-head">
              <div>
                <h3 style="margin:0">${data.rack.rack_name}</h3>
                <div class="muted" style="font-size:.8rem">${totalU}U · ${usedCount} device · ${fillPct}% filled</div>
              </div>
              <div class="rv-fill">
                <div class="rv-fill-bar"><i style="width:${fillPct}%"></i></div>
              </div>
            </div>
            <div class="rv2-scene">
              <div class="rv2-rack">
                <div class="rv2-rail"></div>
                <div class="rv2-head">
                  <span>${data.rack.rack_name}</span>
                  <small>${totalU}U · ${usedCount} device · ${fillPct}% full</small>
                </div>
                <div class="rv2-bays">${baysHtml}</div>
                <div class="rv2-rail bot"></div>
              </div>
            </div>
          </div>
          <div class="rv-photo panel">
            <div class="rv-photo-title">${data.rack.rack_name}</div>
            ${photoHtml}
            ${photoActions}
          </div>`;

        // photo zoom + pan
        const imgEl = $("#rvRackImg");
        const vp = $("#rvPhotoVp");
        if (imgEl && vp) {
          let z = 1, px = 0, py = 0, drag = false, lx = 0, ly = 0;
          const apply = () => {
            imgEl.style.transform = `translate(${px}px,${py}px) scale(${z})`;
            const lbl = $("#rvZoomLbl");
            if (lbl) lbl.textContent = Math.round(z * 100) + "%";
          };
          $("#rvZoomIn")?.addEventListener("click", () => { z = Math.min(4, z + 0.2); apply(); });
          $("#rvZoomOut")?.addEventListener("click", () => { z = Math.max(0.4, z - 0.2); apply(); });
          $("#rvZoomReset")?.addEventListener("click", () => { z = 1; px = 0; py = 0; apply(); });
          vp.addEventListener("wheel", (e) => {
            e.preventDefault();
            z = Math.min(4, Math.max(0.4, z * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
            apply();
          }, { passive: false });
          vp.addEventListener("mousedown", (e) => {
            if (e.button !== 0) return;
            drag = true; lx = e.clientX; ly = e.clientY;
            vp.classList.add("panning");
          });
          window.addEventListener("mousemove", (e) => {
            if (!drag) return;
            px += e.clientX - lx; py += e.clientY - ly;
            lx = e.clientX; ly = e.clientY;
            apply();
          });
          window.addEventListener("mouseup", () => {
            drag = false;
            vp.classList.remove("panning");
          });
        }

        // upload / delete image
        const uploadInput = $("#rvUploadInput");
        if (uploadInput) {
          uploadInput.onchange = async () => {
            const f = uploadInput.files && uploadInput.files[0];
            if (!f) return;
            try {
              const fd = new FormData();
              fd.append("file", f);
              const res = await fetch(`${API}/api/racks/${rid}/image`, {
                method: "POST",
                headers: token ? { Authorization: `Bearer ${token}` } : {},
                body: fd,
              });
              const data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(data.detail || data.message || res.statusText);
              toast("تصویر با موفقیت آپلود شد");
              render();
            } catch (e) {
              toast(e.message || "آپلود ناموفق", true);
            } finally {
              uploadInput.value = "";
            }
          };
        }
        const delBtn = $("#rvDeleteImg");
        if (delBtn) {
          delBtn.onclick = async () => {
            if (!confirm("تصویر این رک حذف شود؟")) return;
            try {
              await api(`/api/racks/${rid}/image`, { method: "DELETE" });
              toast("تصویر حذف شد");
              render();
            } catch (e) {
              toast(e.message, true);
            }
          };
        }
      } catch (e) { toast(e.message, true); }
    };

    $("#rvLoad").onclick = render;
    $("#rvRack").onchange = render;
    if (racks.length) render();
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Monitoring devices */
async function loadMonDevices() {
  const el = $("#page-monDevices");
  try {
    const list = await api("/api/monitoring/devices");
    el.innerHTML = `
      ${hero("DEVICE HEALTH", "Monitoring Devices", "Snapshot, health and telemetry")}
      <div class="panel">
        <label class="field"><span>Device</span>
          <select id="mdSel">${list.map((d) => `<option value="${d.monitoring_device_id}">${d.display_name} · ${d.host || "no IP"}</option>`).join("")}</select>
        </label>
        <button class="btn btn-primary" id="mdLoad">Load detail</button>
      </div>
      <div id="mdOut"></div>`;
    if (!list.length) {
      $("#mdOut").innerHTML = `<div class="panel"><p class="muted">No monitoring devices. Add from Discovery or Add Device.</p></div>`;
      return;
    }
    const show = async () => {
      const id = $("#mdSel").value;
      const d = await api(`/api/monitoring/devices/${encodeURIComponent(id)}`);
      const s = d.snapshot;
      $("#mdOut").innerHTML = `
        <div class="panel">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <div style="font-size:1.3rem;font-weight:900">${s.device_name}</div>
              <div class="muted">${s.vendor} · ${s.model} · ${s.host || "IP not set"}</div>
            </div>
            <span class="badge ${s.status === "UP" ? "badge-up" : "badge-down"}">${s.status}</span>
          </div>
          <div class="stats" style="margin-top:14px;grid-template-columns:repeat(3,1fr)">
            ${[["CPU", s.cpu_percent, "%"], ["Memory", s.memory_percent, "%"], ["Temp", s.temperature_c, "°C"]].map(([l, v, u]) => `
              <div class="stat"><div class="lbl">${l}</div><div class="val">${v}${u}</div>
              <div class="metric-bar"><div style="width:${Math.min(100, v)}%"></div></div></div>`).join("")}
          </div>
        </div>
        <div class="panel"><h3><span class="dot"></span> Interfaces</h3>
          <div class="table-wrap"><table><thead><tr><th>IF</th><th>Status</th><th>RX</th><th>TX</th><th>Util</th></tr></thead>
          <tbody>${(d.interfaces || []).slice(0, 12).map((i) => `<tr>
            <td class="mono">${i.interface}</td>
            <td><span class="badge ${i.status === "UP" ? "badge-up" : "badge-down"}">${i.status}</span></td>
            <td>${i.rx_mbps}</td><td>${i.tx_mbps}</td><td>${i.utilization_percent}%</td>
          </tr>`).join("")}</tbody></table></div>
        </div>
        <div class="panel"><h3><span class="dot"></span> CPU trend</h3><div id="mdChart" style="height:280px"></div></div>`;
      if (window.Plotly && d.cpu_history) {
        Plotly.newPlot(
          "mdChart",
          [{
            x: d.cpu_history.map((x) => x.timestamp),
            y: d.cpu_history.map((x) => x.value),
            type: "scatter",
            mode: "lines",
            fill: "tozeroy",
            line: { color: "#c084fc" },
          }],
          {
            margin: { t: 20, r: 10, b: 40, l: 40 },
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            font: { color: "#c7d2e5" },
            yaxis: { title: "CPU %" },
          },
          { displayModeBar: false, responsive: true }
        );
      }
    };
    $("#mdLoad").onclick = () => show().catch((e) => toast(e.message, true));
    await show();
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Performance */
async function loadMonPerf() {
  const el = $("#page-monPerf");
  try {
    const list = await api("/api/monitoring/devices");
    el.innerHTML = `
      ${hero("HISTORICAL METRICS", "Performance", "CPU, memory, temperature trends")}
      <div class="panel grid-3">
        <label class="field"><span>Device</span>
          <select id="pfDev">${list.map((d) => `<option value="${d.monitoring_device_id}">${d.display_name}</option>`).join("")}</select>
        </label>
        <label class="field"><span>Metric</span>
          <select id="pfMet"><option>CPU</option><option>Memory</option><option>Temperature</option></select>
        </label>
        <label class="field"><span>Hours</span>
          <select id="pfHrs"><option value="6">6</option><option value="12">12</option><option value="24" selected>24</option><option value="168">168</option></select>
        </label>
      </div>
      <button class="btn btn-primary" id="pfGo">Render</button>
      <div class="panel" style="margin-top:14px"><div id="pfChart" style="height:400px"></div></div>`;
    const run = async () => {
      const hist = await api(
        `/api/monitoring/performance?device_id=${encodeURIComponent($("#pfDev").value)}&metric=${$("#pfMet").value}&hours=${$("#pfHrs").value}`
      );
      if (!hist.length || !window.Plotly) return;
      Plotly.newPlot(
        "pfChart",
        [{ x: hist.map((h) => h.timestamp), y: hist.map((h) => h.value), type: "scatter", mode: "lines", line: { color: "#22c55e", width: 2.5 } }],
        {
          margin: { t: 20, r: 15, b: 40, l: 45 },
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
          font: { color: "#c7d2e5" },
        },
        { displayModeBar: false, responsive: true }
      );
    };
    $("#pfGo").onclick = () => run().catch((e) => toast(e.message, true));
    if (list.length) await run();
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Alerts */
async function loadMonAlerts() {
  const el = $("#page-monAlerts");
  try {
    const alerts = await api("/api/monitoring/alerts");
    el.innerHTML = `
      ${hero("ALARM CONSOLE", "Alerts", "Threshold violations and acknowledgements")}
      <div class="panel">
        <div class="table-wrap"><table><thead><tr>
          <th>Severity</th><th>Status</th><th>Device</th><th>Metric</th><th>Value</th><th>Message</th><th></th>
        </tr></thead><tbody>
        ${alerts.map((a) => `<tr>
          <td><span class="badge ${a.severity === "CRITICAL" ? "badge-down" : "badge-warn"}">${a.severity}</span></td>
          <td>${a.status}</td><td>${a.device_name}</td><td>${a.metric_name}</td>
          <td>${a.value}</td><td>${a.message}</td>
          <td>${a.status === "OPEN" ? `<button class="btn btn-primary btn-sm" data-ack="${a.alert_id}">Ack</button>` : ""}</td>
        </tr>`).join("") || "<tr><td colspan=7 class=muted>No alerts</td></tr>"}
        </tbody></table></div>
      </div>`;
    el.onclick = async (ev) => {
      const b = ev.target.closest("[data-ack]");
      if (!b) return;
      try {
        await api("/api/monitoring/alerts/ack", { method: "POST", body: JSON.stringify({ id: b.dataset.ack }) });
        toast("Acknowledged"); loadMonAlerts();
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Traps */
async function loadMonTraps() {
  const el = $("#page-monTraps");
  try {
    const [traps, devices] = await Promise.all([
      api("/api/monitoring/traps"),
      api("/api/monitoring/devices"),
    ]);
    el.innerHTML = `
      ${hero("EVENT RECEIVER", "SNMP Traps", "Asynchronous events")}
      <div class="panel">
        <div class="grid-2">
          <label class="field"><span>Test trap device</span>
            <select id="tpDev">${devices.map((d) => `<option value="${d.monitoring_device_id}">${d.display_name}</option>`).join("")}</select>
          </label>
          <label class="field"><span>Message</span><input id="tpMsg" value="linkDown on Gi1/0/24" /></label>
        </div>
        <button class="btn btn-primary" id="tpInject">Inject test trap</button>
      </div>
      <div class="panel">
        <div class="table-wrap"><table><thead><tr>
          <th>Time</th><th>Severity</th><th>Status</th><th>Device</th><th>OID</th><th>Message</th><th></th>
        </tr></thead><tbody>
        ${traps.map((t) => `<tr>
          <td>${t.received_at || ""}</td>
          <td><span class="badge badge-warn">${t.severity}</span></td>
          <td>${t.status}</td><td>${t.device_name}</td>
          <td class="mono">${t.trap_oid || ""}</td><td>${t.message}</td>
          <td>${t.status === "NEW" ? `<button class="btn btn-primary btn-sm" data-acktrap="${t.trap_id}">Ack</button>` : ""}</td>
        </tr>`).join("") || "<tr><td colspan=7 class=muted>No traps</td></tr>"}
        </tbody></table></div>
      </div>`;
    $("#tpInject").onclick = async () => {
      try {
        await api(`/api/monitoring/traps/test?device_id=${encodeURIComponent($("#tpDev").value)}&message=${encodeURIComponent($("#tpMsg").value)}`, { method: "POST" });
        toast("Trap recorded"); loadMonTraps();
      } catch (e) { toast(e.message, true); }
    };
    el.onclick = async (ev) => {
      const b = ev.target.closest("[data-acktrap]");
      if (!b) return;
      try {
        await api("/api/monitoring/traps/ack", { method: "POST", body: JSON.stringify({ id: b.dataset.acktrap }) });
        loadMonTraps();
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Discovery */
async function loadDiscovery() {
  const el = $("#page-discovery");
  el.innerHTML = `
    ${hero("NETWORK DISCOVERY", "Discovery", "Preview or bounded ICMP scan")}
    <div class="panel">
      <div class="grid-2">
        <label class="field"><span>CIDR</span><input id="diCidr" value="192.168.1.0/24" /></label>
        <label class="field"><span>Mode</span>
          <select id="diMode"><option value="preview">Preview (no traffic)</option><option value="icmp">Live ICMP</option></select>
        </label>
      </div>
      <button class="btn btn-primary" id="diGo">START DISCOVERY</button>
    </div>
    <div class="panel" id="diOut"><p class="muted">نتیجه اینجا نمایش داده می‌شود</p></div>`;
  $("#diGo").onclick = async () => {
    try {
      const mode = $("#diMode").value;
      const path = mode === "icmp"
        ? `/api/discovery/icmp?cidr=${encodeURIComponent($("#diCidr").value)}`
        : `/api/discovery/preview?cidr=${encodeURIComponent($("#diCidr").value)}`;
      const rows = await api(path, { method: "POST" });
      $("#diOut").innerHTML = `
        <div class="table-wrap"><table><thead><tr>
          <th>IP</th><th>Hostname</th><th>Type</th><th>Vendor</th><th>ICMP</th><th>Source</th>
        </tr></thead><tbody>
        ${rows.map((r) => `<tr>
          <td class="mono">${r.ip_address}</td><td>${r.hostname || ""}</td>
          <td>${r.device_type || ""}</td><td>${r.vendor || ""}</td>
          <td>${r.icmp || ""}</td><td class="muted">${r.source || ""}</td>
        </tr>`).join("")}
        </tbody></table></div>`;
    } catch (e) { toast(e.message, true); }
  };
}

/* Add monitoring device */
async function loadAddMon() {
  const el = $("#page-addMon");
  try {
    const [tpl, cred] = await Promise.all([
      api("/api/monitoring/templates"),
      api("/api/monitoring/credentials"),
    ]);
    el.innerHTML = `
      ${hero("DEVICE ONBOARDING", "Add Device", "Register management endpoint")}
      <div class="panel">
        <div class="grid-2">
          <label class="field"><span>Name</span><input id="amName" placeholder="SW-CORE-01" /></label>
          <label class="field"><span>Host / IP</span><input id="amHost" placeholder="192.168.10.20" /></label>
          <label class="field"><span>Vendor</span><input id="amVendor" value="Cisco" /></label>
          <label class="field"><span>Model</span><input id="amModel" /></label>
          <label class="field"><span>Template</span>
            <select id="amTpl">${tpl.map((t) => `<option value="${t.template_id}">${t.name}</option>`).join("")}</select>
          </label>
          <label class="field"><span>Credential</span>
            <select id="amCred"><option value="">Not assigned</option>${cred.map((c) => `<option value="${c.credential_id}">${c.name}</option>`).join("")}</select>
          </label>
          <label class="field"><span>Poll mode</span>
            <select id="amMode"><option value="demo">demo</option><option value="snmp">snmp</option></select>
          </label>
        </div>
        <button class="btn btn-primary" id="amAdd">Add device</button>
      </div>`;
    $("#amAdd").onclick = async () => {
      try {
        const res = await api("/api/monitoring/devices", {
          method: "POST",
          body: JSON.stringify({
            display_name: $("#amName").value,
            host: $("#amHost").value,
            vendor: $("#amVendor").value,
            model: $("#amModel").value,
            template_id: $("#amTpl").value,
            credential_id: $("#amCred").value,
            poll_mode: $("#amMode").value,
          }),
        });
        toast("Added: " + res.id);
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Storage / Database */
async function loadStorage() {
  const el = $("#page-storage");
  try {
    const s = await api("/api/storage/settings");
    const isPg = s.mode === "postgres" && s.connected;
    const statusClass = isPg ? "st-status-on" : "st-status-csv";
    const statusText = isPg ? "PostgreSQL متصل" : "حالت فایل CSV";
    el.innerHTML = `
      ${hero("STORAGE", "منبع داده", "CSV محلی یا PostgreSQL — ساختار جداول یکسان است")}

      <div class="st-shell">
        <div class="st-header">
          <div class="st-header-text">
            <div class="st-eyebrow">DATA BACKEND</div>
            <h3>انتخاب محل ذخیره</h3>
            <p>می‌توانید بین فایل‌های محلی یا دیتابیس PostgreSQL جابه‌جا شوید؛ ساختار داده یکسان می‌ماند.</p>
          </div>
          <div class="st-status-pill ${statusClass}">
            <i></i><span>${statusText}</span>
          </div>
        </div>

        <div class="storage-mode">
          <label class="storage-choice ${!isPg ? "on" : ""}">
            <input type="radio" name="stMode" value="csv" ${!isPg ? "checked" : ""} />
            <span class="st-card-icon st-icon-csv">📁</span>
            <span class="storage-choice-body">
              <b>Excel / CSV</b>
              <small>فایل‌های محلی در <code>data/*.csv</code></small>
              <span class="st-card-tag">ساده · بدون سرور</span>
            </span>
            <span class="st-check" aria-hidden="true"></span>
          </label>
          <label class="storage-choice ${isPg ? "on" : ""}">
            <input type="radio" name="stMode" value="postgres" ${isPg ? "checked" : ""} />
            <span class="st-card-icon st-icon-pg">🐘</span>
            <span class="storage-choice-body">
              <b>PostgreSQL</b>
              <small>دیتابیس <code>datacenter-monitoring</code></small>
              <span class="st-card-tag">مقیاس‌پذیر · چندکاربره</span>
            </span>
            <span class="st-check" aria-hidden="true"></span>
          </label>
        </div>

        <div id="stPgBox" class="storage-pg ${isPg || s.mode === "postgres" ? "" : "hidden"}">
          <div class="st-pg-head">
            <span class="st-pg-title">تنظیمات اتصال</span>
            <span class="st-pg-sub">Host · Port · Credentials</span>
          </div>
          <div class="st-fields">
            <label class="field st-field"><span>Host</span><input id="stHost" value="${s.host || "localhost"}" autocomplete="off" /></label>
            <label class="field st-field"><span>Port</span><input id="stPort" type="number" value="${s.port || 5432}" /></label>
            <label class="field st-field"><span>User</span><input id="stUser" value="${s.user || "postgres"}" autocomplete="off" /></label>
            <label class="field st-field"><span>Password</span><input id="stPass" type="password" placeholder="${s.has_password ? "•••• (خالی = همان قبلی)" : "رمز دیتابیس"}" /></label>
            <label class="field st-field st-field-wide"><span>Database name</span><input id="stDb" value="${s.database || "datacenter-monitoring"}" /></label>
          </div>
          <label class="st-migrate">
            <input type="checkbox" id="stMigrate" checked />
            <span class="st-migrate-ui"></span>
            <span class="st-migrate-text">اگر جداول خالی بودند، دادهٔ فعلی CSV را منتقل کن</span>
          </label>
        </div>

        <div class="st-footer">
          <div class="storage-status">
            ${s.last_ok ? `<div class="st-msg st-msg-ok">✓ ${s.last_ok}</div>` : ""}
            ${s.last_error ? `<div class="st-msg st-msg-err">✕ ${s.last_error}</div>` : ""}
            ${!s.last_ok && !s.last_error ? `<div class="st-msg st-msg-muted">آمادهٔ پیکربندی</div>` : ""}
          </div>
          <div class="st-actions">
            <button class="btn btn-ghost st-btn-test" id="stTest">تست اتصال</button>
            <button class="btn btn-primary st-btn-save" id="stSave">ذخیره و اعمال</button>
          </div>
        </div>

        <div class="st-note">
          <span class="st-note-ico">ⓘ</span>
          <p>با اتصال موفق، دیتابیس (در صورت نبود) ساخته می‌شود و جداول
          <code>datacenters</code>، <code>racks</code>، <code>devices</code>، <code>placements</code>،
          <code>connections</code>، <code>dci_links</code>
          ایجاد می‌گردند. پس از آن همهٔ افزودن/حذف‌ها روی همان منبع انجام می‌شود.</p>
        </div>
      </div>`;

    const syncModeUi = () => {
      const mode = (el.querySelector('input[name="stMode"]:checked') || {}).value || "csv";
      el.querySelectorAll(".storage-choice").forEach((lab) => {
        lab.classList.toggle("on", lab.querySelector("input")?.value === mode);
      });
      $("#stPgBox")?.classList.toggle("hidden", mode !== "postgres");
    };
    el.querySelectorAll('input[name="stMode"]').forEach((r) => {
      r.onchange = syncModeUi;
    });
    syncModeUi();

    const payload = () => ({
      mode: (el.querySelector('input[name="stMode"]:checked') || {}).value || "csv",
      host: $("#stHost")?.value || "localhost",
      port: +($("#stPort")?.value || 5432),
      user: $("#stUser")?.value || "postgres",
      password: $("#stPass")?.value || "",
      database: $("#stDb")?.value || "datacenter-monitoring",
      migrate: !!$("#stMigrate")?.checked,
    });

    $("#stSave").onclick = async () => {
      try {
        const body = payload();
        if (body.mode === "csv") {
          await api("/api/storage/settings", { method: "POST", body: JSON.stringify(body) });
          toast("حالت CSV فعال شد");
        } else {
          await api("/api/storage/settings", { method: "POST", body: JSON.stringify(body) });
          toast("به PostgreSQL متصل شد");
        }
        loadStorage();
      } catch (e) { toast(e.message, true); }
    };
    $("#stTest").onclick = async () => {
      try {
        const body = payload();
        body.mode = "postgres";
        const res = await api("/api/storage/test", { method: "POST", body: JSON.stringify(body) });
        toast(res.last_ok || "اتصال موفق");
        loadStorage();
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

/* Users */
async function loadUsers() {
  const el = $("#page-users");
  try {
    const users = await api("/api/admin/users");
    const meName = (me && (me.user || me.username)) || "";
    const setupMode = !!(me && (me.setup_mode || me.needs_setup));
    const setupBanner = setupMode
      ? `<div class="panel setup-users-banner">
          <b>🛡️ راه‌اندازی اولیه:</b> اولین کاربر به‌صورت خودکار با نقش <b>admin</b> ساخته می‌شود.
          بعد از ساخت، یک‌بار خارج شوید و با همان یوزر وارد شوید.
        </div>`
      : "";
    el.innerHTML = `
      ${hero("ADMINISTRATION", "Users", "Accounts and roles")}
      ${setupBanner}
      <div class="panel">
        <div class="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>DCs</th><th>Active</th><th></th></tr></thead>
        <tbody>${users.map((u) => `<tr>
          <td>${u.username}</td><td>${u.role}</td><td>${u.allowed_dcs}</td><td>${u.is_active}</td>
          <td>${u.username === meName || u.username === "setup" ? "" : `<button class="btn btn-danger btn-sm" data-del-user="${u.username}">حذف</button>`}</td>
        </tr>`).join("") || "<tr><td colspan=5 class=muted>هنوز کاربری ثبت نشده</td></tr>"}</tbody></table></div>
      </div>
      <div class="panel">
        <div class="grid-2">
          <label class="field"><span>Username</span><input id="usName" placeholder="مثلاً admin" /></label>
          <label class="field"><span>Password</span><input id="usPass" type="password" placeholder="حداقل ۴ کاراکتر" /></label>
          <label class="field"><span>Role</span>
            <select id="usRole" ${setupMode ? "disabled" : ""}>
              <option value="admin" ${setupMode ? "selected" : ""}>admin</option>
              <option value="editor">editor</option>
              <option value="read_only" ${!setupMode ? "selected" : ""}>read_only</option>
            </select>
          </label>
          <label class="field"><span>Allowed DCs</span><input id="usDcs" value="all" /></label>
        </div>
        <button class="btn btn-primary" id="usAdd">${setupMode ? "ساخت ادمین" : "Create user"}</button>
      </div>`;
    $("#usAdd").onclick = async () => {
      try {
        const q = new URLSearchParams({
          username: $("#usName").value,
          password: $("#usPass").value,
          role: setupMode ? "admin" : $("#usRole").value,
          allowed_dcs: $("#usDcs").value,
        });
        const res = await api(`/api/admin/users?${q}`, { method: "POST" });
        toast(setupMode ? "ادمین ساخته شد — حالا خارج شوید و با آن وارد شوید" : "User created");
        if (setupMode || res.setup_complete) {
          me.needs_setup = false;
          me.setup_mode = false;
          showSetupModal(false);
        }
        loadUsers();
      } catch (e) { toast(e.message, true); }
    };
    el.querySelectorAll("[data-del-user]").forEach((btn) => {
      btn.onclick = async () => {
        const name = btn.dataset.delUser;
        if (!confirm(`حذف کاربر «${name}»؟`)) return;
        try {
          await api(`/api/admin/users/${encodeURIComponent(name)}`, { method: "DELETE" });
          toast("User deleted");
          loadUsers();
        } catch (e) { toast(e.message, true); }
      };
    });
  } catch (e) {
    el.innerHTML = `<div class="panel"><p class="muted">${e.message}</p></div>`;
  }
}


/* Bale Bot settings */
async function loadBale() {
  const el = $("#page-bale");
  try {
    const s = await api("/api/bale/settings");
    const st = s.status || {};
    const running = !!st.running;
    const statusBadge = running
      ? `<span style="color:#22c55e;font-weight:700">● فعال</span> · @${s.status?.bot_username || "—"}`
      : `<span style="color:#94a3b8;font-weight:700">○ متوقف</span>`;
    const errLine = st.last_error
      ? `<p class="muted" style="color:#f87171;margin-top:6px">آخرین خطا: ${st.last_error}</p>`
      : "";

    el.innerHTML = `
      ${hero("INTEGRATIONS", "ربات بله", "تنظیمات اتصال، polling و مینی‌اپ")}
      <div class="panel" style="border:1px solid rgba(34,211,238,.25)">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
          <div>
            <div class="eyebrow" style="color:#e9d5ff">BALE MESSENGER</div>
            <h3 style="margin:4px 0 0">وضعیت ربات</h3>
            <div style="margin-top:6px">${statusBadge}</div>
            ${errLine}
          </div>
          <div style="font-size:.8rem;color:#94a3b8">
            توکن: <code>${s.bot_token_masked || "—"}</code>
          </div>
        </div>

        <ol style="margin:0 0 16px;padding-right:18px;color:#94a3b8;font-size:.85rem;line-height:1.8">
          <li>در بله به <b>@bot_parent</b> یا BotFather پیام دهید و ربات بسازید</li>
          <li>توکن را اینجا ذخیره کنید و یک‌بار به ربات پیام دهید</li>
          <li>chat_id خود را وارد کنید (از getUpdates یا فوروارد به ربات‌های id)</li>
        </ol>

        <div class="grid-2">
          <label class="field"><span>Bot Token</span>
            <input id="baleToken" type="password" placeholder="${s.has_token ? "•••• (برای تغییر، توکن جدید وارد کنید)" : "123456:ABC..."}" />
          </label>
          <label class="field"><span>Default Chat ID</span>
            <input id="baleChatId" value="${s.default_chat_id || ""}" placeholder="مثلاً 31397962" />
          </label>
          <label class="field" style="grid-column:1/-1"><span>آدرس مینی‌اپ (HTTPS عمومی پنل)</span>
            <input id="baleMiniUrl" value="${s.mini_app_url || ""}" placeholder="https://your-domain.com/mini" />
          </label>
        </div>

        <div style="display:flex;flex-direction:column;gap:10px;margin:16px 0">
          <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
            <input type="checkbox" id="baleEnabled" ${s.enabled ? "checked" : ""} />
            <span>فعال‌سازی اتصال بله</span>
          </label>
          <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
            <input type="checkbox" id="baleAutoAlerts" ${s.auto_alerts ? "checked" : ""} />
            <span>ارسال خودکار آلارم‌ها به بله</span>
          </label>
          <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
            <input type="checkbox" id="baleNotifyStop" ${s.notify_service_stop ? "checked" : ""} />
            <span>اعلان توقف سرویس</span>
          </label>
          <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
            <input type="checkbox" id="balePolling" ${s.polling ? "checked" : ""} />
            <span>Polling فعال (بدون نیاز به دامنه عمومی)</span>
          </label>
        </div>

        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">
          <button class="btn btn-primary" id="baleSave">💾 ذخیره تنظیمات</button>
          <button class="btn" id="baleCheck">✓ بررسی توکن</button>
          <button class="btn" id="baleTest">✉️ ارسال پیام تست</button>
          <button class="btn" id="baleAlerts">🚨 ارسال آلارم‌ها الان</button>
          <button class="btn" id="baleStart" style="background:#16a34a;color:#fff">▶ شروع گوش‌دادن</button>
          <button class="btn" id="baleStop" style="background:#b91c1c;color:#fff">■ توقف گوش‌دادن</button>
        </div>
      </div>

      <div class="panel">
        <h3 style="margin-top:0">دستورات ربات</h3>
        <p class="muted" style="margin-bottom:10px">بعد از «شروع گوش‌دادن»، در چت ربات <code>/start</code> بزنید. منوی دکمه‌ای باز می‌شود.</p>
        <div class="table-wrap"><table>
          <thead><tr><th>دستور / منو</th><th>توضیح</th></tr></thead>
          <tbody>
            <tr><td><code>/start</code></td><td>ورود با نام کاربری و رمز پنل</td></tr>
            <tr><td>📊 داشبورد بصری</td><td>خلاصه + نمودار رنگی</td></tr>
            <tr><td>🗄️ رک‌ها + نمودار</td><td>لیست رک و utilization</td></tr>
            <tr><td>📡 مانیتورینگ</td><td>دستگاه‌ها، پرفورمنس، آلرت، Trap</td></tr>
            <tr><td>🔍 دیسکاوری</td><td>پیش‌نمایش / ICMP روی CIDR</td></tr>
            <tr><td>مینی‌اپ</td><td>دکمه منوی ربات → باز شدن پنل وب داخل بله</td></tr>
          </tbody>
        </table></div>
      </div>
    `;

    const payloadFromForm = () => {
      const body = {
        default_chat_id: $("#baleChatId").value.trim(),
        enabled: $("#baleEnabled").checked,
        auto_alerts: $("#baleAutoAlerts").checked,
        notify_service_stop: $("#baleNotifyStop").checked,
        polling: $("#balePolling").checked,
        mini_app_url: $("#baleMiniUrl").value.trim(),
      };
      const tok = $("#baleToken").value.trim();
      if (tok) body.bot_token = tok;
      return body;
    };

    $("#baleSave").onclick = async () => {
      try {
        await api("/api/bale/settings", { method: "POST", body: JSON.stringify(payloadFromForm()) });
        toast("تنظیمات ذخیره شد");
        loadBale();
      } catch (e) { toast(e.message, true); }
    };
    $("#baleCheck").onclick = async () => {
      try {
        // save token first if typed
        const tok = $("#baleToken").value.trim();
        if (tok) {
          await api("/api/bale/settings", { method: "POST", body: JSON.stringify({ bot_token: tok }) });
        }
        const r = await api("/api/bale/check-token", { method: "POST" });
        if (r.ok) toast(`توکن معتبر: @${r.username} (id=${r.id})`);
        else toast(r.description || "نامعتبر", true);
        loadBale();
      } catch (e) { toast(e.message, true); }
    };
    $("#baleTest").onclick = async () => {
      try {
        await api("/api/bale/settings", { method: "POST", body: JSON.stringify(payloadFromForm()) });
        await api("/api/bale/test-message", { method: "POST", body: JSON.stringify({ text: "🧪 پیام تست از DC Monitor Pro" }) });
        toast("پیام تست ارسال شد");
      } catch (e) { toast(e.message, true); }
    };
    $("#baleAlerts").onclick = async () => {
      try {
        await api("/api/bale/send-alerts-now", { method: "POST" });
        toast("آلارم‌ها ارسال شد");
      } catch (e) { toast(e.message, true); }
    };
    $("#baleStart").onclick = async () => {
      try {
        await api("/api/bale/settings", { method: "POST", body: JSON.stringify(payloadFromForm()) });
        const r = await api("/api/bale/start", { method: "POST" });
        toast(r.message || "شروع شد");
        loadBale();
      } catch (e) { toast(e.message, true); }
    };
    $("#baleStop").onclick = async () => {
      try {
        const r = await api("/api/bale/stop", { method: "POST" });
        toast(r.message || "متوقف شد");
        loadBale();
      } catch (e) { toast(e.message, true); }
    };
  } catch (e) {
    el.innerHTML = `<div class="panel"><p class="muted">${e.message}</p>
      <p class="muted">این بخش فقط برای ادمین است.</p></div>`;
  }
}


/* ========== Topology ========== */
let topoNetwork = null;




let topoEditMode = false;
let topoEditState = null; // {step, src, dst, ...}

async function loadTopology() {
  const el = $("#page-topology");
  try {
    const [elev, dcList] = await Promise.all([
      api("/api/topology?mode=elevation"),
      api("/api/topology/datacenters"),
    ]);
    const dcs = (dcList && dcList.datacenters) || [];
    const dcTabs = [
      `<button class="btn btn-primary btn-sm dc-tab active" data-dc="">همه دیتاسنترها</button>`,
      ...dcs.map((d) =>
        `<button class="btn btn-ghost btn-sm dc-tab" data-dc="${d.datacenter_id}">${d.datacenter_name}${d.rack_count != null ? " · " + d.rack_count + " rack" : ""}</button>`
      ),
    ].join("");

    const meta = elev.meta || {};
    const typeColors = elev.type_colors || {
      fiber: "#4ade80", copper: "#fb923c", network: "#a78bfa", power: "#facc15", other: "#94a3b8",
    };
    el.innerHTML = `
      ${hero("RACK ELEVATION", "Connection Topology", "نقشه ارتفاع رک · اتصالات زنده · فیلتر و جستجو")}
      <div class="topo-chrome">
        <div class="topo-chrome-row">
          <div id="dcTabs" class="topo-dc-tabs">${dcTabs}</div>
          <div class="topo-chrome-actions">
            <button type="button" class="btn btn-primary btn-sm" id="topoEditBtn">✎ حالت ویرایش ارتباط</button>
          </div>
        </div>
        <div class="topo-stats">
          <span class="topo-stat"><b>${meta.rack_count ?? (elev.racks||[]).length}</b> رک</span>
          <span class="topo-stat"><b>${meta.device_count ?? (elev.devices||[]).length}</b> دستگاه</span>
          <span class="topo-stat"><b>${meta.link_count ?? (elev.links||[]).length}</b> لینک</span>
          ${meta.connection_rows != null ? `<span class="topo-stat muted">csv: ${meta.connection_rows}</span>` : ""}
        </div>
        <div class="topo-filters">
          <label class="topo-search">
            <span class="topo-search-ico">⌕</span>
            <input type="search" id="topoSearch" placeholder="جستجوی دستگاه…" autocomplete="off" />
          </label>
          <div class="topo-type-chips" id="topoTypeChips">
            <button type="button" class="topo-chip active" data-type="">همه</button>
            ${Object.entries(typeColors).map(([k, v]) =>
              `<button type="button" class="topo-chip" data-type="${k}"><i style="background:${v}"></i>${k}</button>`
            ).join("")}
          </div>
        </div>
      </div>
      <div class="elev-page">
        <aside class="elev-detail-bar panel">
          <div class="elev-detail-head">
            <h3><span class="dot"></span> جزئیات اتصالات</h3>
            <div class="elev-legend-inline">
              ${Object.entries(typeColors).map(([k, v]) =>
                `<span class="elev-leg"><i style="background:${v}"></i>${k}</span>`
              ).join("")}
            </div>
          </div>
          <div id="topoDetail" class="elev-detail-body">
            <div class="elev-detail-empty">
              <div class="elev-detail-empty-icon">🔌</div>
              <p>روی یک دستگاه در نقشه کلیک کنید</p>
              <p class="muted" style="font-size:.78rem;line-height:1.6">شاسی، پورت‌ها و مقصد هر لینک اینجا نمایش داده می‌شود.</p>
            </div>
          </div>
        </aside>
        <div class="elev-stage" id="elevStage"></div>
      </div>`;

    let currentDc = "";
    let topoSearch = "";
    let topoLinkType = "";
    const paint = async () => {
      $$("#dcTabs .dc-tab").forEach((b) => b.classList.toggle("active", (b.dataset.dc || "") === currentDc));
      const q = currentDc ? `&datacenter_id=${encodeURIComponent(currentDc)}` : "";
      const data = await api(`/api/topology?mode=elevation${q}`);
      renderElevation(data, {
        editMode: topoEditMode,
        onReload: () => paint().catch((e) => toast(e.message, true)),
        searchQuery: topoSearch,
        linkType: topoLinkType,
      });
    };

    $$("#dcTabs .dc-tab").forEach((b) => {
      b.onclick = () => {
        currentDc = b.dataset.dc || "";
        paint().catch((e) => toast(e.message, true));
      };
    });

    const searchInput = $("#topoSearch");
    if (searchInput) {
      let st = null;
      searchInput.value = topoSearch;
      searchInput.oninput = () => {
        clearTimeout(st);
        st = setTimeout(() => {
          topoSearch = (searchInput.value || "").trim().toLowerCase();
          paint().catch((e) => toast(e.message, true));
        }, 180);
      };
    }
    $$("#topoTypeChips .topo-chip").forEach((b) => {
      b.onclick = () => {
        topoLinkType = b.dataset.type || "";
        $$("#topoTypeChips .topo-chip").forEach((x) => x.classList.toggle("active", (x.dataset.type || "") === topoLinkType));
        paint().catch((e) => toast(e.message, true));
      };
      b.classList.toggle("active", (b.dataset.type || "") === topoLinkType);
    });

    const editBtn = $("#topoEditBtn");
    if (editBtn) {
      editBtn.onclick = () => {
        topoEditMode = !topoEditMode;
        topoEditState = null;
        editBtn.classList.toggle("btn-primary", topoEditMode);
        editBtn.classList.toggle("btn-ghost", !topoEditMode);
        editBtn.textContent = topoEditMode ? "✓ پایان ویرایش" : "✎ حالت ویرایش ارتباط";
        paint().catch((e) => toast(e.message, true));
        toast(topoEditMode ? "حالت ویرایش: دستگاه مبدأ را انتخاب کنید" : "حالت مشاهده");
      };
      editBtn.classList.toggle("btn-primary", topoEditMode);
      editBtn.classList.toggle("btn-ghost", !topoEditMode);
      editBtn.textContent = topoEditMode ? "✓ پایان ویرایش" : "✎ حالت ویرایش ارتباط";
    }
    await paint();
  } catch (e) {
    console.error("topology error", e);
    el.innerHTML = `<div class="panel"><p style="color:#fca5a5">خطا در بارگذاری توپولوژی: ${e.message || e}</p>
      <p class="muted" style="font-size:.85rem">اگر تازه آپدیت کرده‌اید، یک‌بار Ctrl+Shift+R بزنید. API را هم چک کنید: <code>/api/topology?mode=elevation</code></p></div>`;
  }
}






function renderElevation(data, opts = {}) {

  const stage = $("#elevStage");
  if (!stage) return;

  const racks = data.racks || [];
  const devices = data.devices || [];
  const linkTypeFilter = ((opts && opts.linkType) || "").toLowerCase();
  const searchQuery = ((opts && opts.searchQuery) || "").toLowerCase();
  const allLinks = (data.links || []).filter((l) => (l.status || "active") !== "inactive");
  const links = linkTypeFilter
    ? allLinks.filter((l) => String(l.connection_type || "network").toLowerCase() === linkTypeFilter)
    : allLinks;
  const editMode = !!(opts && opts.editMode);
  const onReload = (opts && opts.onReload) || (() => {});
  const U_H = 16;
  const RACK_W = 200;
  const GAP = 88;
  const PAD_TOP = 56; // must match CSS .elev-rack top
  const PAD_X = 40;

  if (!racks.length) {
    stage.innerHTML = `<p class="muted" style="padding:16px">هیچ رکی برای نمایش نیست.</p>`;
    return;
  }

  const maxU = Math.max(...racks.map((r) => +r.total_units || 42), 42);
  const stageH = PAD_TOP + 40 + maxU * U_H + 28;
  const stageW = PAD_X * 2 + racks.length * RACK_W + Math.max(0, racks.length - 1) * GAP;

  const byRack = {};
  devices.forEach((d) => {
    const rid = String(d.rack_id);
    byRack[rid] = byRack[rid] || [];
    byRack[rid].push(d);
  });

  const RACK_COLORS = ["#c084fc", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#c084fc", "#e9d5ff"];
  const nameById = {};
  devices.forEach((d) => { nameById[String(d.device_id)] = d.name || `Device ${d.device_id}`; });

  // ports used per device from connections
  const portsByDevice = {};
  links.forEach((l) => {
    const add = (id, port) => {
      const k = String(id);
      if (!k || k === "undefined" || k === "null") return;
      const p = String(port || "?").trim() || "?";
      const t = String(l.connection_type || "network").toLowerCase();
      portsByDevice[k] = portsByDevice[k] || [];
      if (!portsByDevice[k].some((x) => x.port === p)) {
        portsByDevice[k].push({ port: p, type: t });
      }
    };
    add(l.source_device_id, l.source_port);
    add(l.destination_device_id, l.destination_port);
  });
  Object.keys(portsByDevice).forEach((k) => {
    portsByDevice[k].sort((a, b) => {
      const na = parseInt(String(a.port).replace(/\D/g, ""), 10);
      const nb = parseInt(String(b.port).replace(/\D/g, ""), 10);
      if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
      return String(a.port).localeCompare(String(b.port));
    });
  });

  let racksHtml = "";
  racks.forEach((r, ri) => {
    const total = +r.total_units || 42;
    const h = total * U_H;
    const x = PAD_X + ri * (RACK_W + GAP);
    const col = RACK_COLORS[ri % RACK_COLORS.length];
    const name = r.rack_name || `Rack ${r.rack_id}`;
    const devs = (byRack[String(r.rack_id)] || []).slice().sort((a, b) => b.end_unit - a.end_unit);

    let unitsHtml = "";
    for (let u = total; u >= 1; u--) {
      if (u % 5 === 0 || u === 1 || u === total) {
        unitsHtml += `<div class="elev-u" style="top:${(total - u) * U_H}px">U${u}</div>`;
      }
    }

    let devHtml = "";
    devs.forEach((d) => {
      const top = (total - d.end_unit) * U_H;
      const height = Math.max(U_H - 2, (d.units || 1) * U_H - 3);
      const short = d.name || `Device ${d.device_id}`;
      const label = short.length > 24 ? short.slice(0, 22) + "…" : short;
      const linkCount = links.filter(
        (l) => String(l.source_device_id) === String(d.device_id) || String(l.destination_device_id) === String(d.device_id)
      ).length;
      const noLink = linkCount === 0;
      const role = classifyDeviceRole(short, d.model || "");
      const roleIcon = {
        switch: "▣",
        router: "◈",
        server: "▤",
        storage: "▦",
        firewall: "⬡",
        patch: "≣",
        cable: "≈",
        power: "⚡",
        kvm: "⌨",
        other: "▪",
      }[role] || "▪";
      const compact = height < 22 ? " is-compact" : "";
      const uLabel = d.start_unit === d.end_unit ? `U${d.start_unit}` : `U${d.start_unit}-${d.end_unit}`;
      const searchHit = searchQuery && short.toLowerCase().includes(searchQuery);
      const searchMiss = searchQuery && !short.toLowerCase().includes(searchQuery);

      devHtml += `
        <div class="elev-dev elev-dev-card role-${role}${compact} ${noLink ? "elev-dev-idle" : ""} ${searchHit ? "elev-dev-hit" : ""} ${searchMiss ? "elev-dev-miss" : ""}"
             data-device="${d.device_id}" data-rack="${r.rack_id}" data-role="${role}"
             title="${short} · ${role} · ${uLabel}${linkCount ? " · " + linkCount + " link" : ""}"
             style="top:${top + 1}px;height:${height}px;--dev-accent:${col}">
          <span class="dev-role-icon" title="${role}">${roleIcon}</span>
          <span class="elev-dev-led"></span>
          <span class="elev-dev-name">${label}</span>
          ${linkCount ? `<span class="elev-dev-lc" title="${linkCount} connection">${linkCount}</span>` : ""}
          <span class="elev-dev-u">${uLabel}</span>
        </div>`;
    });

    racksHtml += `
      <div class="elev-rack" data-rack="${r.rack_id}" style="left:${x}px;width:${RACK_W}px;top:${PAD_TOP}px;--rack-accent:${col}">
        <div class="elev-rack-head">${name}<small>${devs.length} device</small></div>
        <div class="elev-rack-body" style="height:${h}px">${unitsHtml}${devHtml}</div>
        <div class="elev-rack-foot"></div>
      </div>`;
  });

  stage.innerHTML = `
    <div class="elev-toolbar">
      <div class="elev-toolbar-left">
        <button type="button" class="btn btn-ghost btn-sm" id="elevFs" title="تمام‌صفحه">⛶</button>
        <button type="button" class="btn btn-ghost btn-sm" id="elevZoomOut" title="کوچک">－</button>
        <span class="elev-zoom-lbl" id="elevZoomLbl">100%</span>
        <button type="button" class="btn btn-ghost btn-sm" id="elevZoomIn" title="بزرگ">＋</button>
        <button type="button" class="btn btn-ghost btn-sm" id="elevZoomFit">Fit</button>
        <button type="button" class="btn btn-ghost btn-sm" id="elevReset">بازنشانی</button>
      </div>
      <span class="elev-hint" id="elevHint">اسکرول = زوم · درگ = جابه‌جایی · کلیک = جزئیات پورت</span>
    </div>
    <div class="elev-viewport" id="elevViewport">
      <div class="elev-canvas" id="elevCanvas" style="width:${stageW}px;height:${stageH}px">
        <svg class="elev-svg" id="elevSvg" width="${stageW}" height="${stageH}"></svg>
        ${racksHtml}
        <div id="elevTip" class="elev-tip" hidden></div>
      </div>
    </div>`;

  const canvas = stage.querySelector("#elevCanvas");
  const svg = stage.querySelector("#elevSvg");
  const tipEl = stage.querySelector("#elevTip");
  const viewport = stage.querySelector("#elevViewport");

  let zoom = 1;
  let panX = 0;
  let panY = 0;
  const zoomLbl = stage.querySelector("#elevZoomLbl");
  const applyTransform = () => {
    canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    canvas.style.transformOrigin = "0 0";
    if (zoomLbl) zoomLbl.textContent = Math.round(zoom * 100) + "%";
  };
  const fitZoom = () => {
    if (!viewport || !canvas) return;
    const vw = viewport.clientWidth - 8;
    const vh = viewport.clientHeight - 8;
    if (vw <= 0 || vh <= 0) return;
    const sx = vw / stageW;
    const sy = vh / stageH;
    zoom = Math.min(sx, sy, 1.15);
    // center content
    panX = Math.max(0, (vw - stageW * zoom) / 2);
    panY = Math.max(0, (vh - stageH * zoom) / 2);
    applyTransform();
  };
  const applyZoom = () => applyTransform();

  stage.querySelector("#elevZoomIn")?.addEventListener("click", () => {
    zoom = Math.min(3, zoom + 0.15);
    applyTransform();
  });
  stage.querySelector("#elevZoomOut")?.addEventListener("click", () => {
    zoom = Math.max(0.25, zoom - 0.15);
    applyTransform();
  });
  stage.querySelector("#elevZoomFit")?.addEventListener("click", fitZoom);

  // Wheel zoom (scroll = zoom toward cursor)
  viewport?.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    // point in canvas space before zoom
    const cx = (mx - panX) / zoom;
    const cy = (my - panY) / zoom;
    const delta = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.min(3, Math.max(0.25, zoom * delta));
    panX = mx - cx * next;
    panY = my - cy * next;
    zoom = next;
    applyTransform();
  }, { passive: false });

  // Drag to pan
  let dragging = false;
  let lastX = 0, lastY = 0;
  viewport?.addEventListener("mousedown", (e) => {
    // only left button; ignore clicks starting on device (they handle click)
    if (e.button !== 0) return;
    if (e.target.closest(".elev-dev")) return;
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    viewport.classList.add("panning");
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    panX += e.clientX - lastX;
    panY += e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    applyTransform();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    viewport?.classList.remove("panning");
  });

  function layoutAnchors() {
    /* deterministic anchors from layout constants — independent of zoom/tab visibility */
    const headEl = stage.querySelector(".elev-rack-head");
    const headH = headEl && headEl.offsetHeight > 0 ? headEl.offsetHeight : 42;
    const anchors = {};
    racks.forEach((r, ri) => {
      const total = +r.total_units || 42;
      const x = PAD_X + ri * (RACK_W + GAP);
      const devs = byRack[String(r.rack_id)] || [];
      devs.forEach((d) => {
        const top = (total - d.end_unit) * U_H;
        const height = Math.max(U_H - 2, (d.units || 1) * U_H - 3);
        const cy = PAD_TOP + headH + top + 1 + height / 2;
        const left = x + 26;
        const right = x + RACK_W - 6;
        anchors[String(d.device_id)] = {
          left, right, cy,
          mid: (left + right) / 2,
          rack: String(r.rack_id),
        };
      });
    });
    return anchors;
  }

  function measureAnchors() {
    /* prefer layout math; optionally refine with DOM if visible and sized */
    const base = layoutAnchors();
    if (!canvas) return base;
    const ow = canvas.offsetWidth || stageW;
    const oh = canvas.offsetHeight || stageH;
    if (ow < 10 || oh < 10) return base;
    const cRect = canvas.getBoundingClientRect();
    if (cRect.width < 10 || cRect.height < 10) return base;
    const sx = cRect.width / ow;
    const sy = cRect.height / oh;
    if (!isFinite(sx) || !isFinite(sy) || sx < 0.05 || sy < 0.05) return base;

    let refined = 0;
    stage.querySelectorAll(".elev-dev").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      const id = String(el.dataset.device);
      base[id] = {
        left: (r.left - cRect.left) / sx,
        right: (r.right - cRect.left) / sx,
        cy: ((r.top + r.bottom) / 2 - cRect.top) / sy,
        mid: ((r.left + r.right) / 2 - cRect.left) / sx,
        rack: el.dataset.rack,
      };
      refined++;
    });
    return base;
  }


  function drawLinks(filterDeviceId) {
    const anchors = measureAnchors();
    // group links by pair to fan out parallel cables
    const pairCount = {};
    const pairIndex = {};
    links.forEach((l) => {
      const a = String(Math.min(+l.source_device_id, +l.destination_device_id));
      const b = String(Math.max(+l.source_device_id, +l.destination_device_id));
      const key = a + ":" + b;
      pairCount[key] = (pairCount[key] || 0) + 1;
    });
    const usedPair = {};

    let paths = "";
    let markers = `
      <defs>
</defs>`;

    let idx = 0;
    links.forEach((l) => {
      const sid = String(l.source_device_id);
      const did = String(l.destination_device_id);
      if (filterDeviceId && sid !== filterDeviceId && did !== filterDeviceId) return;
      const a = anchors[sid];
      const b = anchors[did];
      if (!a || !b) return;

      const same = String(a.rack) === String(b.rack);
      const ctype = (l.connection_type || "network").toLowerCase();
      const color = l.color || ({
        fiber: "#4ade80",
        copper: "#fb923c",
        network: "#60a5fa",
        power: "#facc15",
        other: "#94a3b8",
      }[ctype] || "#94a3b8");

      const key = String(Math.min(+sid, +did)) + ":" + String(Math.max(+sid, +did));
      const nParallel = pairCount[key] || 1;
      const pIdx = usedPair[key] || 0;
      usedPair[key] = pIdx + 1;
      // fan offset -1..1 centered
      const fan = nParallel === 1 ? 0 : (pIdx - (nParallel - 1) / 2);

      let dpath;
      if (same) {
        // rounded "bus" on the right of rack — clear vertical channels
        const base = Math.max(a.right, b.right) + 18;
        const side = base + Math.abs(fan) * 14 + (idx % 3) * 6;
        const y1 = a.cy + fan * 3;
        const y2 = b.cy + fan * 3;
        // orthogonal-ish: out → vertical → in
        const midY = (y1 + y2) / 2;
        dpath = `M ${a.right} ${y1}
                 L ${side} ${y1}
                 Q ${side + 8} ${y1} ${side + 8} ${midY}
                 Q ${side + 8} ${y2} ${side} ${y2}
                 L ${b.right} ${y2}`;
      } else {
        const leftToRight = a.mid < b.mid;
        const x1 = leftToRight ? a.right : a.left;
        const x2 = leftToRight ? b.left : b.right;
        const y1 = a.cy + fan * 4;
        const y2 = b.cy + fan * 4;
        const dx = Math.abs(x2 - x1);
        const bulge = Math.min(90, Math.max(36, dx * 0.22)) + fan * 16;
        const mx = (x1 + x2) / 2;
        // smooth S-curve, parallel cables offset by fan
        dpath = `M ${x1} ${y1}
                 C ${mx} ${y1 - bulge},
                   ${mx} ${y2 + bulge},
                   ${x2} ${y2}`;
      }

      const tip = `${ctype.toUpperCase()} | ${nameById[sid] || sid} [${l.source_port || "?"}] → ${nameById[did] || did} [${l.destination_port || "?"}]`;
      const wCore = same ? 2.2 : 2.4;

      // main path
      paths += `<path class="elev-link elev-link-${ctype} ${same ? "elev-link-in" : "elev-link-out"} ${filterDeviceId ? "on" : ""}"
        data-src="${sid}" data-dst="${did}" data-tip="${tip.replace(/"/g, "&quot;")}"
        data-type="${ctype}"
        d="${dpath}" stroke="${color}"
        stroke-width="${wCore}" fill="none" stroke-linecap="round" stroke-linejoin="round"
        style="animation-delay:${(idx % 9) * 0.08}s"></path>`;
      idx++;
    });

    svg.innerHTML = markers + `<g>${paths}</g>`;

    svg.querySelectorAll(".elev-link").forEach((p) => {
      p.addEventListener("mouseenter", () => {
        p.classList.add("hover");
        tipEl.hidden = false;
        tipEl.textContent = p.dataset.tip || "";
      });
      p.addEventListener("mousemove", (ev) => {
        const rect = canvas.getBoundingClientRect();
        tipEl.style.left = (ev.clientX - rect.left) / zoom + 12 + "px";
        tipEl.style.top = (ev.clientY - rect.top) / zoom + 12 + "px";
      });
      p.addEventListener("mouseleave", () => {
        p.classList.remove("hover");
        tipEl.hidden = true;
      });
    });
  }

  function showPortBox(deviceId) {
    const id = String(deviceId);
    const related = links.filter(
      (l) => String(l.source_device_id) === id || String(l.destination_device_id) === id
    );
    const dev = devices.find((d) => String(d.device_id) === id);
    const title = nameById[id] || ("Device " + id);

    // Build port map: localPort -> [{...}]
    const portMap = {};
    related.forEach((l) => {
      const isSrc = String(l.source_device_id) === id;
      const localPort = String(isSrc ? (l.source_port || "?") : (l.destination_port || "?"));
      const remotePort = String(isSrc ? (l.destination_port || "?") : (l.source_port || "?"));
      const remoteId = String(isSrc ? l.destination_device_id : l.source_device_id);
      const entry = {
        localPort,
        remotePort,
        remoteId,
        remoteName: nameById[remoteId] || remoteId,
        dir: isSrc ? "out" : "in",
        type: (l.connection_type || "network").toLowerCase(),
        color: l.color || "#64748b",
        connection_id: l.connection_id,
      };
      portMap[localPort] = portMap[localPort] || [];
      portMap[localPort].push(entry);
    });

    const portKeys = Object.keys(portMap).sort((a, b) => {
      const na = parseInt(String(a).replace(/\D/g, ""), 10);
      const nb = parseInt(String(b).replace(/\D/g, ""), 10);
      if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
      return String(a).localeCompare(String(b));
    });

    // switch faceplate ports
    const portsHtml = portKeys.map((pk) => {
      const entries = portMap[pk];
      const primary = entries[0];
      const t = primary.type;
      const tip = entries.map((e) =>
        `${e.dir === "out" ? "→" : "←"} ${e.remoteName} [${e.remotePort}] (${e.type})`
      ).join(" | ");
      return `
        <button type="button" class="sw-port sw-port-${t}" data-port="${pk.replace(/"/g, "&quot;")}"
                title="${tip.replace(/"/g, "&quot;")}">
          <span class="sw-port-led"></span>
          <span class="sw-port-num">${pk}</span>
          <span class="sw-port-link">${primary.remoteName.length > 14 ? primary.remoteName.slice(0, 12) + "…" : primary.remoteName}</span>
        </button>`;
    }).join("");

    const rows = related.map((l) => {
      const isSrc = String(l.source_device_id) === id;
      const localPort = isSrc ? (l.source_port || "?") : (l.destination_port || "?");
      const remotePort = isSrc ? (l.destination_port || "?") : (l.source_port || "?");
      const remoteId = isSrc ? String(l.destination_device_id) : String(l.source_device_id);
      const remoteName = nameById[remoteId] || remoteId;
      const dir = isSrc ? "خروجی" : "ورودی";
      const ctype = (l.connection_type || "network").toLowerCase();
      return `
        <div class="port-card" data-highlight-port="${String(localPort).replace(/"/g, "&quot;")}">
          <div class="port-card-top">
            <span class="port-badge">${localPort}</span>
            <span class="port-dir">${dir}</span>
            <span class="port-type port-type-${ctype}">${ctype}</span>
          </div>
          <div class="port-card-body">
            <span class="port-arrow">${isSrc ? "→" : "←"}</span>
            <div>
              <div class="port-remote-name">${remoteName}</div>
              <div class="port-remote-port">port: ${remotePort}</div>
            </div>
          </div>
        </div>`;
    }).join("");

    $("#topoDetail").innerHTML = `
      <div class="port-box-wide">
        <div class="port-box-wide-head">
          <div>
            <div class="port-box-title">${title}</div>
            <div class="port-box-sub">ID ${id}${dev ? ` · U${dev.start_unit}-${dev.end_unit}` : ""} · ${related.length} اتصال · ${portKeys.length} پورت</div>
          </div>
          <button class="btn btn-ghost btn-sm" id="elevClear">بازنشانی</button>
        </div>

        <div class="sw-chassis">
          <div class="sw-chassis-bar">
            <span class="sw-brand">DC FABRIC</span>
            <span class="sw-model">${title}</span>
            <span class="sw-status"><i></i> ACTIVE</span>
          </div>
          <div class="sw-ports">
            ${portsHtml || '<div class="muted" style="padding:12px">پورتی ثبت نشده</div>'}
          </div>
          <div class="sw-chassis-foot">
            <span>PWR</span><span class="sw-led-on"></span>
            <span>SYS</span><span class="sw-led-on"></span>
            <span class="sw-hint">هاور روی پورت = مقصد · کلیک = هایلایت لینک</span>
          </div>
        </div>

        <div class="port-box-grid" style="margin-top:10px">
          ${rows || '<p class="muted">اتصالی ثبت نشده</p>'}
        </div>
      </div>`;

    $("#elevClear")?.addEventListener("click", clearFilter);

    // click port on chassis → highlight related SVG links only for that port
    [...(($("#topoDetail")||document).querySelectorAll(".sw-port"))].forEach((btn) => {
      btn.addEventListener("click", () => {
        const pk = btn.dataset.port;
        [...($("#topoDetail").querySelectorAll(".sw-port"))].forEach((b) => b.classList.toggle("on", b === btn));
        [...($("#topoDetail").querySelectorAll(".port-card"))].forEach((c) => {
          c.classList.toggle("dim", c.dataset.highlightPort !== pk);
          c.classList.toggle("on", c.dataset.highlightPort === pk);
        });
        // highlight SVG links matching this local port for this device
        const matchIds = new Set();
        related.forEach((l) => {
          const isSrc = String(l.source_device_id) === id;
          const localPort = String(isSrc ? (l.source_port || "?") : (l.destination_port || "?"));
          if (localPort === pk) {
            matchIds.add(String(l.source_device_id));
            matchIds.add(String(l.destination_device_id));
          }
        });
        stage.querySelectorAll(".elev-link").forEach((p) => {
          const hit =
            (String(p.dataset.src) === id || String(p.dataset.dst) === id) &&
            related.some((l) => {
              const isSrc = String(l.source_device_id) === id;
              const localPort = String(isSrc ? (l.source_port || "?") : (l.destination_port || "?"));
              if (localPort !== pk) return false;
              return (
                (String(p.dataset.src) === String(l.source_device_id) &&
                  String(p.dataset.dst) === String(l.destination_device_id)) ||
                (String(p.dataset.src) === String(l.destination_device_id) &&
                  String(p.dataset.dst) === String(l.source_device_id))
              );
            });
          p.classList.toggle("on", hit);
          p.classList.toggle("dim", !hit);
        });
      });
    });
  }

  const clearFilter = () => {
    stage.querySelectorAll(".elev-dev").forEach((n) => n.classList.remove("on", "dim"));
    drawLinks(null);
    requestAnimationFrame(() => drawLinks(null));
    $("#topoDetail").innerHTML = `<div class="elev-detail-empty"><div class="elev-detail-empty-icon">🔌</div><p>روی یک دستگاه در نقشه کلیک کنید</p><p class="muted" style="font-size:.78rem;line-height:1.6">شاسی سوئیچ، پورت‌ها و مقصد هر لینک اینجا نمایش داده می‌شود.</p></div>`;
  };

  stage.querySelector("#elevReset")?.addEventListener("click", clearFilter);
  stage.querySelector("#elevFs")?.addEventListener("click", () => {
    const page = stage.closest(".elev-page") || stage;
    if (!document.fullscreenElement) {
      (page.requestFullscreen || page.webkitRequestFullscreen)?.call(page);
      setTimeout(fitZoom, 100);
    } else {
      document.exitFullscreen?.();
      setTimeout(fitZoom, 100);
    }
  });


  /* ----- rubber-band SVG for edit mode ----- */
  let rubber = null;
  const ensureRubber = () => {
    if (rubber) return rubber;
    rubber = document.createElementNS("http://www.w3.org/2000/svg", "line");
    rubber.setAttribute("class", "elev-rubber");
    rubber.setAttribute("stroke", "#e9d5ff");
    rubber.setAttribute("stroke-width", "2.5");
    rubber.setAttribute("stroke-dasharray", "6 4");
    rubber.setAttribute("stroke-linecap", "round");
    svg.appendChild(rubber);
    return rubber;
  };
  const hideRubber = () => {
    if (rubber) {
      rubber.remove();
      rubber = null;
    }
  };
  const deviceAnchor = (id) => {
    const a = measureAnchors()[String(id)];
    return a || null;
  };
  const setRubber = (x1, y1, x2, y2) => {
    const r = ensureRubber();
    r.setAttribute("x1", x1);
    r.setAttribute("y1", y1);
    r.setAttribute("x2", x2);
    r.setAttribute("y2", y2);
  };

  const showEditPanel = (html) => {
    const box = $("#topoDetail");
    if (box) box.innerHTML = html;
  };

  const renderEditHome = () => {
    showEditPanel(`
      <div class="edit-panel">
        <div class="edit-badge">EDIT MODE</div>
        <h3 style="margin:8px 0 6px">ساخت / حذف ارتباط</h3>
        <p class="muted" style="font-size:.82rem;line-height:1.65">
          ۱) روی <b>دستگاه مبدأ</b> کلیک کنید<br/>
          ۲) پورت مبدأ را انتخاب کنید<br/>
          ۳) خط را روی <b>دستگاه مقصد</b> ببرید و کلیک کنید<br/>
          ۴) پورت مقصد و نوع لینک را مشخص کنید
        </p>
        <div class="edit-types">
          <span class="elev-leg"><i style="background:#4ade80"></i>fiber</span>
          <span class="elev-leg"><i style="background:#fb923c"></i>copper</span>
          <span class="elev-leg"><i style="background:#60a5fa"></i>network</span>
          <span class="elev-leg"><i style="background:#facc15"></i>power</span>
        </div>
      </div>`);
  };

  const portOptionsHtml = (deviceId, ports) => {
    const used = new Set((ports || []).map((p) => String(p.port)));
    const opts = [...used].map((p) => `<option value="${p}">${p}</option>`).join("");
    return `
      <label class="field"><span>پورت (موجود یا جدید)</span>
        <input list="portList_${deviceId}" id="editPortInput" class="mono" placeholder="مثلاً port 12" />
        <datalist id="portList_${deviceId}">${opts}</datalist>
      </label>`;
  };

  const openSourceEditor = async (deviceId, rackId) => {
    const name = nameById[String(deviceId)] || deviceId;
    let portInfo = { links: [], device: {} };
    try {
      portInfo = await api(`/api/devices/${encodeURIComponent(deviceId)}/ports`);
    } catch (_) {}
    const existing = (portsByDevice[String(deviceId)] || []).map((p) => p.port);
    const fromLinks = (portInfo.links || []).map((l) => l.local_port);
    const allPorts = [...new Set([...existing, ...fromLinks])].filter(Boolean);

    showEditPanel(`
      <div class="edit-panel">
        <div class="edit-badge">STEP 1 · مبدأ</div>
        <div class="edit-dev-name">${name}</div>
        <div class="muted mono" style="font-size:.75rem;margin-bottom:10px">ID ${deviceId} · rack ${rackId}</div>
        ${portOptionsHtml(deviceId, allPorts.map((p) => ({ port: p })))}
        <button class="btn btn-primary" type="button" id="editPickSrcPort">ادامه — کشیدن به مقصد</button>
        <button class="btn btn-ghost btn-sm" type="button" id="editCancel" style="margin-top:8px">انصراف</button>
        <hr style="border:none;border-top:1px solid rgba(148,163,184,.15);margin:14px 0" />
        <h4 style="margin:0 0 8px;font-size:.85rem">ارتباط‌های فعلی</h4>
        <div class="edit-conn-list" id="editConnList"></div>
      </div>`);

    const list = $("#editConnList");
    const rel = links.filter(
      (l) => String(l.source_device_id) === String(deviceId) || String(l.destination_device_id) === String(deviceId)
    );
    if (list) {
      list.innerHTML = rel.length
        ? rel
            .map((l) => {
              const isSrc = String(l.source_device_id) === String(deviceId);
              const local = isSrc ? l.source_port : l.destination_port;
              const remote = isSrc ? l.destination_device_id : l.source_device_id;
              const rport = isSrc ? l.destination_port : l.source_port;
              return `<div class="edit-conn-row">
                <div><b>${local}</b> → ${nameById[String(remote)] || remote} <span class="muted">[${rport}]</span>
                <span class="badge soft">${l.connection_type || ""}</span></div>
                <button class="btn btn-danger btn-sm" type="button" data-del-conn="${l.connection_id}">حذف</button>
              </div>`;
            })
            .join("")
        : `<p class="muted" style="font-size:.8rem">اتصالی نیست</p>`;
      list.querySelectorAll("[data-del-conn]").forEach((b) => {
        b.onclick = async () => {
          if (!confirm("این ارتباط حذف شود؟")) return;
          try {
            await api(`/api/connections/${b.dataset.delConn}`, { method: "DELETE" });
            toast("حذف شد");
            onReload();
          } catch (e) {
            toast(e.message, true);
          }
        };
      });
    }

    $("#editCancel")?.addEventListener("click", () => {
      topoEditState = null;
      hideRubber();
      renderEditHome();
      stage.querySelectorAll(".elev-dev").forEach((n) => n.classList.remove("on", "dim", "edit-src", "edit-dst"));
      drawLinks(null);
    });

    $("#editPickSrcPort")?.addEventListener("click", () => {
      const port = ($("#editPortInput")?.value || "").trim();
      if (!port) return toast("پورت مبدأ را وارد/انتخاب کنید", true);
      topoEditState = {
        step: "drag",
        src: { device_id: deviceId, rack_id: rackId, port, name },
      };
      const a = deviceAnchor(deviceId);
      if (a) setRubber(a.right, a.cy, a.right + 20, a.cy);
      toast("حالا روی دستگاه مقصد کلیک کنید");
      showEditPanel(`
        <div class="edit-panel">
          <div class="edit-badge">STEP 2 · مقصد</div>
          <p>مبدأ: <b>${name}</b> / <span class="mono">${port}</span></p>
          <p class="muted" style="font-size:.82rem;line-height:1.6">خط را روی دستگاه مقصد ببرید و کلیک کنید.</p>
          <button class="btn btn-ghost btn-sm" type="button" id="editCancel2">انصراف</button>
        </div>`);
      $("#editCancel2")?.addEventListener("click", () => {
        topoEditState = null;
        hideRubber();
        renderEditHome();
        stage.querySelectorAll(".elev-dev").forEach((n) => n.classList.remove("on", "dim", "edit-src", "edit-dst"));
      });
    });
  };

  const openDestEditor = async (deviceId, rackId) => {
    if (!topoEditState || topoEditState.step !== "drag") return;
    if (String(deviceId) === String(topoEditState.src.device_id)) {
      return toast("دستگاه مقصد باید متفاوت باشد", true);
    }
    const name = nameById[String(deviceId)] || deviceId;
    let portInfo = { links: [] };
    try {
      portInfo = await api(`/api/devices/${encodeURIComponent(deviceId)}/ports`);
    } catch (_) {}
    const existing = (portsByDevice[String(deviceId)] || []).map((p) => p.port);
    const fromLinks = (portInfo.links || []).map((l) => l.local_port);
    const allPorts = [...new Set([...existing, ...fromLinks])].filter(Boolean);

    topoEditState.step = "dest";
    topoEditState.dst = { device_id: deviceId, rack_id: rackId, name };
    nodeHighlight(topoEditState.src.device_id, deviceId);

    showEditPanel(`
      <div class="edit-panel">
        <div class="edit-badge">STEP 3 · تأیید</div>
        <div class="edit-link-summary">
          <div><span class="muted">مبدأ</span><br/><b>${topoEditState.src.name}</b><br/><span class="mono">${topoEditState.src.port}</span></div>
          <div class="edit-arrow">→</div>
          <div><span class="muted">مقصد</span><br/><b>${name}</b></div>
        </div>
        ${portOptionsHtml(deviceId, allPorts.map((p) => ({ port: p })))}
        <label class="field"><span>نوع ارتباط</span>
          <select id="editConnType">
            <option value="copper">Copper</option>
            <option value="fiber">Fiber</option>
            <option value="network">Network</option>
            <option value="power">Power</option>
            <option value="other">Other</option>
          </select>
        </label>
        <button class="btn btn-primary" type="button" id="editCreateConn">ایجاد ارتباط</button>
        <button class="btn btn-ghost btn-sm" type="button" id="editCancel3" style="margin-top:8px">انصراف</button>
      </div>`);

    $("#editCancel3")?.addEventListener("click", () => {
      topoEditState = null;
      hideRubber();
      renderEditHome();
      stage.querySelectorAll(".elev-dev").forEach((n) => n.classList.remove("on", "dim", "edit-src", "edit-dst"));
      drawLinks(null);
    });

    $("#editCreateConn")?.addEventListener("click", async () => {
      const dport = ($("#editPortInput")?.value || "").trim();
      if (!dport) return toast("پورت مقصد را وارد کنید", true);
      const ctype = $("#editConnType")?.value || "copper";
      try {
        await api("/api/connections", {
          method: "POST",
          body: JSON.stringify({
            source_rack_id: Number(topoEditState.src.rack_id),
            source_device_id: Number(topoEditState.src.device_id),
            source_port: topoEditState.src.port,
            destination_rack_id: Number(rackId),
            destination_device_id: Number(deviceId),
            destination_port: dport,
            connection_type: ctype,
            status: "active",
            notes: "",
          }),
        });
        toast("ارتباط ایجاد شد");
        topoEditState = null;
        hideRubber();
        onReload();
      } catch (e) {
        toast(e.message, true);
      }
    });
  };

  const nodeHighlight = (srcId, dstId) => {
    stage.querySelectorAll(".elev-dev").forEach((n) => {
      const id = String(n.dataset.device);
      n.classList.toggle("edit-src", id === String(srcId));
      n.classList.toggle("edit-dst", dstId && id === String(dstId));
      n.classList.toggle("dim", id !== String(srcId) && id !== String(dstId));
      n.classList.toggle("on", id === String(srcId) || id === String(dstId));
    });
  };

  if (editMode) {
    stage.classList.add("elev-edit-mode");
    if (!topoEditState || topoEditState.step === "idle") renderEditHome();
    else if (topoEditState.step === "drag" && topoEditState.src) {
      // restore rubber message
      showEditPanel(`
        <div class="edit-panel">
          <div class="edit-badge">STEP 2 · مقصد</div>
          <p>مبدأ: <b>${topoEditState.src.name}</b> / <span class="mono">${topoEditState.src.port}</span></p>
          <p class="muted">روی دستگاه مقصد کلیک کنید</p>
        </div>`);
    }

    viewport?.addEventListener("mousemove", (e) => {
      if (!topoEditState || topoEditState.step !== "drag" || !topoEditState.src) return;
      const a = deviceAnchor(topoEditState.src.device_id);
      if (!a || !canvas) return;
      const cRect = canvas.getBoundingClientRect();
      const ow = canvas.offsetWidth || 1;
      const oh = canvas.offsetHeight || 1;
      const sx = cRect.width / ow;
      const sy = cRect.height / oh;
      const x2 = (e.clientX - cRect.left) / (sx || 1);
      const y2 = (e.clientY - cRect.top) / (sy || 1);
      setRubber(a.right, a.cy, x2, y2);
    });
  } else {
    stage.classList.remove("elev-edit-mode");
    hideRubber();
  }

  stage.querySelectorAll(".elev-dev").forEach((node) => {
    node.style.pointerEvents = "auto";
    node.style.cursor = "pointer";
    node.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const id = String(node.dataset.device);
      const rackId = node.dataset.rack;

      if (editMode) {
        if (!topoEditState || !topoEditState.step || topoEditState.step === "idle") {
          topoEditState = { step: "src" };
          nodeHighlight(id, null);
          openSourceEditor(id, rackId);
          return;
        }
        if (topoEditState.step === "drag") {
          openDestEditor(id, rackId);
          return;
        }
        // if on dest form, clicking another device restarts dest
        if (topoEditState.step === "dest") {
          openDestEditor(id, rackId);
          return;
        }
        return;
      }

      // view mode
      const related = links.filter(
        (l) => String(l.source_device_id) === id || String(l.destination_device_id) === id
      );
      const relatedIds = new Set([id]);
      related.forEach((l) => {
        relatedIds.add(String(l.source_device_id));
        relatedIds.add(String(l.destination_device_id));
      });
      stage.querySelectorAll(".elev-dev").forEach((n) => {
        const on = relatedIds.has(String(n.dataset.device));
        n.classList.toggle("on", String(n.dataset.device) === id);
        n.classList.toggle("dim", !on);
      });
      drawLinks(id);
      showPortBox(id);
    });
  });

  function relayoutLinks() {
    fitZoom();
    // wait for transform + layout paint
    requestAnimationFrame(() => {
      drawLinks(null);
      requestAnimationFrame(() => drawLinks(null));
    });
  }

  // stable first paint (tab may still be settling)
  requestAnimationFrame(() => {
    relayoutLinks();
    setTimeout(relayoutLinks, 50);
    setTimeout(relayoutLinks, 200);
  });

  if (viewport && typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => {
      relayoutLinks();
    });
    ro.observe(viewport);
  }

  window.addEventListener("resize", () => {
    relayoutLinks();
  }, { passive: true });
}

/* ========== Drag & Drop Placement ========== */
async function loadDndPlace() {
  // merged into Rack Placement
  showPage("placements");
}


/* ===== Welcome splash + Help guide ===== */
function showWelcomeSplash() {
  const ov = $("#welcomeOverlay");
  if (!ov) {
    showPage("dashboard");
    return;
  }
  ov.classList.remove("hidden");
  const fill = $("#welcomeBarFill");
  const st = $("#welcomeStatusText");
  const toast = $("#welcomeToast");
  const msgs = [
    "Initializing control modules…",
    "Loading inventory graph…",
    "Syncing rack elevation maps…",
    "Warming topology engine…",
    "Access channel ready.",
  ];
  let p = 0;
  if (fill) fill.style.width = "0%";
  if (toast) {
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2800);
  }
  const timer = setInterval(() => {
    p += 4;
    if (fill) fill.style.width = Math.min(100, p) + "%";
    if (st) st.textContent = msgs[Math.min(msgs.length - 1, Math.floor(p / 22))];
    if (p >= 100) {
      clearInterval(timer);
      if (st) st.textContent = "Systems online — ready for operations";
    }
  }, 50);

  const enter = () => {
    ov.classList.add("hidden");
    clearInterval(timer);
    showPage("dashboard");
  };
  $("#welcomeEnter")?.addEventListener("click", enter, { once: true });
  $$(".welcome-pill").forEach((b) => {
    b.onclick = () => {
      ov.classList.add("hidden");
      clearInterval(timer);
      showPage(b.dataset.go || "dashboard");
    };
  });
  // فقط با دکمه ورود / pill ها وارد برنامه می‌شود (بدون auto-enter)
}

const HELP_STEPS = [
  {
    title: "۱) ساخت دارایی‌ها (Assets)",
    body: "از منوی ASSETS ابتدا Datacenter، بعد Racks و سپس Devices را بسازید. این ترتیب پایهٔ کل سیستم است.",
    icon: "🏗️",
    color: "#c084fc",
  },
  {
    title: "۲) چیدمان رک (Rack Placement)",
    body: "رک را از پنل چپ انتخاب کنید. سپس تب Devices را باز کنید و دستگاه‌ها را روی یونیت‌های خالی درگ کنید. با رها کردن، ذخیره می‌شود.",
    icon: "📦",
    color: "#34d399",
  },
  {
    title: "۳) مشاهده جزئیات رک",
    body: "در Rack Detail View می‌توانید elevation دوبعدی رک و در صورت وجود، عکس واقعی رک را ببینید.",
    icon: "🗄️",
    color: "#a78bfa",
  },
  {
    title: "۴) توپولوژی اتصالات",
    body: "Connection Topology ارتباط بین دستگاه‌ها را نشان می‌دهد. با حالت ویرایش می‌توانید لینک جدید بسازید یا حذف کنید (شبیه Packet Tracer).",
    icon: "🔌",
    color: "#f472b6",
  },
];

let helpIndex = 0;

function openHelp(i = 0) {
  helpIndex = i;
  const modal = document.getElementById("helpModal");
  if (!modal) {
    console.error("helpModal not in DOM");
    return;
  }
  modal.classList.remove("hidden");
  modal.style.display = "grid";
  renderHelpStep();
}

function closeHelp() {
  const modal = document.getElementById("helpModal");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.style.display = "";
}

function renderHelpStep() {
  if (helpIndex < 0) helpIndex = 0;
  if (helpIndex >= HELP_STEPS.length) helpIndex = HELP_STEPS.length - 1;
  const s = HELP_STEPS[helpIndex];
  const box = document.getElementById("helpSteps");
  if (!box || !s) return;
  box.innerHTML = `
    <div class="help-step-card" style="--accent:${s.color}">
      <div class="help-step-icon">${s.icon}</div>
      <h3>${s.title}</h3>
      <p>${s.body}</p>
      <div class="help-step-num">${helpIndex + 1} / ${HELP_STEPS.length}</div>
    </div>`;
  const dots = document.getElementById("helpDots");
  if (dots) {
    dots.innerHTML = HELP_STEPS.map((_, i) =>
      `<button type="button" class="help-dot${i === helpIndex ? " on" : ""}" data-hi="${i}" onclick="window.__dcHelpGo && window.__dcHelpGo(${i})"></button>`
    ).join("");
  }
  const next = document.getElementById("helpNext");
  if (next) next.textContent = helpIndex >= HELP_STEPS.length - 1 ? "پایان" : "بعدی";
  const prev = document.getElementById("helpPrev");
  if (prev) prev.disabled = helpIndex <= 0;
}

function formatUptime(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function openAbout() {
  const pop = document.getElementById("userPopover");
  if (pop) pop.classList.add("hidden");
  const modal = document.getElementById("aboutModal");
  if (!modal) {
    console.error("aboutModal not in DOM");
    return;
  }
  modal.classList.remove("hidden");
  modal.style.display = "grid";
}
function closeAbout() {
  const modal = document.getElementById("aboutModal");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.style.display = "";
}

function toggleUserPopover() {
  const pop = $("#userPopover");
  if (!pop) return;
  const willShow = pop.classList.contains("hidden");
  if (willShow) {
    const name = (me && (me.user || me.username)) || "—";
    const role = (me && me.role) || "—";
    const up = formatUptime(Date.now() - (sessionStartedAt || Date.now()));
    const n = $("#userPopName");
    const r = $("#userPopRole");
    const u = $("#userPopUptime");
    if (n) n.textContent = name;
    if (r) r.textContent = role;
    if (u) u.textContent = up;
    pop.classList.remove("hidden");
  } else {
    pop.classList.add("hidden");
  }
}

function wireHelpUi() {
  const fab = document.getElementById("helpFab");
  if (fab && !fab.dataset.wired) {
    fab.dataset.wired = "1";
    fab.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      $("#userPopover")?.classList.add("hidden");
      openHelp(0);
    });
  }
  document.querySelectorAll("[data-help-close]").forEach((el) => {
    if (el.dataset.wired) return;
    el.dataset.wired = "1";
    el.addEventListener("click", (e) => {
      e.preventDefault();
      closeHelp();
    });
  });
  // helpPrev / helpNext: فقط از onclick در HTML (جلوگیری از double-step)

  const userFab = document.getElementById("userFab");
  if (userFab && !userFab.dataset.wired) {
    userFab.dataset.wired = "1";
    userFab.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleUserPopover();
    });
  }
  const aboutFab = document.getElementById("aboutFab");
  if (aboutFab && !aboutFab.dataset.wired) {
    aboutFab.dataset.wired = "1";
    aboutFab.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openAbout();
    });
  }
  document.querySelectorAll("[data-about-close]").forEach((el) => {
    if (el.dataset.wired) return;
    el.dataset.wired = "1";
    el.addEventListener("click", () => closeAbout());
  });
  // close user popover on outside click
  if (!document.body.dataset.userPopWired) {
    document.body.dataset.userPopWired = "1";
    document.addEventListener("click", (e) => {
      const pop = document.getElementById("userPopover");
      const fab = document.getElementById("userFab");
      if (!pop || pop.classList.contains("hidden")) return;
      if (fab && (fab === e.target || fab.contains(e.target))) return;
      if (pop.contains(e.target)) return;
      pop.classList.add("hidden");
    });
  }
}



// global hooks for FAB buttons (inline onclick + programmatic)
window.__dcOpenHelp = function () {
  try {
    const pop = document.getElementById("userPopover");
    if (pop) pop.classList.add("hidden");
    openHelp(0);
  } catch (e) { console.error(e); alert("Help error: " + e.message); }
};
window.__dcToggleUser = function (ev) {
  try {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    toggleUserPopover();
  } catch (e) { console.error(e); }
};
window.__dcOpenAbout = function () {
  try { openAbout(); } catch (e) { console.error(e); alert("About error: " + e.message); }
};
window.__dcCloseAbout = function () { try { closeAbout(); } catch (e) {} };
window.__dcCloseHelp = function () { try { closeHelp(); } catch (e) {} };

window.__dcHelpPrev = function (ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (window.__helpNavLock) return;
  window.__helpNavLock = true;
  try {
    helpIndex = Math.max(0, (helpIndex || 0) - 1);
    renderHelpStep();
  } finally {
    setTimeout(() => { window.__helpNavLock = false; }, 50);
  }
};
window.__dcHelpNext = function (ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (window.__helpNavLock) return;
  window.__helpNavLock = true;
  try {
    if ((helpIndex || 0) >= HELP_STEPS.length - 1) {
      closeHelp();
      return;
    }
    helpIndex = (helpIndex || 0) + 1;
    renderHelpStep();
  } finally {
    setTimeout(() => { window.__helpNavLock = false; }, 50);
  }
};
window.__dcHelpGo = function (i, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (window.__helpNavLock) return;
  window.__helpNavLock = true;
  try {
    helpIndex = +i;
    renderHelpStep();
  } finally {
    setTimeout(() => { window.__helpNavLock = false; }, 50);
  }
};
window.__dcToggleFab = function () {
  const stack = document.getElementById("fabStack");
  if (!stack) {
    console.warn("fabStack missing");
    return;
  }
  const closed = stack.classList.toggle("is-closed");
  // also support legacy class
  stack.classList.toggle("collapsed", closed);
  const items = document.getElementById("fabItems");
  if (items) {
    if (closed) {
      items.style.maxHeight = "0px";
      items.style.opacity = "0";
      items.style.pointerEvents = "none";
    } else {
      items.style.maxHeight = "240px";
      items.style.opacity = "1";
      items.style.pointerEvents = "auto";
    }
  }
};


/* Events */
(function wireCoreUi() {
  const loginBtn = document.getElementById("loginBtn");
  if (loginBtn) loginBtn.onclick = doLogin;
  const loginPass = document.getElementById("loginPass");
  if (loginPass) loginPass.addEventListener("keydown", (e) => e.key === "Enter" && doLogin());
  const setupEnterBtn = document.getElementById("setupEnterBtn");
  if (setupEnterBtn) setupEnterBtn.onclick = () => enterSetupMode();
  const setupGo = document.getElementById("setupGoUsers");
  if (setupGo) {
    setupGo.onclick = () => {
      showSetupModal(false);
      showPage("users");
      // باز کردن گروه ADMIN در سایدبار
      document.querySelectorAll("#nav .nav-group").forEach((g) => {
        if (g.querySelector('[data-page="users"]')) g.classList.remove("collapsed");
      });
    };
  }
  const setupLater = document.getElementById("setupLater");
  if (setupLater) setupLater.onclick = () => showSetupModal(false);
  const logoutBtn = document.getElementById("logoutBtn");
  if (logoutBtn) logoutBtn.onclick = () => logout(true);
  document.querySelectorAll("#nav button[data-page]").forEach((b) => {
    b.addEventListener("click", () => showPage(b.dataset.page));
  });
  document.querySelectorAll("#nav .nav-sec-toggle").forEach((sec) => {
    sec.addEventListener("click", () => {
      const group = sec.closest(".nav-group");
      if (group) group.classList.toggle("collapsed");
    });
  });
  try { wireHelpUi(); } catch (e) { console.error("wireHelpUi", e); }
  // document-level delegation backup
  document.addEventListener("click", (e) => {
    const t = e.target.closest("#aiFab, #helpFab, #userFab, #aboutFab, #fabToggle, [data-help-close], [data-about-close], [data-ai-close]");
    if (!t) return;
    // helpPrev/Next/dots فقط از onclick خودشان — جلوگیری از دوباره‌اجرایی
    if (t.id === "fabToggle") { e.preventDefault(); window.__dcToggleFab(); }
    else if (t.id === "aiFab") { e.preventDefault(); window.__dcOpenAIChat(); }
    else if (t.id === "helpFab") { e.preventDefault(); window.__dcOpenHelp(); }
    else if (t.id === "userFab") { e.preventDefault(); window.__dcToggleUser(e); }
    else if (t.id === "aboutFab") { e.preventDefault(); window.__dcOpenAbout(); }
    else if (t.hasAttribute && t.hasAttribute("data-ai-close")) { e.preventDefault(); window.__dcCloseAIChat(); }
    else if (t.hasAttribute("data-help-close")) { e.preventDefault(); window.__dcCloseHelp(); }
    else if (t.hasAttribute("data-about-close")) { e.preventDefault(); window.__dcCloseAbout(); }
  });
})();

(async function init() {
  if (!token) {
    await checkSetupAndInitLogin();
    return;
  }
  try {
    me = await api("/api/me");
    $("#loginView").classList.add("hidden");
    $("#appView").classList.remove("hidden");
    $("#userChip").textContent = me.setup_mode
      ? `راه‌اندازی · admin`
      : `${me.user} · ${me.role}`;
    showWelcomeSplash();
    if (me.needs_setup || me.setup_mode) {
      setTimeout(() => showSetupModal(true), 500);
    }
  } catch {
    logout(false);
    await checkSetupAndInitLogin();
  }
})();


/* direct fab toggle bind — last resort */
(function fabToggleDirectBind() {
  function bind() {
    const btn = document.getElementById("fabToggle");
    if (!btn || btn.dataset.direct) return;
    btn.dataset.direct = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (window.__dcToggleFab) window.__dcToggleFab();
    }, true);
    // also bind children
    document.getElementById("helpFab")?.addEventListener("click", function (e) {
      e.stopPropagation();
      window.__dcOpenHelp && window.__dcOpenHelp();
    }, true);
    document.getElementById("userFab")?.addEventListener("click", function (e) {
      e.stopPropagation();
      window.__dcToggleUser && window.__dcToggleUser(e);
    }, true);
    document.getElementById("aboutFab")?.addEventListener("click", function (e) {
      e.stopPropagation();
      window.__dcOpenAbout && window.__dcOpenAbout();
    }, true);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();


/* ===== AI Chat FAB + Popup (AI mode + Guided OpManager-like mode) ===== */
(function aiChatModule() {
  let aiHistory = [];
  let aiBusy = false;
  let currentMode = "ai";
  let aiAvailable = null;
  let guidedStep = "menu";
  let guidedCategory = null;
  let guidedState = { rackId: null, deviceId: null };
  let cache = { racks: null, devices: null, connections: null, datacenters: null };

  var WELCOME_AI =
    'سلام! 👋 من <b>دستیار هوشمند</b> هستم.<br>' +
    'از روی فایل دستگاه‌ها و اتصالات جواب می‌دهم. مثلاً:<br>' +
    '• دستگاه <code>1004</code> به چی وصله؟<br>' +
    '• دستگاه‌های رک <code>MOKHABERAT</code>؟<br>' +
    '• لیست رک‌ها رو بگو';

  function el(id) { return document.getElementById(id); }

  function authHeaders(json) {
    var h = {};
    if (json) h["Content-Type"] = "application/json";
    var token = localStorage.getItem("dc_token") || localStorage.getItem("token") || "";
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
  }

  async function apiGet(path) {
    var res = await fetch(path, { headers: authHeaders() });
    if (res.status === 401) throw new Error("UNAUTHORIZED");
    if (!res.ok) {
      var t = await res.text().catch(function () { return ""; });
      throw new Error(t || ("HTTP " + res.status));
    }
    return res.json();
  }

  async function checkAIHealth() {
    try {
      var data = await apiGet("/api/ai/health");
      aiAvailable = !!(data && data.available !== false && data.status !== "unavailable");
      return aiAvailable;
    } catch (e) {
      aiAvailable = false;
      return false;
    }
  }

  function setModeUI(mode) {
    currentMode = mode;
    var btnAI = el("aiModeAI");
    var btnG = el("aiModeGuided");
    if (btnAI) btnAI.classList.toggle("active", mode === "ai");
    if (btnG) btnG.classList.toggle("active", mode === "guided");

    var inputWrap = el("aiInputWrap");
    var footer = el("aiGuidedFooter");
    if (inputWrap) inputWrap.classList.toggle("hidden", mode !== "ai");
    if (footer) footer.classList.toggle("hidden", mode !== "guided");

    var title = el("aiChatTitle");
    var sub = el("aiChatSub");
    var avatar = el("aiChatAvatar");
    if (mode === "ai") {
      if (title) title.textContent = "دستیار هوشمند";
      if (sub) sub.innerHTML = "اتصالات و دستگاه‌های دیتاسنتر" +
        (aiAvailable === false ? ' <span class="ai-status-badge off"><i></i>AI آفلاین</span>' :
          aiAvailable === true ? ' <span class="ai-status-badge on"><i></i>آنلاین</span>' : "");
      if (avatar) avatar.textContent = "🤖";
    } else {
      if (title) title.textContent = "راهنمای دیتاسنتر";
      if (sub) sub.innerHTML = "انتخاب مرحله‌به‌مرحله (شبیه OpManager)";
      if (avatar) avatar.textContent = "📋";
    }
  }

  function clearMessages() {
    var box = el("aiChatMessages");
    if (box) box.innerHTML = "";
  }

  function appendMsg(role, html) {
    var box = el("aiChatMessages");
    if (!box) return null;
    var row = document.createElement("div");
    row.className = "ai-msg " + (role === "user" ? "user" : "bot");
    row.innerHTML = '<div class="ai-bubble">' + html + "</div>";
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    return row;
  }

  function showTyping() {
    var box = el("aiChatMessages");
    if (!box) return;
    var row = document.createElement("div");
    row.id = "aiTyping";
    row.className = "ai-msg bot";
    row.innerHTML = '<div class="ai-bubble"><div class="ai-typing"><i></i><i></i><i></i></div></div>';
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }
  function hideTyping() {
    var t = el("aiTyping");
    if (t) t.remove();
  }

  function simpleMd(text) {
    if (!text) return "";
    var t = String(text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    t = t.replace(/```[\s\S]*?```/g, function (m) {
      var body = m.replace(/^```\w*\n?/, "").replace(/```$/, "");
      return "<pre style='direction:ltr;text-align:left;background:#0f172a;padding:10px;border-radius:8px;overflow:auto;font-size:12px'>" + body + "</pre>";
    });
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/^### (.+)$/gm, "<div style='font-weight:700;margin:8px 0 4px'>$1</div>");
    t = t.replace(/^## (.+)$/gm, "<div style='font-weight:800;margin:8px 0 4px'>$1</div>");
    t = t.replace(/^- (.+)$/gm, "• $1");
    t = t.replace(/\n/g, "<br>");
    return t;
  }

  async function loadCache() {
    if (!cache.racks) {
      try { cache.racks = await apiGet("/api/racks"); } catch (e) { cache.racks = []; }
    }
    if (!cache.devices) {
      try { cache.devices = await apiGet("/api/devices"); } catch (e) { cache.devices = []; }
    }
    if (!cache.connections) {
      try { cache.connections = await apiGet("/api/connections"); } catch (e) { cache.connections = []; }
    }
    if (!cache.datacenters) {
      try {
        var d = await apiGet("/api/datacenters");
        cache.datacenters = d.datacenters || [];
      } catch (e) { cache.datacenters = []; }
    }
  }

  function deviceLabel(d) {
    if (!d) return "—";
    var name = d.device_name || d.model || ("Device-" + d.device_id);
    return name + " (#" + d.device_id + ")";
  }

  function rackLabel(r) {
    if (!r) return "—";
    return (r.rack_name || ("Rack-" + r.rack_id)) + " (#" + r.rack_id + ")";
  }

  function renderMenu() {
    guidedStep = "menu";
    guidedCategory = null;
    guidedState = { rackId: null, deviceId: null };
    clearMessages();
    var html =
      'سلام! 👋 من <b>راهنمای دیتاسنتر</b> هستم.<br>' +
      'چی می‌خوای بدونی؟ یکی از گزینه‌ها رو انتخاب کن:' +
      '<div class="ai-guide-cards">' +
      '<button type="button" class="ai-guide-card" data-cat="dc">' +
      '<span class="gc-icon">📊</span><span class="gc-label">اطلاعات دیتاسنتر</span>' +
      '<span class="gc-desc">لیست و خلاصه DCها</span></button>' +
      '<button type="button" class="ai-guide-card" data-cat="rack">' +
      '<span class="gc-icon">🗄️</span><span class="gc-label">اطلاعات رک</span>' +
      '<span class="gc-desc">دستگاه‌های داخل رک</span></button>' +
      '<button type="button" class="ai-guide-card" data-cat="device">' +
      '<span class="gc-icon">💻</span><span class="gc-label">اطلاعات دستگاه</span>' +
      '<span class="gc-desc">جزئیات یک تجهیز</span></button>' +
      '<button type="button" class="ai-guide-card" data-cat="conn">' +
      '<span class="gc-icon">🔗</span><span class="gc-label">اطلاعات اتصالات</span>' +
      '<span class="gc-desc">لینک‌های یک دستگاه</span></button>' +
      '</div>';
    appendMsg("bot", html);
    setGuidedHint("یکی از چهار گزینه را انتخاب کنید");
    var box = el("aiChatMessages");
    if (box) {
      box.querySelectorAll("[data-cat]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          startCategory(btn.getAttribute("data-cat"));
        });
      });
    }
  }

  function setGuidedHint(text) {
    var h = el("aiGuidedHint");
    if (h) h.textContent = text || "";
  }

  function selectHTML(id, label, options, placeholder) {
    var opts = '<option value="">' + (placeholder || "انتخاب کنید...") + "</option>";
    (options || []).forEach(function (o) {
      opts += '<option value="' + o.value + '">' + String(o.label).replace(/</g, "&lt;") + "</option>";
    });
    return (
      '<div class="ai-guide-select-wrap">' +
      '<label class="ai-guide-select-label">' + label + "</label>" +
      '<select class="ai-guide-select" id="' + id + '">' + opts + "</select>" +
      "</div>"
    );
  }

  async function startCategory(cat) {
    guidedCategory = cat;
    await loadCache();
    clearMessages();

    if (cat === "dc") {
      guidedStep = "result";
      appendMsg("user", "📊 اطلاعات دیتاسنتر");
      var dcs = cache.datacenters || [];
      var racks = cache.racks || [];
      var devices = cache.devices || [];
      var html = "<b>دیتاسنترها</b><div class='ai-guide-result'>";
      if (!dcs.length) {
        html += "<div class='ai-guide-empty'>هیچ دیتاسنتری ثبت نشده.</div>";
      } else {
        dcs.forEach(function (dc) {
          var id = dc.datacenter_id;
          var rc = racks.filter(function (r) { return String(r.datacenter_id) === String(id); }).length;
          var dcDevs = devices.filter(function (d) { return String(d.datacenter_id) === String(id); }).length;
          html +=
            "<div class='ai-guide-result-item'>" +
            "<strong>" + (dc.datacenter_name || ("DC-" + id)) + "</strong>" +
            " <span class='tag'>#" + id + "</span><br>" +
            "مکان: " + (dc.location || "—") + "<br>" +
            "تعداد رک: <b>" + rc + "</b> · تعداد دستگاه: <b>" + dcDevs + "</b>" +
            "</div>";
        });
      }
      html += "</div>";
      appendMsg("bot", html);
      setGuidedHint("برای شروع مجدد روی 🗑️ بزنید");
      return;
    }

    if (cat === "rack") {
      guidedStep = "rack";
      appendMsg("user", "🗄️ اطلاعات رک");
      var opts = (cache.racks || []).map(function (r) {
        return { value: r.rack_id, label: rackLabel(r) };
      });
      appendMsg("bot", "کدام رک را می‌خواهید؟" + selectHTML("gSelRack", "انتخاب رک", opts, "رک را انتخاب کنید..."));
      setGuidedHint("رک مورد نظر را از لیست انتخاب کنید");
      bindSelect("gSelRack", onRackSelected);
      return;
    }

    if (cat === "device") {
      guidedStep = "rack";
      appendMsg("user", "💻 اطلاعات دستگاه");
      var opts = (cache.racks || []).map(function (r) {
        return { value: r.rack_id, label: rackLabel(r) };
      });
      opts.unshift({ value: "__all__", label: "همه رک‌ها" });
      appendMsg("bot", "اول رک را مشخص کنید (یا همه):" + selectHTML("gSelRack", "انتخاب رک", opts, "رک را انتخاب کنید..."));
      setGuidedHint("رک را انتخاب کنید تا لیست دستگاه‌ها بیاید");
      bindSelect("gSelRack", onRackSelectedForDevice);
      return;
    }

    if (cat === "conn") {
      guidedStep = "rack";
      appendMsg("user", "🔗 اطلاعات اتصالات");
      var opts = (cache.racks || []).map(function (r) {
        return { value: r.rack_id, label: rackLabel(r) };
      });
      opts.unshift({ value: "__all__", label: "همه رک‌ها" });
      appendMsg("bot", "رک مربوط به دستگاه را انتخاب کنید:" + selectHTML("gSelRack", "انتخاب رک", opts, "رک را انتخاب کنید..."));
      setGuidedHint("رک → دستگاه → نمایش اتصالات");
      bindSelect("gSelRack", onRackSelectedForConn);
      return;
    }
  }

  function bindSelect(id, handler) {
    setTimeout(function () {
      var s = el(id);
      if (s) {
        s.addEventListener("change", function () {
          if (s.value) handler(s.value);
        });
      }
    }, 30);
  }

  async function onRackSelected(rackId) {
    guidedState.rackId = rackId;
    guidedStep = "result";
    appendMsg("user", "رک: " + rackId);
    showTyping();
    try {
      var data = await apiGet("/api/guided/rack-devices/" + encodeURIComponent(rackId));
      hideTyping();
      if (data.error) {
        appendMsg("bot", data.error);
        return;
      }
      var rack = data.rack || {};
      var devs = data.devices || [];
      var html =
        "<b>رک " + (rack.rack_name || rackId) + "</b>" +
        " · " + (data.total_devices || devs.length) + " دستگاه<br>" +
        "<div class='ai-guide-result'>";
      if (!devs.length) {
        html += "<div class='ai-guide-empty'>دستگاهی در این رک نیست.</div>";
      } else {
        devs.forEach(function (d) {
          html +=
            "<div class='ai-guide-result-item'>" +
            "<strong>" + (d.device_name || "Device") + "</strong>" +
            " <span class='tag'>#" + d.device_id + "</span><br>" +
            (d.model ? "مدل: " + d.model + "<br>" : "") +
            "یونیت: " + (d.start_unit || "?") + "–" + (d.end_unit || "?") +
            (d.description ? "<br>" + d.description : "") +
            "</div>";
        });
      }
      html += "</div>";
      appendMsg("bot", html);
      setGuidedHint("برای شروع مجدد 🗑️ را بزنید");
    } catch (e) {
      hideTyping();
      appendMsg("bot", "خطا: " + (e.message || e));
    }
  }

  async function devicesForRack(rackId) {
    await loadCache();
    if (rackId === "__all__") {
      return (cache.devices || []).map(function (d) {
        return { value: d.device_id, label: deviceLabel(d) };
      });
    }
    try {
      var data = await apiGet("/api/guided/rack-devices/" + encodeURIComponent(rackId));
      var opts = (data.devices || []).map(function (d) {
        return { value: d.device_id, label: (d.device_name || "Device") + " (#" + d.device_id + ")" };
      });
      if (opts.length) return opts;
    } catch (e) { /* ignore */ }
    return (cache.devices || []).map(function (d) {
      return { value: d.device_id, label: deviceLabel(d) };
    });
  }

  async function onRackSelectedForDevice(rackId) {
    guidedState.rackId = rackId;
    guidedStep = "device";
    appendMsg("user", rackId === "__all__" ? "همه رک‌ها" : ("رک: " + rackId));
    showTyping();
    var opts = await devicesForRack(rackId);
    hideTyping();
    if (!opts.length) {
      appendMsg("bot", "دستگاهی پیدا نشد.");
      return;
    }
    appendMsg("bot", "کدام دستگاه؟" + selectHTML("gSelDev", "انتخاب دستگاه", opts, "دستگاه را انتخاب کنید..."));
    setGuidedHint("دستگاه مورد نظر را انتخاب کنید");
    bindSelect("gSelDev", onDeviceInfoSelected);
  }

  async function onDeviceInfoSelected(deviceId) {
    guidedState.deviceId = deviceId;
    guidedStep = "result";
    appendMsg("user", "دستگاه #" + deviceId);
    await loadCache();
    var d = (cache.devices || []).find(function (x) { return String(x.device_id) === String(deviceId); });
    if (!d) {
      appendMsg("bot", "دستگاه پیدا نشد.");
      return;
    }
    var rackName = "—";
    try {
      var placements = await apiGet("/api/placements");
      var p = (placements || []).find(function (x) { return String(x.device_id) === String(deviceId); });
      if (p) {
        var r = (cache.racks || []).find(function (x) { return String(x.rack_id) === String(p.rack_id); });
        rackName = r ? rackLabel(r) : ("Rack #" + p.rack_id);
        rackName += " (U" + (p.start_unit || "?") + "–" + (p.end_unit || "?") + ")";
      }
    } catch (e) { /* ignore */ }

    var html =
      "<div class='ai-guide-result'><div class='ai-guide-result-item'>" +
      "<strong>" + (d.device_name || "Device") + "</strong> <span class='tag'>#" + d.device_id + "</span><br>" +
      "مدل: <b>" + (d.model || "—") + "</b><br>" +
      "توضیح: " + (d.description || "—") + "<br>" +
      "یونیت مصرفی: " + (d.units_used || "—") + "<br>" +
      "پورت شبکه: " + (d.network_ports || 0) +
      " · برق: " + (d.power_ports || 0) +
      " · فیبر: " + (d.fiber_ports || 0) + "<br>" +
      "دیتاسنتر: #" + (d.datacenter_id || "—") + "<br>" +
      "محل قرارگیری: <b>" + rackName + "</b>" +
      "</div></div>";
    appendMsg("bot", html);
    setGuidedHint("برای شروع مجدد 🗑️ را بزنید");
  }

  async function onRackSelectedForConn(rackId) {
    guidedState.rackId = rackId;
    guidedStep = "device";
    appendMsg("user", rackId === "__all__" ? "همه رک‌ها" : ("رک: " + rackId));
    showTyping();
    var opts = await devicesForRack(rackId);
    hideTyping();
    if (!opts.length) {
      appendMsg("bot", "دستگاهی پیدا نشد.");
      return;
    }
    appendMsg("bot", "دستگاه مورد نظر برای مشاهده اتصالات:" + selectHTML("gSelDev", "انتخاب دستگاه", opts, "دستگاه را انتخاب کنید..."));
    setGuidedHint("دستگاه را انتخاب کنید تا اتصالاتش نمایش داده شود");
    bindSelect("gSelDev", onConnDeviceSelected);
  }

  async function onConnDeviceSelected(deviceId) {
    guidedState.deviceId = deviceId;
    guidedStep = "result";
    appendMsg("user", "اتصالات دستگاه #" + deviceId);
    showTyping();
    try {
      var data = await apiGet("/api/guided/device-connections/" + encodeURIComponent(deviceId));
      hideTyping();
      if (data.error) {
        appendMsg("bot", data.error);
        return;
      }
      var dev = data.device || {};
      var links = data.connections || [];
      var html =
        "<b>اتصالات " + (dev.device_name || ("#" + deviceId)) + "</b>" +
        " · تعداد: " + (data.total_connections || links.length) +
        "<div class='ai-guide-result'>";
      if (!links.length) {
        html += "<div class='ai-guide-empty'>هیچ اتصالی برای این دستگاه ثبت نشده.</div>";
      } else {
        links.forEach(function (l) {
          var typ = (l.type || "").toLowerCase();
          var tagClass = typ.indexOf("fiber") >= 0 ? "fiber" : "copper";
          html +=
            "<div class='ai-guide-result-item'>" +
            "<strong>" + (l.from_device || "?") + "</strong>" +
            " <span class='tag'>" + (l.from_port || "") + "</span>" +
            " ⟷ " +
            "<strong>" + (l.to_device || "?") + "</strong>" +
            " <span class='tag'>" + (l.to_port || "") + "</span><br>" +
            "رک: " + (l.from_rack || "?") + " → " + (l.to_rack || "?") +
            " · <span class='tag " + tagClass + "'>" + (l.type || "—") + "</span>" +
            " · " + (l.status || "") +
            (l.notes ? "<br>یادداشت: " + l.notes : "") +
            "</div>";
        });
      }
      html += "</div>";
      appendMsg("bot", html);
      setGuidedHint("برای شروع مجدد 🗑️ را بزنید");
    } catch (e) {
      hideTyping();
      appendMsg("bot", "خطا: " + (e.message || e));
    }
  }

  function guidedBack() {
    renderMenu();
  }

  async function sendAI() {
    var input = el("aiChatInput");
    var btn = el("aiChatSend");
    if (!input || aiBusy || currentMode !== "ai") return;
    var msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    input.style.height = "auto";
    aiBusy = true;
    if (btn) btn.disabled = true;

    appendMsg("user", msg.replace(/</g, "&lt;"));
    aiHistory.push({ role: "user", content: msg });
    showTyping();

    try {
      var res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({ message: msg, history: aiHistory.slice(0, -1).slice(-10) }),
      });
      hideTyping();
      if (res.status === 401) {
        appendMsg("bot", "برای استفاده از دستیار ابتدا وارد حساب شوید.");
      } else {
        var data = await res.json();
        var answer = data.answer || data.detail || JSON.stringify(data);
        appendMsg("bot", simpleMd(answer));
        aiHistory.push({ role: "assistant", content: answer });
      }
    } catch (e) {
      hideTyping();
      appendMsg("bot", "خطا در ارتباط: " + (e.message || e) +
        "<br><br>اگر مدل AI در دسترس نیست، از تب <b>📋 راهنما</b> استفاده کنید.");
    } finally {
      aiBusy = false;
      if (btn) btn.disabled = false;
      if (input) input.focus();
    }
  }

  function resetCurrentMode() {
    if (currentMode === "ai") {
      aiHistory = [];
      clearMessages();
      appendMsg("bot", WELCOME_AI);
      var input = el("aiChatInput");
      if (input) {
        input.value = "";
        input.style.height = "auto";
        input.focus();
      }
    } else {
      cache = { racks: null, devices: null, connections: null, datacenters: null };
      renderMenu();
    }
  }

  async function applyMode(mode, force) {
    if (mode === "ai" && aiAvailable === false && !force) {
      mode = "guided";
    }
    setModeUI(mode);
    resetCurrentMode();
  }

  window.__dcOpenAIChat = async function () {
    var m = el("aiChatModal");
    if (!m) return;
    m.classList.remove("hidden");

    if (aiAvailable === null) {
      showTyping();
      await checkAIHealth();
      hideTyping();
    }

    var preferred = aiAvailable === false ? "guided" : "ai";
    if (!m.dataset.openedOnce) {
      m.dataset.openedOnce = "1";
      await applyMode(preferred);
    } else {
      setModeUI(currentMode);
      if (currentMode === "ai") {
        var box = el("aiChatMessages");
        if (box && !box.children.length) appendMsg("bot", WELCOME_AI);
        setTimeout(function () { el("aiChatInput") && el("aiChatInput").focus(); }, 80);
      } else if (guidedStep === "menu") {
        renderMenu();
      }
    }

    var btnAI = el("aiModeAI");
    if (btnAI) {
      if (aiAvailable === false) {
        btnAI.title = "AI در دسترس نیست — روی راهنما بمانید یا دوباره امتحان کنید";
      } else {
        btnAI.title = "حالت هوش مصنوعی";
      }
    }
  };

  window.__dcCloseAIChat = function () {
    var m = el("aiChatModal");
    if (m) m.classList.add("hidden");
  };

  function wire() {
    var fab = el("aiFab");
    if (fab && !fab.dataset.wired) {
      fab.dataset.wired = "1";
      fab.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        window.__dcOpenAIChat();
      }, true);
    }
    document.querySelectorAll("[data-ai-close]").forEach(function (n) {
      if (!n.dataset.wired) {
        n.dataset.wired = "1";
        n.addEventListener("click", function (e) {
          e.preventDefault();
          window.__dcCloseAIChat();
        });
      }
    });
    var sendBtn = el("aiChatSend");
    if (sendBtn && !sendBtn.dataset.wired) {
      sendBtn.dataset.wired = "1";
      sendBtn.addEventListener("click", sendAI);
    }
    var clearBtn = el("aiChatClear");
    if (clearBtn && !clearBtn.dataset.wired) {
      clearBtn.dataset.wired = "1";
      clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        resetCurrentMode();
      });
    }
    var input = el("aiChatInput");
    if (input && !input.dataset.wired) {
      input.dataset.wired = "1";
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendAI();
        }
      });
      input.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 100) + "px";
      });
    }
    var backBtn = el("aiGuidedBack");
    if (backBtn && !backBtn.dataset.wired) {
      backBtn.dataset.wired = "1";
      backBtn.addEventListener("click", function (e) {
        e.preventDefault();
        guidedBack();
      });
    }
    var modeAI = el("aiModeAI");
    var modeG = el("aiModeGuided");
    if (modeAI && !modeAI.dataset.wired) {
      modeAI.dataset.wired = "1";
      modeAI.addEventListener("click", async function () {
        await checkAIHealth();
        if (aiAvailable === false) {
          setModeUI("ai");
          clearMessages();
          appendMsg("bot", "⚠️ سرویس هوش مصنوعی در دسترس نیست (Ollama/مدل).<br>از تب <b>📋 راهنما</b> استفاده کنید یا بعداً دوباره امتحان کنید.");
          var inputWrap = el("aiInputWrap");
          if (inputWrap) inputWrap.classList.remove("hidden");
          var footer = el("aiGuidedFooter");
          if (footer) footer.classList.add("hidden");
          return;
        }
        await applyMode("ai", true);
      });
    }
    if (modeG && !modeG.dataset.wired) {
      modeG.dataset.wired = "1";
      modeG.addEventListener("click", function () {
        applyMode("guided", true);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
  setTimeout(wire, 400);
  setTimeout(wire, 1200);
})();
