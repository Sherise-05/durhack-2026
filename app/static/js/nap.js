/* One-tap nap button: ask the nap maths for the best time, count down, ring.
   The plan is kept in localStorage only so a page refresh keeps the countdown. */
(() => {
    "use strict";
    const widget = document.querySelector("[data-nap-widget]");
    if (!widget) return;

    const STORE = "doze.nap.v1";
    const $ = sel => widget.querySelector(sel);
    const els = {
        main: $("[data-nap-main]"), icon: $("[data-nap-icon]"), eyebrow: $("[data-nap-eyebrow]"),
        title: $("[data-nap-title]"), sub: $("[data-nap-sub]"), panel: $("[data-nap-panel]"),
        reasonLabel: $("[data-nap-reason-label]"), reason: $("[data-nap-reason]"),
        warnings: $("[data-nap-warnings]"), cancel: $("[data-nap-cancel]"),
    };
    const clock = new Intl.DateTimeFormat("en-GB", {timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    const hhmm = iso => clock.format(new Date(iso));
    let plan = null, timer = null, audio = null, ringing = null, chimed = false;

    function save() {
        try { plan ? localStorage.setItem(STORE, JSON.stringify(plan)) : localStorage.removeItem(STORE); } catch (e) { /* storage unavailable */ }
    }
    function load() {
        try { return JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { return null; }
    }

    function countdown(ms) {
        const total = Math.max(0, Math.ceil(ms / 1000));
        const h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60;
        const pad = n => String(n).padStart(2, "0");
        return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
    }
    function describe(p) {
        const kind = p.kind === "full_cycle" ? "full-cycle nap" : "power nap";
        return `Lie down ${hhmm(p.lie_down)} · alarm ${hhmm(p.alarm)} · ${p.sleep_min}-min ${kind}`;
    }
    function show(state, {eyebrow, title, sub, icon = "☾"}) {
        widget.dataset.state = state;
        els.eyebrow.textContent = eyebrow;
        els.title.textContent = title;
        els.sub.textContent = sub;
        els.icon.textContent = icon;
    }
    function showWarnings(list) {
        els.warnings.replaceChildren(...(list || []).map(text => Object.assign(document.createElement("li"), {textContent: text})));
        els.warnings.hidden = !(list && list.length);
    }

    // ---- sound, notifications --------------------------------------------------
    function unlockAudio() {
        try {
            audio = audio || new (window.AudioContext || window.webkitAudioContext)();
            if (audio.state === "suspended") audio.resume();
        } catch (e) { audio = null; }
    }
    function beep(at, freq, length = 0.18) {
        const osc = audio.createOscillator(), gain = audio.createGain();
        osc.type = "sine"; osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.35, at + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
        osc.connect(gain).connect(audio.destination);
        osc.start(at); osc.stop(at + length + 0.05);
    }
    function chime() {
        if (!audio) return;
        const t = audio.currentTime;
        [523, 659, 784].forEach((f, i) => beep(t + i * 0.22, f, 0.4));
    }
    function notify(body) {
        try { if ("Notification" in window && Notification.permission === "granted") new Notification("Doze", {body, tag: "doze-nap"}); } catch (e) { /* ignore */ }
        try { navigator.vibrate && navigator.vibrate([300, 150, 300, 150, 600]); } catch (e) { /* ignore */ }
    }
    function startRinging() {
        if (ringing) return;
        ringing = "speaking";  // the wake-up message plays first, then the beeps
        notify("Time to wake up from your nap.");
        const ring = () => { if (audio) { const t = audio.currentTime; [880, 1175, 880, 1175].forEach((f, i) => beep(t + i * 0.2, f)); } };
        const startBeeps = () => {
            if (ringing !== "speaking") return;  // stopped while it was speaking
            ring();
            ringing = setInterval(ring, 1400);
            setTimeout(stopRinging, 120000); // give up after 2 minutes
        };
        if (window.DozeWakeVoice) window.DozeWakeVoice.play(audio).then(startBeeps);
        else startBeeps();
    }
    function stopRinging() {
        if (ringing && ringing !== "speaking") clearInterval(ringing);
        ringing = null;
        try { if ("speechSynthesis" in window) speechSynthesis.cancel(); } catch (e) { /* ignore */ }
    }

    // ---- states ------------------------------------------------------------------
    function reset() {
        stopRinging();
        clearInterval(timer); timer = null;
        plan = null; chimed = false; save();
        els.panel.hidden = true;
        els.cancel.hidden = false;
        showWarnings([]);
        show("idle", {eyebrow: "Tired right now?", title: "Nap now", sub: "Doze sets the alarm for you"});
    }

    function tick() {
        if (!plan || !plan.alarm) return;
        const now = Date.now(), lie = Date.parse(plan.lie_down), alarm = Date.parse(plan.alarm);
        if (now < lie) {
            show("waiting", {eyebrow: "Your nap starts in", title: countdown(lie - now), sub: describe(plan)});
        } else if (now < alarm) {
            if (!chimed && now - lie < 60000 && lie > plan.pressed_at + 60000) { chime(); notify(`Time to lie down. Alarm set for ${hhmm(plan.alarm)}.`); }
            chimed = true;
            show("napping", {eyebrow: "Sleep well · alarm in", title: countdown(alarm - now), sub: plan.demo ? `Demo timer · the real nap would be ${plan.sleep_min} min` : `Alarm ${hhmm(plan.alarm)} · includes time to fall asleep`, icon: "☾"});
        } else if (now - alarm < 10 * 60000) {
            show("ringing", {eyebrow: "Wake up", title: "Time to get up", sub: "Tap to stop the alarm", icon: "☀"});
            startRinging();
        } else {
            reset();
        }
    }

    function begin(p) {
        plan = p; save();
        els.panel.hidden = false;
        clearInterval(timer);
        tick();
        timer = setInterval(tick, 1000);
    }

    function showReason(text, source, label) {
        els.reasonLabel.textContent = label + (source === "gemini" ? " · explained by Gemini" : "");
        els.reason.textContent = text;
    }

    async function post(url) {
        const response = await fetch(url, {method: "POST", headers: {"Accept": "application/json"}, credentials: "same-origin"});
        if (response.status === 401) { window.location.href = "/login?next=" + encodeURIComponent(location.pathname); throw new Error("signed out"); }
        if (!response.ok) throw new Error("request failed");
        return response.json();
    }

    async function loadReason(label) {
        showReason("Thinking about your day…", null, label);
        try {
            const result = await post("/api/me/nap-now/reason?start=now");
            showReason(result.reason, result.source, label);
            if (plan) { plan.reason = result.reason; plan.source = result.source; save(); }
        } catch (e) {
            showReason("Couldn't load the explanation just now.", null, label);
        }
    }

    async function press() {
        const state = widget.dataset.state;
        if (state === "ringing") { reset(); return; }
        if (state !== "idle" && state !== "none") return; // a nap is already counting down
        unlockAudio();
        try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch (e) { /* ignore */ }
        show("loading", {eyebrow: "One moment", title: "Setting your nap…", sub: "Checking your sleep, body clock and calendar"});
        let result;
        try {
            result = await post("/api/me/nap-now?start=now");
        } catch (e) {
            reset();
            show("idle", {eyebrow: "Something went wrong", title: "Tap to try again", sub: "Doze couldn't reach the nap planner"});
            return;
        }
        showWarnings(result.warnings);
        if (result.status === "blocked") {
            plan = null; save();
            els.panel.hidden = false;
            els.cancel.hidden = true;
            show("none", {eyebrow: "Not right now", title: "Skip the nap", sub: "Tap to check again later", icon: "☀"});
            loadReason("Why not now");
            return;
        }
        els.cancel.hidden = false;
        // Fetch the spoken wake-up message now; the tap above unlocked audio for later.
        if (window.DozeWakeVoice) window.DozeWakeVoice.prepare(audio, "nap");
        const demoSeconds = Number(document.body.dataset.demoTimer || 0);
        if (demoSeconds > 0) {  // demo: ring after a few seconds instead of the real nap length
            const now = Date.now();
            result.lie_down = new Date(now).toISOString();
            result.alarm = new Date(now + demoSeconds * 1000).toISOString();
            result.demo = true;
        }
        begin({...result, pressed_at: Date.now()});
        loadReason("Why this timing");
    }

    els.main.addEventListener("click", press);
    els.cancel.addEventListener("click", reset);

    // Resume a countdown after a refresh (sound needs one tap again on most browsers).
    const stored = load();
    if (stored && stored.alarm && Date.now() - Date.parse(stored.alarm) < 10 * 60000) {
        chimed = Date.now() > Date.parse(stored.lie_down);
        begin(stored);
        showWarnings(stored.warnings);
        showReason(stored.reason || "", stored.source, "Why this timing");
        // After a refresh, sound needs one tap again; fetch the wake-up message then too.
        document.addEventListener("click", () => {
            unlockAudio();
            if (window.DozeWakeVoice) window.DozeWakeVoice.prepare(audio, "nap");
        }, {once: true});
    } else {
        reset();
    }
})();
