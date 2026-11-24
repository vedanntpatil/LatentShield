/* LatentShield SPA: 6-page router, renderers, controls, JSON diff modal */
const App = {
  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  time(iso) { try { return new Date(iso).toLocaleTimeString(); } catch { return iso; } },

  // ---------- router ----------
  init() {
    document.querySelectorAll(".nav-btn").forEach(b =>
      b.addEventListener("click", () => this.go(b.dataset.page)));
    document.getElementById("modal-close").addEventListener("click", () =>
      document.getElementById("modal").classList.add("hidden"));
    WS.connect();
    State.init();
    this.go("overview");
  },

  go(page) {
    State.page = page;
    document.querySelectorAll(".page").forEach(p => p.classList.add("hidden"));
    document.getElementById(`page-${page}`).classList.remove("hidden");
    document.querySelectorAll(".nav-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.page === page));
    this.renderAll();
    lucide.createIcons();
  },

  renderAll() {
    this.renderHealthBar();
    this.renderStats();
    if (State.page === "overview") this.renderOverview();
    if (State.page === "events") this.renderEvents();
    if (State.page === "incidents") this.renderIncidents();
    if (State.page === "dlq") this.renderDlq();
    if (State.page === "telemetry") this.renderTelemetry();
    if (State.page === "controls") this.renderControls();
    lucide.createIcons();
  },

  // ---------- persistent header ----------
  renderHealthBar() {
    const bar = document.getElementById("health-bar");
    const order = ["ALARM", "CAMERA", "RFID", "ENV_SENSOR"];
    bar.innerHTML = order.map(s => {
      const h = State.data.health[s] || {};
      const st = h.state || "ONLINE";
      const cls = st === "ONLINE" ? "bg-emerald-500" : st === "DEGRADED" ? "bg-amber-500" : "bg-red-500";
      const label = st === "ONLINE" ? "Online" : st === "DEGRADED" ? "Degraded" : "Offline";
      return `<div class="flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-slate-800 border border-slate-700"
        title="${s}: ${label} — last seen ${h.last_seen ? this.time(h.last_seen) : "never"}">
        <span class="w-2 h-2 rounded-full ${cls} ${st !== "ONLINE" ? "live-dot" : ""}"></span>
        <span class="text-slate-400">${s.replace("_", " ")}</span>
        <span class="${st === "ONLINE" ? "text-emerald-400" : st === "DEGRADED" ? "text-amber-400" : "text-red-400"}">${label}</span>
      </div>`;
    }).join("");
    document.getElementById("stat-ingested").textContent = State.data.stats?.ingested ?? 0;
    document.getElementById("stat-correlated").textContent = State.data.stats?.correlated ?? 0;
    document.getElementById("stat-dlq").textContent = State.data.stats?.dlq ?? 0;
  },

  renderStats() {},

  // ---------- Overview ----------
  renderOverview() {
    const p = document.getElementById("page-overview");
    Charts.drawGauge(State.data.risk_score);
    Charts.pushRisk(State.data.risk_score);
    const inc = State.activeIncidents();
    const sevCount = { CRITICAL: 0, WARNING: 0 };
    inc.forEach(i => sevCount[i.severity] = (sevCount[i.severity] || 0) + 1);
    p.innerHTML = `
      <div class="grid lg:grid-cols-3 gap-4">
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 text-center">
          <div id="risk-gauge"></div>
          <div class="text-xs text-slate-500 mt-1">Updated ${this.time(State.data.risk_updated_at)}</div>
        </div>
        <div class="lg:col-span-2 space-y-4">
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
            ${this.statCard("siren", "Active Incidents", inc.length, "text-red-400")}
            ${this.statCard("alert-triangle", "Critical", sevCount.CRITICAL || 0, "text-red-500")}
            ${this.statCard("alert-circle", "Warnings", sevCount.WARNING || 0, "text-amber-400")}
            ${this.statCard("activity", "Events Buffered", State.data.events.length, "text-cyan-400")}
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <h3 class="text-sm font-semibold text-slate-300 mb-2 flex items-center gap-2"><i data-lucide="flame" class="w-4 h-4 text-orange-400"></i>Top Active Incidents</h3>
            <div class="space-y-2">
              ${inc.slice(0, 5).map(i => this.incidentRow(i)).join("") || '<div class="text-sm text-slate-500">No active incidents — all clear.</div>'}
            </div>
          </div>
        </div>
      </div>`;
  },

  statCard(icon, label, value, color) {
    return `<div class="bg-slate-900 border border-slate-800 rounded-xl p-3">
      <div class="flex items-center gap-2 text-xs text-slate-500"><i data-lucide="${icon}" class="w-3.5 h-3.5"></i>${label}</div>
      <div class="text-2xl font-bold ${color} mt-1">${value}</div></div>`;
  },

  incidentRow(i) {
    return `<div class="flex items-center justify-between bg-slate-950 rounded-lg px-3 py-2 text-sm">
      <div class="min-w-0">
        <div class="font-medium text-slate-200 truncate">${this.esc(i.title)}</div>
        <div class="text-xs text-slate-500">${this.esc(i.zone_id)} · conf ${(i.confidence * 100).toFixed(0)}% · ${this.time(i.updated_at)}</div>
      </div>
      <span class="sev-${i.severity} text-xs px-2 py-0.5 rounded bg-slate-800 shrink-0 ml-2">${i.severity}</span>
    </div>`;
  },

  // ---------- Events ----------
  renderEvents() {
    const p = document.getElementById("page-events");
    const evs = State.data.events;
    p.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold text-white flex items-center gap-2"><i data-lucide="radio" class="w-5 h-5 text-cyan-400"></i>Canonical Event Stream</h2>
        <span class="text-xs text-slate-500">last ${evs.length} of 500 buffered</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <table class="w-full text-sm">
          <thead class="bg-slate-950 text-slate-500 text-xs uppercase">
            <tr><th class="text-left p-3">Time</th><th class="text-left p-3">Source</th><th class="text-left p-3">Zone</th><th class="text-left p-3">Type</th><th class="text-left p-3">Severity</th><th class="text-left p-3">Summary</th><th class="p-3"></th></tr>
          </thead>
          <tbody>
            ${evs.map(e => `<tr class="border-t border-slate-800 hover:bg-slate-800/50">
              <td class="p-3 text-slate-400 whitespace-nowrap">${this.time(e.timestamp)}</td>
              <td class="p-3"><span class="text-xs px-1.5 py-0.5 rounded bg-slate-800">${e.source_type}</span></td>
              <td class="p-3 text-slate-300">${e.zone_id}</td>
              <td class="p-3 text-slate-300">${e.event_type}</td>
              <td class="p-3 sev-${e.severity} text-xs">${e.severity}</td>
              <td class="p-3 text-slate-400 truncate max-w-[280px]" title="${this.esc(e.summary)}">${this.esc(e.summary)}</td>
              <td class="p-3"><button class="view-raw text-cyan-400 hover:text-cyan-300" data-idx="${evs.indexOf(e)}"><i data-lucide="code-2" class="w-4 h-4"></i></button></td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
    p.querySelectorAll(".view-raw").forEach(b => b.addEventListener("click", () =>
      this.showDiff(State.data.events[+b.dataset.idx])));
  },

  // ---------- Incidents ----------
  renderIncidents() {
    const p = document.getElementById("page-incidents");
    const incs = State.data.incidents;
    p.innerHTML = `
      <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-3"><i data-lucide="siren" class="w-5 h-5 text-red-400"></i>Correlated Incidents</h2>
      <div class="space-y-2">
        ${incs.map(i => `
          <div class="bg-slate-900 border ${i.resolved ? "border-slate-800 opacity-50" : "border-slate-700"} rounded-xl p-4">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-semibold text-white">${this.esc(i.title)}</span>
                  <span class="sev-${i.severity} text-xs px-2 py-0.5 rounded bg-slate-800">${i.severity}</span>
                  ${i.resolved ? '<span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-500">RESOLVED</span>' : '<span class="text-xs px-2 py-0.5 rounded bg-red-950 text-red-300">ACTIVE</span>'}
                </div>
                <div class="text-xs text-slate-500 mt-1">${i.incident_id} · ${this.esc(i.zone_id)} · rule ${i.rule_id} · conf ${(i.confidence * 100).toFixed(0)}% · ${i.contributing_event_ids.length} events · opened ${this.time(i.created_at)}</div>
                <div class="text-sm text-slate-400 mt-1">${this.esc(i.detail)}</div>
              </div>
              ${!i.resolved ? `<button class="resolve-btn shrink-0 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-emerald-900/50 text-emerald-300 border border-slate-700" data-id="${i.incident_id}">Resolve</button>` : ""}
            </div>
          </div>`).join("") || '<div class="text-slate-500 text-sm">No incidents yet.</div>'}
      </div>`;
    p.querySelectorAll(".resolve-btn").forEach(b => b.addEventListener("click", async () => {
      await API.resolveIncident(b.dataset.id); App.renderAll();
    }));
  },

  // ---------- DLQ ----------
  renderDlq() {
    const p = document.getElementById("page-dlq");
    const dlq = State.data.dlq;
    p.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold text-white flex items-center gap-2"><i data-lucide="archive" class="w-5 h-5 text-amber-400"></i>Dead-Letter Queue</h2>
        <span class="text-xs text-slate-500">invalid payloads quarantined safely — never crash ingestion</span>
      </div>
      <div class="space-y-2">
        ${dlq.map(d => `
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="font-mono text-xs text-amber-300">${d.dlq_id} · ${this.esc(d.endpoint)} · ${this.time(d.timestamp)}</div>
                <div class="text-sm text-red-300 mt-1">${this.esc(d.error_reason)}</div>
                <pre class="json text-xs bg-slate-950 rounded p-2 mt-2 text-slate-400 max-h-32 overflow-auto">${this.esc(JSON.stringify(d.raw_body, null, 2))}</pre>
              </div>
              <button class="replay-btn shrink-0 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-cyan-900/50 text-cyan-300 border border-slate-700" data-id="${d.dlq_id}">Replay</button>
            </div>
          </div>`).join("") || '<div class="text-slate-500 text-sm">DLQ is empty — all payloads valid.</div>'}
      </div>`;
    p.querySelectorAll(".replay-btn").forEach(b => b.addEventListener("click", async () => {
      const r = await API.replayDlq(b.dataset.id);
      if (r.replay?.status === "accepted") alert("Replay succeeded — event ingested.");
      else alert(`Replay still failing: ${r.replay?.reason || "unknown"}`);
      App.renderAll();
    }));
  },

  // ---------- Telemetry ----------
  _lastEventCount: 0,
  renderTelemetry() {
    const p = document.getElementById("page-telemetry");
    const shell = `
      <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-3"><i data-lucide="line-chart" class="w-5 h-5 text-cyan-400"></i>Live Telemetry</h2>
      <div class="grid md:grid-cols-2 gap-4">
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4"><h3 class="text-sm text-slate-400 mb-2">Threat Risk Score History</h3><canvas id="risk-history" height="180"></canvas></div>
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4"><h3 class="text-sm text-slate-400 mb-2">Environmental (Temp °C / Smoke ppm)</h3><canvas id="temp-chart" height="180"></canvas></div>
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 md:col-span-2"><h3 class="text-sm text-slate-400 mb-2">Event Throughput</h3><canvas id="events-chart" height="120"></canvas></div>
      </div>`;
    const canvasesAlive = document.getElementById("risk-history") &&
      document.getElementById("risk-history").isConnected;
    if (!canvasesAlive) {
      p.innerHTML = shell;
      Charts.chartsReady = false;
      Charts.initCharts();
    }
    Charts.pushTelemetry(State.data.events.slice(0, Math.max(0, State.data.events.length - this._lastEventCount)));
    this._lastEventCount = State.data.events.length;
  },

  // ---------- Controls ----------
  renderControls() {
    const p = document.getElementById("page-controls");
    API.simStatus().then(s => {
      p.innerHTML = `
        <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-4"><i data-lucide="sliders-horizontal" class="w-5 h-5 text-cyan-400"></i>Simulation Controls</h2>
        <div class="grid md:grid-cols-2 gap-4">
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <h3 class="font-semibold text-white mb-2">Ambient Simulator</h3>
            <p class="text-xs text-slate-500 mb-3">Background telemetry generator emitting realistic low-rate events from all zones.</p>
            <button id="btn-pause" class="px-4 py-2 rounded-lg text-sm ${s.paused ? "bg-emerald-700 hover:bg-emerald-600" : "bg-amber-700 hover:bg-amber-600"}">
              ${s.paused ? "Resume Simulator" : "Pause Simulator"}
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <h3 class="font-semibold text-white mb-2">Demo Scenarios</h3>
            <p class="text-xs text-slate-500 mb-3">Deterministic multi-sensor injectors that trigger real correlation rules.</p>
            <div class="flex flex-wrap gap-2">
              <button data-sc="intrusion" class="sc-btn px-3 py-2 rounded-lg text-sm bg-red-900/60 hover:bg-red-800 border border-red-800">Forced Intrusion</button>
              <button data-sc="fire" class="sc-btn px-3 py-2 rounded-lg text-sm bg-orange-900/60 hover:bg-orange-800 border border-orange-800">Fire / Smoke</button>
              <button data-sc="bad_payload" class="sc-btn px-3 py-2 rounded-lg text-sm bg-slate-800 hover:bg-slate-700 border border-slate-700">Corrupt Payloads → DLQ</button>
            </div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 md:col-span-2">
            <h3 class="font-semibold text-white mb-2">Fault Injection (graceful degradation)</h3>
            <p class="text-xs text-slate-500 mb-3">Force a source OFFLINE/DEGRADED — ingestion continues, correlation degrades confidence, risk adjusts.</p>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
              ${["ALARM", "CAMERA", "RFID", "ENV_SENSOR"].map(s2 => `
                <div class="border border-slate-800 rounded-lg p-2">
                  <div class="text-xs text-slate-400 mb-2">${s2.replace("_", " ")}</div>
                  <div class="flex gap-1">
                    <button data-fault="${s2}:OFFLINE" class="fault-btn flex-1 text-xs px-2 py-1 rounded bg-red-950 hover:bg-red-900 text-red-300">Offline</button>
                    <button data-fault="${s2}:DEGRADED" class="fault-btn flex-1 text-xs px-2 py-1 rounded bg-amber-950 hover:bg-amber-900 text-amber-300">Degraded</button>
                    <button data-fault="${s2}:ONLINE" class="fault-btn flex-1 text-xs px-2 py-1 rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-300">Online</button>
                  </div>
                </div>`).join("")}
            </div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 md:col-span-2">
            <h3 class="font-semibold text-white mb-2">Session Export</h3>
            <p class="text-xs text-slate-500 mb-3">Download a full JSON snapshot (events, incidents, DLQ, health, stats).</p>
            <a href="/api/export" download="latentshield-session.json" class="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm bg-cyan-800 hover:bg-cyan-700 text-white">
              <i data-lucide="download" class="w-4 h-4"></i> Export JSON
            </a>
          </div>
        </div>`;
      document.getElementById("btn-pause")?.addEventListener("click", async () => {
        s.paused ? await API.resumeSim() : await API.pauseSim(); App.renderAll();
      });
      p.querySelectorAll(".sc-btn").forEach(b => b.addEventListener("click", async () => {
        await API.scenario(b.dataset.sc); App.renderAll();
      }));
      p.querySelectorAll(".fault-btn").forEach(b => b.addEventListener("click", async () => {
        const [src, st] = b.dataset.fault.split(":");
        await API.fault(src, st); App.renderAll();
      }));
      lucide.createIcons();
    });
  },

  // ---------- JSON diff modal ----------
  showDiff(ev) {
    document.getElementById("modal-canonical").textContent = JSON.stringify(ev, null, 2);
    document.getElementById("modal-raw").textContent = JSON.stringify(ev.raw_payload, null, 2);
    document.getElementById("modal").classList.remove("hidden");
  },
};

document.addEventListener("DOMContentLoaded", () => App.init());
