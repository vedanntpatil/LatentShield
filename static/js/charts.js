/* SVG semicircular Risk Gauge + Chart.js live telemetry graphs */
const Charts = {
  riskChart: null,
  tempChart: null,
  eventChart: null,
  gaugeCtx: null,

  gaugeColor(score) {
    if (score >= 70) return "#ef4444";
    if (score >= 40) return "#f59e0b";
    if (score >= 15) return "#eab308";
    return "#22c55e";
  },

  drawGauge(score) {
    const el = document.getElementById("risk-gauge");
    if (!el) return;
    const r = 80, cx = 110, cy = 105;
    const circ = Math.PI * r; // semicircle length
    const pct = Math.max(0, Math.min(100, score)) / 100;
    const color = this.gaugeColor(score);
    el.innerHTML = `
      <svg viewBox="0 0 220 125" class="w-full max-w-xs mx-auto">
        <path d="M 30 105 A 80 80 0 0 1 190 105" fill="none" stroke="#1e293b" stroke-width="14" stroke-linecap="round"/>
        <path id="gauge-arc" class="gauge-arc" d="M 30 105 A 80 80 0 0 1 190 105"
              fill="none" stroke="${color}" stroke-width="14" stroke-linecap="round"
              stroke-dasharray="${circ}" stroke-dashoffset="${circ * (1 - pct)}"/>
        <text x="110" y="95" text-anchor="middle" class="fill-white" font-size="34" font-weight="700">${Math.round(score)}</text>
        <text x="110" y="115" text-anchor="middle" class="fill-slate-500" font-size="11">THREAT RISK / 100</text>
      </svg>`;
  },

  initCharts() {
    if (this.chartsReady) return;
    const dark = { color: "#94a3b8" };
    this.riskChart = new Chart(document.getElementById("risk-history"), {
      type: "line",
      data: { labels: [], datasets: [{ label: "Risk", data: [], borderColor: "#22d3ee", backgroundColor: "rgba(34,211,238,.15)", fill: true, tension: .3 }] },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { ticks: dark }, y: { min: 0, max: 100, ticks: dark } }, animation: false },
    });
    this.tempChart = new Chart(document.getElementById("temp-chart"), {
      type: "line",
      data: { labels: [], datasets: [
        { label: "Temp °C", data: [], borderColor: "#f59e0b", tension: .3, spanGaps: true },
        { label: "Smoke ppm", data: [], borderColor: "#ef4444", tension: .3, spanGaps: true, yAxisID: "y1" },
      ]},
      options: { responsive: true, scales: { x: { ticks: dark }, y: { position: "left", ticks: dark }, y1: { position: "right", grid: { drawOnChartArea: false }, ticks: dark } }, animation: false },
    });
    this.eventChart = new Chart(document.getElementById("events-chart"), {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Events/min", data: [], backgroundColor: "#38bdf8" }] },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { ticks: dark }, y: { beginAtZero: true, ticks: dark } }, animation: false },
    });
    this.chartsReady = true;
  },

  pushRisk(score) {
    if (!this.chartsReady) return;
    const t = new Date().toLocaleTimeString();
    const r = this.riskChart.data;
    r.labels.push(t); r.datasets[0].data.push(score);
    if (r.labels.length > 60) { r.labels.shift(); r.datasets[0].data.shift(); }
    this.riskChart.update();
  },

  pushTelemetry(events) {
    if (!this.chartsReady) return;
    const temps = events.filter(e => e.metrics?.temp_celsius != null).slice(0, 30).reverse();
    if (!temps.length) return;
    const t = this.tempChart.data;
    temps.forEach(e => {
      t.labels.push(new Date(e.timestamp).toLocaleTimeString());
      t.datasets[0].data.push(e.metrics.temp_celsius);
      t.datasets[1].data.push(e.metrics.smoke_ppm);
    });
    while (t.labels.length > 60) { t.labels.shift(); t.datasets[0].data.shift(); t.datasets[1].data.shift(); }
    this.tempChart.update();

    // event rate per source
    const counts = {};
    events.forEach(e => counts[e.source_type] = (counts[e.source_type] || 0) + 1);
    const ec = this.eventChart.data;
    ec.labels.push(new Date().toLocaleTimeString());
    ec.datasets[0].data.push(Object.values(counts).reduce((a,b)=>a+b,0));
    if (ec.labels.length > 60) { ec.labels.shift(); ec.datasets[0].data.shift(); }
    this.eventChart.update();
  },
};
