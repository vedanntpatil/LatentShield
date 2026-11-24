/* REST API client + auto-reconnecting WebSocket handler */
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? "{}" : JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  },

  state: () => API.get("/api/state"),
  events: (n = 200) => API.get(`/api/events?limit=${n}`),
  incidents: () => API.get("/api/incidents"),
  dlq: () => API.get("/api/dlq"),
  simStatus: () => API.get("/api/sim/status"),
  exportSession: () => API.get("/api/export"),

  ingest: (kind, payload) => API.post(`/api/ingest/${kind}`, payload),
  resolveIncident: (id) => API.post(`/api/incidents/${id}/resolve`),
  replayDlq: (id) => API.post(`/api/dlq/${id}/replay`),
  pauseSim: () => API.post("/api/sim/pause"),
  resumeSim: () => API.post("/api/sim/resume"),
  scenario: (name) => API.post(`/api/sim/scenario/${name}`),
  fault: (source, state) => API.post(`/api/sim/fault/${source}?state=${state}`),
};

/* ---- WebSocket with auto-reconnect ---- */
const WS = {
  socket: null,
  listeners: new Set(),
  retryMs: 1000,

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${proto}://${location.host}/ws`);
    this.socket.onopen = () => {
      this.retryMs = 1000;
      document.getElementById("conn-dot").className = "w-2 h-2 rounded-full bg-emerald-400 live-dot";
      document.getElementById("conn-text").textContent = "live";
    };
    this.socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        this.listeners.forEach((fn) => fn(msg));
      } catch (_) {}
    };
    this.socket.onclose = () => {
      document.getElementById("conn-dot").className = "w-2 h-2 rounded-full bg-red-500";
      document.getElementById("conn-text").textContent = "reconnecting…";
      setTimeout(() => this.connect(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, 15000);
    };
  },
  onMessage(fn) { this.listeners.add(fn); },
};
