/* Reactive frontend state synced from WebSocket broadcasts */
const State = {
  data: {
    risk_score: 0,
    events: [],
    incidents: [],
    dlq: [],
    health: {},
    stats: { ingested: 0, correlated: 0, dlq: 0 },
  },
  page: "overview",
  chartsReady: false,

  init() {
    WS.onMessage((msg) => {
      if (msg.type === "state" || msg.type === "snapshot") {
        this.data = msg.data;
        App.renderAll();
      }
    });
    // initial fetch in case WS snapshot races
    API.state().then((d) => { this.data = d; App.renderAll(); }).catch(() => {});
  },

  activeIncidents() {
    return this.data.incidents.filter((i) => !i.resolved);
  },
};
