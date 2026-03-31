/* AiTrading Dashboard — Shared utilities and chart helpers */

// --- Clock ---
function updateClock() {
    const el = document.getElementById('clock');
    if (el) {
        const now = new Date();
        el.textContent = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
}
setInterval(updateClock, 1000);
updateClock();

// --- Last refresh ---
function updateRefreshTime() {
    const el = document.getElementById('last-refresh');
    if (el) {
        el.textContent = 'Updated: ' + new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    }
}
updateRefreshTime();

// --- Chart.js global defaults ---
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace';
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyleWidth = 8;
Chart.defaults.plugins.tooltip.backgroundColor = '#0f172a';
Chart.defaults.plugins.tooltip.borderColor = '#334155';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.titleColor = '#f1f5f9';
Chart.defaults.plugins.tooltip.bodyColor = '#94a3b8';
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.cornerRadius = 8;

// --- Helpers ---
function formatCurrency(value) {
    if (value == null) return '$0.00';
    return '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPct(value) {
    if (value == null) return '0.0%';
    const sign = value >= 0 ? '+' : '';
    return sign + Number(value).toFixed(1) + '%';
}

function scoreColor(val) {
    if (val >= 75) return '#22c55e';
    if (val >= 60) return '#3b82f6';
    if (val >= 45) return '#eab308';
    return '#ef4444';
}

function pnlColor(val) {
    if (val > 0) return '#22c55e';
    if (val < 0) return '#ef4444';
    return '#94a3b8';
}

// --- Fetch helper ---
async function fetchJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Fetch failed: ${resp.status}`);
    return resp.json();
}

// --- Create line chart ---
function createLineChart(canvasId, labels, datasets, opts = {}) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    return new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: datasets.length > 1 },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 10 },
                },
                y: {
                    grid: { color: '#1e293b40' },
                    ticks: {
                        callback: opts.yFormat || (v => formatCurrency(v)),
                    },
                },
            },
            elements: {
                point: { radius: 0, hoverRadius: 4 },
                line: { tension: 0.3, borderWidth: 2 },
            },
            ...opts,
        },
    });
}

// --- Create radar chart ---
function createRadarChart(canvasId, labels, values, label = 'Score') {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    return new Chart(ctx, {
        type: 'radar',
        data: {
            labels,
            datasets: [{
                label,
                data: values,
                backgroundColor: 'rgba(59, 130, 246, 0.15)',
                borderColor: '#3b82f6',
                borderWidth: 2,
                pointBackgroundColor: values.map(v => scoreColor(v)),
                pointBorderColor: 'transparent',
                pointRadius: 5,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                r: {
                    beginAtZero: true,
                    max: 100,
                    ticks: { stepSize: 25, display: false },
                    grid: { color: '#1e293b' },
                    angleLines: { color: '#1e293b' },
                    pointLabels: { font: { size: 12 } },
                },
            },
            plugins: {
                legend: { display: false },
            },
        },
    });
}

// --- Create doughnut chart ---
function createDoughnutChart(canvasId, labels, values, colors) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderColor: '#0f172a',
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    position: 'right',
                    labels: { padding: 15, font: { size: 12 } },
                },
            },
        },
    });
}

// --- Auto-refresh ---
function autoRefresh(fn, intervalMs = 30000) {
    fn();
    return setInterval(() => {
        fn();
        updateRefreshTime();
    }, intervalMs);
}

// --- DataTables default config ---
const DT_DEFAULTS = {
    paging: true,
    pageLength: 25,
    ordering: true,
    searching: true,
    info: true,
    autoWidth: false,
    language: {
        emptyTable: '<span class="text-gray-500">No data available</span>',
        zeroRecords: '<span class="text-gray-500">No matching records</span>',
    },
};
