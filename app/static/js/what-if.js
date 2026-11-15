/* Today's Tonight card: tap a "What if…" chip to compare it with Doze's plan, in place. */
(() => {
    "use strict";
    const box = document.querySelector("[data-what-if]");
    if (!box) return;
    const result = box.querySelector("[data-what-if-result]");
    const chips = [...box.querySelectorAll("[data-preset]")];
    const clock = new Intl.DateTimeFormat("en-GB", {timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    const hhmm = iso => clock.format(new Date(iso));
    const duration = hours => {
        const total = Math.round(Math.abs(hours) * 60), h = Math.floor(total / 60), m = total % 60;
        return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m} min`;
    };
    const el = (tag, props = {}, ...children) => { const node = Object.assign(document.createElement(tag), props); node.append(...children); return node; };

    function render(data) {
        const delta = data.sleep_delta_h;
        const headline = delta === null ? `${duration(data.sleep_h)} asleep`
            : Math.abs(delta) < 0.05 ? "About the same sleep as Doze's plan"
            : `${duration(delta)} ${delta < 0 ? "less" : "more"} sleep than Doze's plan`;
        const nodes = [
            el("p", {className: "what-if-headline " + (delta < -0.05 ? "is-worse" : delta > 0.05 ? "is-better" : "")},
               el("strong", {textContent: `${hhmm(data.bedtime)} → ${hhmm(data.alarm)}`}), ` · ${headline}`),
        ];
        if (data.events.length) {
            const rows = data.events.slice(0, 3).map(row => {
                const change = row.choice === null ? null : row.choice - row.plan;
                const text = row.late ? "late: no time to get ready" : change === null ? "—" : change === 0 ? "same" : (change > 0 ? `+${change}` : `${change}`);
                const tone = row.late || (change !== null && change < 0) ? "is-worse" : change > 0 ? "is-better" : "";
                return el("li", {},
                    el("span", {className: "what-if-event", textContent: `${row.title} · ${hhmm(row.start)}`}),
                    el("span", {className: "what-if-score", textContent: row.choice === null ? `${row.plan}` : `${row.plan} → ${row.choice}`}),
                    el("span", {className: "what-if-change " + tone, textContent: text}));
            });
            nodes.push(el("p", {className: "what-if-note", textContent: "Predicted alertness at your events (0–100):"}), el("ul", {className: "what-if-events"}, ...rows));
        }
        (data.warnings || []).slice(0, 2).forEach(w => nodes.push(el("p", {className: "what-if-warning", textContent: w})));
        result.replaceChildren(...nodes);
    }

    chips.forEach(chip => chip.addEventListener("click", async () => {
        const already = chip.getAttribute("aria-pressed") === "true";
        chips.forEach(c => c.setAttribute("aria-pressed", "false"));
        if (already) { result.hidden = true; return; }  // tap again to close
        chip.setAttribute("aria-pressed", "true");
        result.hidden = false;
        result.replaceChildren(el("p", {className: "what-if-note", textContent: "Simulating tomorrow…"}));
        try {
            const response = await fetch("/api/me/what-if/scenario", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                body: JSON.stringify({preset: chip.dataset.preset}),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't simulate that.");
            render(data);
        } catch (error) {
            result.replaceChildren(el("p", {className: "what-if-warning", textContent: error.message}));
        }
    }));
})();

/* Today's Nap card: tap a time to simulate a nap then, against no nap. */
(() => {
    "use strict";
    const box = document.querySelector("[data-nap-what-if]");
    if (!box) return;
    const result = box.querySelector("[data-what-if-result]");
    const chips = [...box.querySelectorAll("[data-at]")];
    const clock = new Intl.DateTimeFormat("en-GB", {timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    const hhmm = iso => clock.format(new Date(iso));
    const el = (tag, props = {}, ...children) => { const node = Object.assign(document.createElement(tag), props); node.append(...children); return node; };

    function render(data) {
        if (!data.possible) {
            result.replaceChildren(el("p", {className: "what-if-headline", textContent: data.headline + "."}));
            return;
        }
        const kind = data.kind === "full_cycle" ? "full-cycle nap" : "power nap";
        const lift = data.alertness_before !== null && data.alertness_after !== null
            ? ` · alertness ${data.alertness_before} → ${data.alertness_after}` : "";
        const nodes = [
            el("p", {className: "what-if-headline " + (data.helps ? "is-better" : "")},
               el("strong", {textContent: `${hhmm(data.lie_down)} → ${hhmm(data.alarm)}`}), ` · ${data.sleep_min}-min ${kind}${lift}`),
            el("p", {className: "what-if-note", textContent: `${data.helps ? "Predicted to help." : "Only a small predicted lift."} Uses ${data.tonight_cost_pct}% of tonight's sleep pressure.`}),
        ];
        if (data.events.length) {
            nodes.push(el("p", {className: "what-if-note", textContent: "Alertness at your events, without → with this nap:"}));
            nodes.push(el("ul", {className: "what-if-events"}, ...data.events.map(row => {
                const change = row.with_nap - row.without_nap;
                return el("li", {},
                    el("span", {className: "what-if-event", textContent: `${row.title} · ${hhmm(row.start)}`}),
                    el("span", {className: "what-if-score", textContent: `${row.without_nap} → ${row.with_nap}`}),
                    el("span", {className: "what-if-change " + (change > 0 ? "is-better" : change < 0 ? "is-worse" : ""), textContent: change === 0 ? "same" : (change > 0 ? `+${change}` : `${change}`)}));
            })));
        }
        (data.warnings || []).slice(0, 2).forEach(w => nodes.push(el("p", {className: "what-if-warning", textContent: w})));
        result.replaceChildren(...nodes);
    }

    chips.forEach(chip => chip.addEventListener("click", async () => {
        const already = chip.getAttribute("aria-pressed") === "true";
        chips.forEach(c => c.setAttribute("aria-pressed", "false"));
        if (already) { result.hidden = true; return; }
        chip.setAttribute("aria-pressed", "true");
        result.hidden = false;
        result.replaceChildren(el("p", {className: "what-if-note", textContent: "Simulating your nap…"}));
        try {
            const response = await fetch("/api/me/nap-what-if", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                body: JSON.stringify({at: chip.dataset.at === "" ? null : chip.dataset.at}),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't simulate that nap.");
            render(data);
        } catch (error) {
            result.replaceChildren(el("p", {className: "what-if-warning", textContent: error.message}));
        }
    }));
})();
