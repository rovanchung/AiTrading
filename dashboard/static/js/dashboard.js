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

// --- Timestamp parsing & date-aware time axis -----------------------------
// Parse "YYYY-MM-DD[ HH:MM:SS[.ffffff]]" (or ISO) into a local Date.
function parseLocalTimestamp(t) {
    let s = String(t).replace(' ', 'T').replace(/\.\d+$/, '');
    if (s.length === 10) s += 'T00:00:00';
    const d = new Date(s);
    return isNaN(d) ? null : d;
}

function fmtTime(d) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}
function fmtDate(d) {
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
function fmtDateTime(d) {
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

// X-axis tick callback: render HH:MM, and add a second line with the date
// (Mon D) only on the first visible tick and whenever the day changes from
// the previously shown tick — so a repeated date is never printed twice.
// Pass the same raw timestamp strings used as the chart's category labels.
function makeDateAwareTimeTicks(rawLabels) {
    const dates = rawLabels.map(parseLocalTimestamp);
    const sameDay = (a, b) => a && b && a.toDateString() === b.toDateString();
    return function (value, index, ticks) {
        const d = dates[value];
        if (!d) return '';
        const prev = index > 0 ? dates[ticks[index - 1].value] : null;
        return sameDay(prev, d) ? fmtTime(d) : [fmtTime(d), fmtDate(d)];
    };
}

// Reusable pieces for raw `new Chart(...)` time-series charts so they match
// the createLineChart date-aware axis (date shown only when the day changes).
function timeAxisScale(rawLabels, extra = {}) {
    return {
        grid: { display: false },
        ticks: { maxTicksLimit: 10, autoSkip: true, maxRotation: 0, callback: makeDateAwareTimeTicks(rawLabels) },
        ...extra,
    };
}
function timeTooltip(rawLabels) {
    return {
        callbacks: {
            title: items => {
                const d = parseLocalTimestamp(rawLabels[items[0].dataIndex]);
                return d ? fmtDateTime(d) : '';
            },
        },
    };
}

// --- Create line chart ---
// opts.xRawLabels: raw timestamp strings → enables the date-aware time axis
//   (smart tick labels + full date/time tooltip titles).
// opts.scales.{x,y}: merged over the defaults. opts.yFormat: y tick formatter.
function createLineChart(canvasId, labels, datasets, opts = {}) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    const { scales: optScales = {}, xRawLabels, yFormat, ...restOpts } = opts;

    const xTicks = { maxTicksLimit: 10, autoSkip: true, maxRotation: 0 };
    const tooltip = {};
    if (xRawLabels) {
        xTicks.callback = makeDateAwareTimeTicks(xRawLabels);
        tooltip.callbacks = {
            title: items => {
                const d = parseLocalTimestamp(xRawLabels[items[0].dataIndex]);
                return d ? fmtDateTime(d) : '';
            },
        };
    }

    return new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            ...restOpts,
            plugins: {
                legend: { display: datasets.length > 1 },
                tooltip,
                ...(restOpts.plugins || {}),
            },
            scales: {
                x: { grid: { display: false }, ticks: xTicks, ...(optScales.x || {}) },
                y: {
                    grid: { color: '#1e293b40' },
                    ticks: { callback: yFormat || (v => formatCurrency(v)) },
                    ...(optScales.y || {}),
                },
            },
            elements: {
                point: { radius: 0, hoverRadius: 4 },
                line: { tension: 0.3, borderWidth: 2 },
            },
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
// Start small; fitDataTableToViewport() grows the page if there's actual
// vertical room. This guarantees the rendered table never starts taller
// than the viewport, even if the fit measurement is delayed or fails.
const DT_DEFAULTS = {
    paging: true,
    pageLength: 8,
    ordering: true,
    searching: true,
    info: true,
    autoWidth: false,
    language: {
        emptyTable: '<span class="text-gray-500">No data available</span>',
        zeroRecords: '<span class="text-gray-500">No matching records</span>',
    },
};

// --- Multi-column row split -------------------------------------------------
// Tables marked `data-split="true"` will visually continue their rows in
// sibling clone tables when the card has horizontal room, separated by a
// vertical split line. Original tbody rows get .split-hidden so DataTables
// pagination/sort still see them; clones are append-only DOM we manage.

function shouldSplit(tableEl) {
    return !!(tableEl && tableEl.dataset && tableEl.dataset.split === 'true');
}

function teardownSplit($table) {
    const $wrapper = $table.closest('.dataTables_wrapper, .dt-container');
    $wrapper.find('table.table-clone').remove();
    // Scope to the outer tbody only — `find('tbody > tr')` would also match
    // rows inside nested tables (e.g., per-ticker buy-order breakdowns).
    $table.children('tbody').children('tr.split-hidden').removeClass('split-hidden');
    const $parent = $table.parent();
    if ($parent.hasClass('table-cols')) $table.unwrap();
}

function applySplit($table, numCols) {
    if (numCols <= 1) { teardownSplit($table); return; }
    if (!$table.parent().hasClass('table-cols')) {
        $table.wrap('<div class="table-cols"></div>');
    }
    const $cols = $table.parent();
    $cols.find('table.table-clone').remove();

    // Only the outer tbody's direct rows — nested tables inside cells must
    // not be treated as positions.
    const rows = $table.children('tbody').children('tr').get();
    const rowCount = rows.length;
    if (rowCount === 0) return;
    const perCol = Math.ceil(rowCount / numCols);

    for (let c = 1; c < numCols; c++) {
        const start = c * perCol;
        const end = Math.min(start + perCol, rowCount);
        if (start >= end) break;
        const $clone = $table.clone(false, true);
        $clone.removeAttr('id').addClass('table-clone');
        // $clone.find('tbody') would also match nested tbodies; pick the
        // direct child to keep nested detail tables intact in cloned rows.
        const cloneTbody = $clone.children('tbody').empty()[0];
        for (let i = start; i < end; i++) {
            const r = rows[i].cloneNode(true);
            r.classList.remove('split-hidden');
            cloneTbody.appendChild(r);
        }
        $cols.append($clone);
    }

    rows.forEach((r, i) => {
        r.classList.toggle('split-hidden', i >= perCol);
    });
}

// Measure each DataTable's actual rendered position and set pageLength so
// the widget fits within the viewport vertically. For data-split tables we
// also compute how many columns fit horizontally and multiply pageLength so
// each visual column has the same vertical row capacity.
function fitDataTableToViewport(api, depth) {
    depth = depth || 0;
    const tableEl = api.table().node();
    if (!tableEl) return;
    const $table = $(tableEl);
    teardownSplit($table);

    const tbody = tableEl.tBodies[0];
    if (!tbody) return;
    const rowCount = tbody.rows.length;
    if (rowCount === 0) return;
    const wrapperEl = tableEl.closest('.dataTables_wrapper, .dt-container') || tableEl;
    const tbodyRect = tbody.getBoundingClientRect();
    const wrapperRect = wrapperEl.getBoundingClientRect();
    const belowChrome = Math.max(0, wrapperRect.bottom - tbodyRect.bottom);
    const bottomMargin = 16;
    const rowH = tbodyRect.height / rowCount;
    if (!isFinite(rowH) || rowH <= 0) return;

    let numCols = 1;
    if (shouldSplit(tableEl)) {
        const tableWidth = tableEl.getBoundingClientRect().width;
        const $card = $table.closest('.card');
        const $cardParent = $card.parent();
        if (tableWidth > 0 && $cardParent.length) {
            const parentWidth = $cardParent[0].clientWidth;
            const cardPadX = parseFloat($card.css('padding-left')) + parseFloat($card.css('padding-right'));
            const usableWidth = parentWidth - cardPadX;
            const gap = 24;
            const totalRecs = api.rows().count();
            numCols = Math.max(1, Math.floor((usableWidth + gap) / (tableWidth + gap)));
            if (totalRecs > 0) numCols = Math.min(numCols, totalRecs);
        }
    }
    $(wrapperEl).data('split-cols', numCols);

    const availableForRows = window.innerHeight - tbodyRect.top - belowChrome - bottomMargin;
    const rowsPerCol = Math.max(3, Math.floor(availableForRows / rowH));
    const newLen = rowsPerCol * numCols;

    if (api.page.len() === newLen) {
        if (numCols > 1) applySplit($table, numCols);
        const finalRect = wrapperEl.getBoundingClientRect();
        if (finalRect.bottom > window.innerHeight && rowsPerCol > 3 && depth < 5) {
            api.page.len((rowsPerCol - 1) * numCols).draw(false);
            requestAnimationFrame(() => fitDataTableToViewport(api, depth + 1));
        }
        return;
    }
    api.page.len(newLen).draw(false);
    if (depth < 5) {
        requestAnimationFrame(() => fitDataTableToViewport(api, depth + 1));
    }
}

function fitAllDataTables() {
    if (typeof $ === 'undefined' || !$.fn || !$.fn.dataTable) return;
    const tables = $.fn.dataTable.tables();
    for (let i = 0; i < tables.length; i++) {
        const api = $(tables[i]).DataTable();
        fitDataTableToViewport(api);
    }
}

$(function() {
    if (typeof $ !== 'undefined' && $.fn && $.fn.dataTable) {
        $(document).on('init.dt', function(e, settings) {
            const api = new $.fn.dataTable.Api(settings);
            requestAnimationFrame(() => fitDataTableToViewport(api));
        });
        // Pagination/sort/search redraws the original tbody but leaves
        // our clone tables with stale rows. Re-apply the split with the
        // numCols decided by the most recent fit pass.
        $(document).on('draw.dt', function(e, settings) {
            const tableEl = settings.nTable;
            if (!shouldSplit(tableEl)) return;
            const $table = $(tableEl);
            const $wrapper = $table.closest('.dataTables_wrapper, .dt-container');
            const numCols = parseInt($wrapper.data('split-cols') || '1', 10) || 1;
            applySplit($table, numCols);
        });
    }
    setTimeout(fitAllDataTables, 0);
});

window.addEventListener('load', () => {
    fitAllDataTables();
    // Re-measure once more after styles/fonts settle.
    setTimeout(fitAllDataTables, 50);
});

(function attachResizeFit() {
    let raf = null;
    window.addEventListener('resize', () => {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(fitAllDataTables);
    });
})();
