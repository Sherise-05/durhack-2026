/* Shared interactions. Calculations and saved records remain server controlled. */
(() => {
    "use strict";
    const zone = "Europe/London";
    const clock = new Intl.DateTimeFormat("en-GB", {timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    const wallClock = new Intl.DateTimeFormat("en-GB", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    const pad = value => String(value).padStart(2, "0");
    function wallText(value) {
        const parts = Object.fromEntries(wallClock.formatToParts(value).map(part => [part.type, part.value]));
        return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
    }
    function wallNumber(text) {
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(text);
        if (!match) return NaN;
        const [, y, m, d, h, minute] = match.map(Number);
        const result = new Date(0);
        result.setUTCFullYear(y, m - 1, d);
        result.setUTCHours(h, minute, 0, 0);
        if (result.getUTCFullYear() !== y || result.getUTCMonth() !== m - 1 || result.getUTCDate() !== d || result.getUTCHours() !== h || result.getUTCMinutes() !== minute) return NaN;
        return result.getTime();
    }
    function londonCandidates(text) {
        const wall = wallNumber(text);
        if (!Number.isFinite(wall)) return [];
        const offsets = new Set([-36, -12, 0, 12, 36].map(hours => {
            const stamp = wall + hours * 3600000;
            return wallNumber(wallText(new Date(stamp))) - stamp;
        }));
        const candidates = [];
        offsets.forEach(offset => {
            const stamp = wall - offset;
            if (wallText(new Date(stamp)) !== text) return;
            const minutes = Math.round(offset / 60000);
            const suffix = `${minutes < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
            candidates.push({stamp, iso: text + suffix, suffix});
        });
        return candidates.sort((a, b) => a.stamp - b.stamp);
    }

    document.querySelectorAll("input[data-datetime-field]").forEach(source => {
        const original = source.value;
        const aware = /(?:Z|[+-]\d\d:\d\d)$/.test(original);
        const parsed = aware ? Date.parse(original) : NaN;
        const naiveMatch = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(original);
        if (original && !Number.isFinite(parsed) && !naiveMatch) return;
        const picker = document.createElement("input");
        picker.type = "datetime-local";
        if (picker.type !== "datetime-local") return;
        picker.step = "60";
        picker.id = `${source.id}-picker`;
        picker.dataset.datetimePicker = source.id;
        picker.required = source.required;
        picker.value = Number.isFinite(parsed) ? wallText(new Date(parsed)) : (naiveMatch ? naiveMatch[1] : "");
        const wrapper = document.createElement("div"); wrapper.className = "datetime-control";
        const occurrence = document.createElement("select");
        occurrence.dataset.datetimeOccurrence = source.id;
        occurrence.className = "datetime-occurrence";
        occurrence.setAttribute("aria-label", "Choose which occurrence of this repeated Europe/London time");
        occurrence.hidden = true;
        const error = document.createElement("span"); error.className = "datetime-error";
        error.id = `${source.id}-error`; error.dataset.datetimeError = source.id;
        error.setAttribute("aria-live", "polite");
        picker.setAttribute("aria-describedby", error.id);
        wrapper.append(picker, occurrence, error);
        source.insertAdjacentElement("afterend", wrapper);
        const label = document.querySelector(`label[for="${CSS.escape(source.id)}"]`);
        if (label) label.htmlFor = picker.id;
        source.type = "hidden"; source.required = false;
        const help = source.closest(".field")?.querySelector("[data-datetime-help]");
        if (help) help.textContent = "Local time: Europe/London.";

        function update(preserveOriginal) {
            picker.setCustomValidity(""); occurrence.setCustomValidity(""); error.textContent = "";
            const previousChoice = occurrence.value;
            const candidates = picker.value ? londonCandidates(picker.value) : [];
            occurrence.replaceChildren(); occurrence.hidden = true; occurrence.required = false;
            if (!picker.value) { source.value = ""; return; }
            if (!candidates.length) {
                const message = "This local time does not exist when the clocks move forward. Choose another time.";
                picker.setCustomValidity(message); error.textContent = message; source.value = ""; return;
            }
            let chosen = candidates.length === 1 ? candidates[0] : null;
            if (candidates.length > 1) {
                occurrence.hidden = false; occurrence.required = true;
                occurrence.add(new Option("This time occurs twice — choose one", ""));
                candidates.forEach((candidate, index) => occurrence.add(new Option(`${index === 0 ? "First" : "Second"} occurrence (${candidate.suffix})`, candidate.iso)));
                const originalStamp = preserveOriginal && Number.isFinite(parsed) ? Math.floor(parsed / 60000) * 60000 : NaN;
                chosen = candidates.find(candidate => candidate.stamp === originalStamp) || candidates.find(candidate => candidate.iso === previousChoice) || null;
                occurrence.value = chosen ? chosen.iso : "";
                if (!chosen) {
                    const message = "The clocks repeat this time. Choose the first or second occurrence.";
                    picker.setCustomValidity(message); error.textContent = message; source.value = ""; return;
                }
            }
            source.value = preserveOriginal && original ? original : chosen.iso;
        }
        picker.addEventListener("input", () => update(false));
        occurrence.addEventListener("change", () => {
            if (!occurrence.value) { picker.setCustomValidity("Choose which occurrence of this time you mean."); source.value = ""; return; }
            source.value = occurrence.value; picker.setCustomValidity(""); error.textContent = "";
        });
        update(true);
    });

    document.querySelectorAll("form").forEach(form => {
        form.addEventListener("invalid", event => {
            let parent = event.target.parentElement;
            while (parent && parent !== form) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
        }, true);
        const action = form.getAttribute("action") || "";
        if (action.endsWith("/delete")) form.addEventListener("submit", event => {
            if (!window.confirm("Delete this saved sleep record? Its check-in will also be removed.")) event.preventDefault();
        });
    });

    const colours = ["#553688", "#a98bdc", "#b4579f"];
    function makeTable(canvas, labels, rows) {
        if (canvas.dataset.tableReady) return;
        canvas.dataset.tableReady = "true";
        const details = document.createElement("details"); details.className = "chart-data";
        const summary = document.createElement("summary"); summary.textContent = "View chart values";
        const scroll = document.createElement("div"); scroll.className = "table-scroll";
        const table = document.createElement("table");
        const caption = document.createElement("caption"); caption.textContent = canvas.getAttribute("aria-label") || "Chart values";
        const head = document.createElement("thead"), body = document.createElement("tbody"), heading = document.createElement("tr");
        labels.forEach(label => { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = label; heading.append(cell); });
        head.append(heading);
        rows.forEach(row => { const tr = document.createElement("tr"); row.forEach(value => { const cell = document.createElement("td"); cell.textContent = value; tr.append(cell); }); body.append(tr); });
        table.append(caption, head, body); scroll.append(table); details.append(summary, scroll);
        canvas.insertAdjacentElement("afterend", details);
    }
    function frame(canvas, domain, isBar) {
        const width = Math.max(120, canvas.clientWidth), height = 260;
        const scale = window.devicePixelRatio || 1;
        canvas.width = width * scale; canvas.height = height * scale;
        const ctx = canvas.getContext("2d");
        if (!ctx) return null;
        ctx.scale(scale, scale);
        const left = 38, right = width - 12, top = 24, bottom = height - 45;
        const y = value => bottom - value / domain * (bottom - top);
        ctx.font = "12px system-ui, sans-serif";
        ctx.fillStyle = "#6c6383"; ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const value = domain * i / 4;
            ctx.strokeStyle = "#ece5f6";
            ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
            ctx.fillText(String(Math.round(value * 10) / 10), 5, y(value) + 4);
        }
        ctx.fillStyle = "#6c6383";
        ctx.fillText(isBar ? "Hours" : "Estimate · 0–100", left, 12);
        return {ctx, width, height, left, right, top, bottom, y};
    }
    function installReadout(canvas, length, textAt, positionAt) {
        let readout = canvas.parentElement.querySelector(`[data-readout-for="${CSS.escape(canvas.dataset.chart)}"]`);
        if (!readout) {
            readout = document.createElement("p"); readout.className = "chart-readout";
            readout.dataset.readoutFor = canvas.dataset.chart;
            readout.textContent = "Point to the chart to inspect a value. Exact values are also listed below.";
            canvas.insertAdjacentElement("afterend", readout);
        }
        canvas.onpointermove = event => {
            const point = event.clientX - canvas.getBoundingClientRect().left;
            let closest = 0, distance = Infinity;
            for (let index = 0; index < length; index++) { const current = Math.abs(positionAt(index) - point); if (current < distance) { closest = index; distance = current; } }
            readout.textContent = textAt(closest);
        };
    }
    function drawLine(canvas, data) {
        const entries = Object.entries(data).map(([label, points]) => [label, points.map(point => ({time: Date.parse(point.at), value: point.alertness})).filter(point => Number.isFinite(point.time))]);
        const times = [...new Set(entries.flatMap(([, points]) => points.map(point => point.time)))].sort((a, b) => a - b);
        if (!times.length) return;
        const chart = frame(canvas, 100, false); if (!chart) return;
        const {ctx, left, right, bottom, y} = chart;
        const start = times[0], end = times[times.length - 1];
        const x = stamp => left + (stamp - start) / Math.max(end - start, 1) * (right - left);
        ctx.textAlign = "center"; ctx.fillStyle = "#6c6383";
        for (let i = 0; i < 4; i++) { const stamp = start + (end - start) * i / 3; ctx.textAlign = i === 0 ? "left" : i === 3 ? "right" : "center"; ctx.fillText(clock.format(new Date(stamp)), x(stamp), bottom + 23); }
        entries.forEach(([, points], series) => {
            ctx.strokeStyle = colours[series % colours.length]; ctx.lineWidth = 2.5;
            ctx.setLineDash(series % 2 ? [6, 4] : []); ctx.beginPath();
            let connected = false;
            points.forEach(point => {
                if (point.value === null || !Number.isFinite(point.value)) { connected = false; return; }
                if (connected) ctx.lineTo(x(point.time), y(point.value)); else ctx.moveTo(x(point.time), y(point.value));
                connected = true;
            });
            ctx.stroke(); ctx.setLineDash([]);
        });
        if (!canvas.dataset.legendReady) {
            canvas.dataset.legendReady = "true";
            const legend = document.createElement("div"); legend.className = "chart-legend";
            entries.forEach(([label], index) => { const span = document.createElement("span"), swatch = document.createElement("i"); swatch.className = "chart-swatch"; swatch.style.setProperty("--series-color", colours[index % colours.length]); if (index % 2) swatch.style.borderTopStyle = "dashed"; span.append(swatch, document.createTextNode(label)); legend.append(span); });
            canvas.insertAdjacentElement("afterend", legend);
        }
        const indexed = entries.map(([, points]) => new Map(points.map(point => [point.time, point.value])));
        const valueText = value => value === null ? "Asleep" : Number.isFinite(value) ? String(Math.round(value * 10) / 10) : "Not available";
        makeTable(canvas, ["Time (Europe/London)", ...entries.map(([label]) => label)], times.map(time => [clock.format(new Date(time)), ...indexed.map(points => valueText(points.get(time)))]));
        installReadout(canvas, times.length, index => `${clock.format(new Date(times[index]))} · ${entries.map(([label], series) => `${label}: ${valueText(indexed[series].get(times[index]))}`).join(" · ")}`, index => x(times[index]));
    }
    function drawBar(canvas, data) {
        const points = data.points || []; if (!points.length) return;
        const domain = Math.ceil(Math.max(10, ...points.map(point => Number.isFinite(point.value) ? point.value : 0), Number.isFinite(data.reference) ? data.reference : 0));
        const chart = frame(canvas, domain, true); if (!chart) return;
        const {ctx, left, right, bottom, y} = chart;
        const step = (right - left) / points.length;
        const width = Math.min(step * .64, 38);
        const x = index => left + step * (index + .5);
        ctx.fillStyle = colours[0];
        points.forEach((point, index) => { if (point.value === null || !Number.isFinite(point.value)) return; ctx.fillRect(x(index) - width / 2, y(point.value), width, bottom - y(point.value)); });
        ctx.textAlign = "center"; ctx.fillStyle = "#6c6383"; ctx.font = "11px system-ui, sans-serif";
        const every = chart.width < 300 ? 2 : 1;
        points.forEach((point, index) => { if (index % every === 0 || index === points.length - 1) ctx.fillText(point.label, x(index), bottom + 23); });
        if (Number.isFinite(data.reference)) {
            ctx.strokeStyle = "#8763c4"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]); ctx.beginPath(); ctx.moveTo(left, y(data.reference)); ctx.lineTo(right, y(data.reference)); ctx.stroke(); ctx.setLineDash([]);
        }
        makeTable(canvas, ["Wake-up date", "Estimated time asleep (hours)"], points.map(point => [point.label, point.value === null ? "Not recorded" : String(point.value)]));
        installReadout(canvas, points.length, index => `${points[index].label} · ${points[index].value === null ? "Not recorded" : `${points[index].value} hours estimated asleep`}`, x);
    }
    function drawWindow(canvas, data) {
        // Each night as a bar from bedtime to wake-up. Values are minutes after noon.
        const points = data.points || [];
        const known = points.filter(point => Number.isFinite(point.start) && Number.isFinite(point.end));
        if (!known.length) return;
        const lo = Math.floor((Math.min(...known.map(p => p.start)) - 30) / 60) * 60;
        const hi = Math.ceil((Math.max(...known.map(p => p.end)) + 30) / 60) * 60;
        const width = Math.max(120, canvas.clientWidth), height = 280, scale = window.devicePixelRatio || 1;
        canvas.width = width * scale; canvas.height = height * scale;
        const ctx = canvas.getContext("2d"); if (!ctx) return;
        ctx.scale(scale, scale);
        const left = 46, right = width - 12, top = 14, bottom = height - 40;
        const y = minutes => top + (minutes - lo) / Math.max(hi - lo, 1) * (bottom - top);
        const label = minutes => { const total = ((Math.round(minutes) + 720) % 1440 + 1440) % 1440; return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`; };
        ctx.font = "12px system-ui, sans-serif"; ctx.lineWidth = 1;
        const stepH = (hi - lo) > 720 ? 180 : 120;
        for (let m = lo; m <= hi; m += stepH) {
            ctx.strokeStyle = "#ece5f6"; ctx.beginPath(); ctx.moveTo(left, y(m)); ctx.lineTo(right, y(m)); ctx.stroke();
            ctx.fillStyle = "#6c6383"; ctx.textAlign = "left"; ctx.fillText(label(m), 4, y(m) + 4);
        }
        const step = (right - left) / points.length, barWidth = Math.min(step * .56, 26);
        const x = index => left + step * (index + .5);
        points.forEach((point, index) => {
            if (!Number.isFinite(point.start) || !Number.isFinite(point.end)) return;
            const top_ = y(point.start), h = Math.max(2, y(point.end) - top_);
            const gradient = ctx.createLinearGradient(0, top_, 0, top_ + h);
            gradient.addColorStop(0, "#54365f"); gradient.addColorStop(1, "#c9a3d8");
            ctx.fillStyle = gradient;
            const r = Math.min(barWidth / 2, 8), bx = x(index) - barWidth / 2;
            ctx.beginPath();
            if (ctx.roundRect) ctx.roundRect(bx, top_, barWidth, h, r); else ctx.rect(bx, top_, barWidth, h);
            ctx.fill();
        });
        ctx.textAlign = "center"; ctx.fillStyle = "#6c6383"; ctx.font = "11px system-ui, sans-serif";
        const every = width < 420 ? 3 : width < 640 ? 2 : 1;
        points.forEach((point, index) => { if (index % every === 0 || index === points.length - 1) ctx.fillText(point.label, x(index), bottom + 22); });
        const text = point => Number.isFinite(point.start) ? `${label(point.start)} → ${label(point.end)}` : "Not recorded";
        makeTable(canvas, ["Night (wake-up day)", "Asleep → awake"], points.map(point => [point.label, text(point)]));
        installReadout(canvas, points.length, index => `${points[index].label} · ${text(points[index])}`, x);
    }
    function drawCharts() {
        document.querySelectorAll("canvas[data-chart]").forEach(canvas => {
            const source = document.getElementById(canvas.dataset.chart); if (!source) return;
            let data; try { data = JSON.parse(source.textContent); } catch (_) { return; }
            if (canvas.dataset.chartKind === "bar") drawBar(canvas, data);
            else if (canvas.dataset.chartKind === "window") drawWindow(canvas, data);
            else drawLine(canvas, data);
        });
    }
    drawCharts();
    let resizeTimer;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawCharts, 120); });
})();
