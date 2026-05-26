const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmtTs(ts) {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function relTs(ts) {
  if (!ts) return "never";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function flash(el, text, kind = "ok", ms = 4000) {
  el.textContent = text;
  el.className = `msg ${kind}`;
  if (ms) setTimeout(() => { if (el.textContent === text) { el.textContent = ""; el.className = "msg"; } }, ms);
}

async function loadStatus() {
  const s = await api("/api/status");
  const pill = $("#status-pill");
  if (!s.ready) {
    pill.textContent = "setup required";
    pill.className = "pill warn";
  } else if (s.last_scan && s.last_scan.error) {
    pill.textContent = "scan error";
    pill.className = "pill err";
  } else {
    pill.textContent = "running";
    pill.className = "pill ok";
  }

  $("#stat-channels").textContent = s.channels.enabled;
  $("#stat-channels-sub").textContent =
    s.channels.total === s.channels.enabled
      ? `${s.channels.total} total`
      : `${s.channels.total} total, ${s.channels.total - s.channels.enabled} disabled`;
  $("#stat-queued").textContent = s.videos.queued;

  if (s.last_scan && s.last_scan.started_at) {
    $("#stat-last").textContent = relTs(s.last_scan.started_at);
    const parts = [];
    if (s.last_scan.queued != null) parts.push(`${s.last_scan.queued} queued`);
    if (s.last_scan.error) parts.push(`error: ${s.last_scan.error}`);
    $("#stat-last-sub").textContent = parts.join(" · ") || fmtTs(s.last_scan.started_at);
  } else {
    $("#stat-last").textContent = "never";
    $("#stat-last-sub").textContent = "";
  }

  const interval = s.settings.scan_interval_minutes;
  if (s.last_scan && s.last_scan.started_at) {
    const next = s.last_scan.started_at + interval * 60;
    const diff = next - Math.floor(Date.now() / 1000);
    if (diff > 0) {
      const m = Math.floor(diff / 60);
      $("#stat-next").textContent = m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    } else {
      $("#stat-next").textContent = "due";
    }
  } else {
    $("#stat-next").textContent = `${interval}m`;
  }

  fillSettingsForm(s.settings);
}

function fillSettingsForm(settings) {
  const f = $("#settings-form");
  // don't clobber a value the user is actively editing
  if (document.activeElement && f.contains(document.activeElement)) return;
  f.elements.ta_url.value = settings.ta_url || "";
  f.elements.scan_interval_minutes.value = settings.scan_interval_minutes;
  f.elements.max_age_days.value = settings.max_age_days;
  f.elements.autostart.checked = !!settings.autostart;
  f.elements.source.value = settings.source;
  f.elements.verify_ssl.checked = settings.verify_ssl !== false;
  f.elements.skip_shorts.checked = settings.skip_shorts !== false;
  f.elements.skip_streams.checked = !!settings.skip_streams;
  $("#token-status").textContent = settings.ta_token_set
    ? "Token is set. Enter a new value to replace it; leave blank to keep."
    : "No token saved yet.";
  markFormClean();
}

function snapshotForm() {
  const f = $("#settings-form");
  return JSON.stringify({
    ta_url: f.elements.ta_url.value,
    token_set: !!f.elements.ta_token.value,
    interval: f.elements.scan_interval_minutes.value,
    max_age: f.elements.max_age_days.value,
    autostart: f.elements.autostart.checked,
    source: f.elements.source.value,
    verify: f.elements.verify_ssl.checked,
    skip_shorts: f.elements.skip_shorts.checked,
    skip_streams: f.elements.skip_streams.checked,
  });
}
let cleanSnapshot = "";
function markFormClean() {
  cleanSnapshot = snapshotForm();
  $("#dirty-pill").hidden = true;
}
function checkDirty() {
  $("#dirty-pill").hidden = snapshotForm() === cleanSnapshot;
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const payload = {
    ta_url: f.elements.ta_url.value.trim(),
    scan_interval_minutes: parseInt(f.elements.scan_interval_minutes.value || "60", 10),
    max_age_days: parseInt(f.elements.max_age_days.value || "7", 10),
    autostart: f.elements.autostart.checked,
    source: f.elements.source.value,
    verify_ssl: f.elements.verify_ssl.checked,
    skip_shorts: f.elements.skip_shorts.checked,
    skip_streams: f.elements.skip_streams.checked,
  };
  const tokenInput = f.elements.ta_token.value;
  if (tokenInput) payload.ta_token = tokenInput;

  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    f.elements.ta_token.value = "";
    flash($("#form-msg"), "Saved.", "ok");
    markFormClean();
    loadStatus();
  } catch (e) {
    flash($("#form-msg"), e.message, "err", 8000);
  }
}

async function testConnection() {
  const f = $("#settings-form");
  const body = {
    ta_url: f.elements.ta_url.value.trim(),
    verify_ssl: f.elements.verify_ssl.checked,
    scan_interval_minutes: parseInt(f.elements.scan_interval_minutes.value || "60", 10),
    max_age_days: parseInt(f.elements.max_age_days.value || "7", 10),
    autostart: f.elements.autostart.checked,
    source: f.elements.source.value,
    skip_shorts: f.elements.skip_shorts.checked,
    skip_streams: f.elements.skip_streams.checked,
  };
  if (f.elements.ta_token.value) body.ta_token = f.elements.ta_token.value;
  try {
    const r = await api("/api/settings/test", { method: "POST", body: JSON.stringify(body) });
    if (r.ok) {
      flash($("#form-msg"), "Connection OK.", "ok");
    } else {
      flash($("#form-msg"), r.error || "Ping failed.", "err", 12000);
    }
  } catch (e) {
    flash($("#form-msg"), e.message, "err", 12000);
  }
}

async function loadChannels() {
  const list = await api("/api/channels");
  const tbody = $("#channels-table tbody");
  tbody.innerHTML = "";
  $("#ch-count").textContent = list.length ? `(${list.length})` : "";
  $("#channels-empty").hidden = list.length > 0;
  for (const ch of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(ch.name || "(unknown)")}</td>
      <td>${ch.handle ? escapeHtml(ch.handle) : '<span class="muted">–</span>'}</td>
      <td class="id">${ch.channel_id}</td>
      <td>
        <label class="toggle">
          <input type="checkbox" ${ch.enabled ? "checked" : ""} data-toggle="${ch.channel_id}" />
          <span class="slider"></span>
        </label>
      </td>
      <td><button class="row-del" data-del="${ch.channel_id}">Remove</button></td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => removeChannel(b.dataset.del))
  );
  tbody.querySelectorAll("[data-toggle]").forEach((cb) =>
    cb.addEventListener("change", () => toggleChannel(cb.dataset.toggle, cb.checked))
  );
}

async function addChannel(ev) {
  ev.preventDefault();
  const input = $("#add-channel-input");
  const query = input.value.trim();
  if (!query) return;
  const btn = ev.submitter;
  btn.disabled = true;
  try {
    const r = await api("/api/channels", { method: "POST", body: JSON.stringify({ query }) });
    flash($("#add-msg"), r.added ? `Added ${r.name || r.channel_id}.` : `Already tracked: ${r.name || r.channel_id}.`, "ok");
    input.value = "";
    loadChannels();
    loadStatus();
  } catch (e) {
    flash($("#add-msg"), e.message, "err", 8000);
  } finally {
    btn.disabled = false;
  }
}

async function removeChannel(id) {
  if (!confirm("Remove this channel?")) return;
  await api(`/api/channels/${id}`, { method: "DELETE" });
  loadChannels();
  loadStatus();
}

async function toggleChannel(id, enabled) {
  await api(`/api/channels/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
  loadStatus();
}

async function importOpml(ev) {
  const file = ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/channels/import-opml", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const out = await r.json();
    flash($("#import-msg"), `Parsed ${out.parsed}, added ${out.added}.`, "ok");
    loadChannels();
    loadStatus();
  } catch (e) {
    flash($("#import-msg"), e.message, "err", 8000);
  } finally {
    ev.target.value = "";
  }
}

async function syncFromTa() {
  const btn = $("#sync-ta");
  btn.disabled = true;
  try {
    const r = await api("/api/channels/sync-from-ta", { method: "POST" });
    flash($("#import-msg"), `Fetched ${r.fetched}, added ${r.added}.`, "ok");
    loadChannels();
    loadStatus();
  } catch (e) {
    flash($("#import-msg"), e.message, "err", 8000);
  } finally {
    btn.disabled = false;
  }
}

async function scanNow() {
  const btn = $("#scan-now");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const r = await api("/api/scan", { method: "POST" });
    if (r.error) flash($("#form-msg"), r.error, "err", 8000);
    loadAll();
  } catch (e) {
    flash($("#form-msg"), e.message, "err", 8000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan now";
  }
}

async function loadActivity() {
  const items = await api("/api/activity?limit=50");
  const tbody = $("#activity-table tbody");
  tbody.innerHTML = "";
  $("#activity-empty").hidden = items.length > 0;
  for (const it of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtTs(it.queued_at)}</td>
      <td>${escapeHtml(it.channel_name || it.channel_id)}</td>
      <td>${escapeHtml(it.title || "")}</td>
      <td class="id"><a href="https://youtu.be/${it.video_id}" target="_blank" rel="noopener">${it.video_id}</a></td>
    `;
    tbody.appendChild(tr);
  }
}

async function loadScans() {
  const items = await api("/api/scans?limit=15");
  const tbody = $("#scans-table tbody");
  tbody.innerHTML = "";
  for (const s of items) {
    const dur = s.finished_at && s.started_at ? `${s.finished_at - s.started_at}s` : "–";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtTs(s.started_at)}</td>
      <td>${dur}</td>
      <td>${s.channels ?? "–"}</td>
      <td>${s.candidates ?? "–"}</td>
      <td>${s.new_videos ?? "–"}</td>
      <td>${s.queued ?? "–"}</td>
      <td>${s.skipped ?? "–"}</td>
      <td class="${s.error ? "err" : ""}">${s.error ? escapeHtml(s.error) : ""}</td>
    `;
    tbody.appendChild(tr);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function loadAll() {
  loadStatus();
  loadChannels();
  loadActivity();
  loadScans();
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const btn = $("#theme-toggle");
  if (btn) {
    const effective = theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    btn.textContent = effective === "light" ? "Dark" : "Light";
  }
}

function initTheme() {
  const saved = localStorage.getItem("theme");
  applyTheme(saved);
  $("#theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme")
      || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = current === "light" ? "dark" : "light";
    localStorage.setItem("theme", next);
    applyTheme(next);
  });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (!localStorage.getItem("theme")) applyTheme(null);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#settings-form").addEventListener("input", checkDirty);
  $("#settings-form").addEventListener("change", checkDirty);
  $("#test-conn").addEventListener("click", testConnection);
  $("#add-channel-form").addEventListener("submit", addChannel);
  $("#opml-file").addEventListener("change", importOpml);
  $("#sync-ta").addEventListener("click", syncFromTa);
  $("#scan-now").addEventListener("click", scanNow);
  loadAll();
  setInterval(() => { loadStatus(); }, 15000);
});
