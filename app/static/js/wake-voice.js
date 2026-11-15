/* Spoken wake-up message for the nap and sleep alarms (ElevenLabs, via /api/me/wake-voice).

   prepare(ctx, kind): fetch and decode the audio while the user is still here
     (call it right after they tap Sleep now / Nap now, with an AudioContext
     created during that tap, so browsers allow it to play later).
   play(ctx): play it when the countdown ends; resolves when it has finished.
   If ElevenLabs is unavailable, the browser's own voice reads the same message. */
window.DozeWakeVoice = (() => {
    "use strict";
    let buffer = null, message = "", pending = null;

    async function prepare(ctx, kind) {
        buffer = null; message = "";
        pending = (async () => {
            try {
                const response = await fetch(`/api/me/wake-voice?kind=${encodeURIComponent(kind)}`, {credentials: "same-origin"});
                if (response.ok && (response.headers.get("content-type") || "").includes("audio")) {
                    message = response.headers.get("X-Wake-Message") || "";
                    const bytes = await response.arrayBuffer();
                    if (ctx) buffer = await ctx.decodeAudioData(bytes);
                } else {
                    const data = await response.json().catch(() => ({}));
                    message = data.message || "Time to wake up!";
                }
            } catch (error) {
                message = message || "Time to wake up!";
            }
        })();
        return pending;
    }

    function speakWithBrowser(text) {
        return new Promise(resolve => {
            if (!("speechSynthesis" in window) || !text) { resolve(); return; }
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = "en-GB";
            utterance.onend = utterance.onerror = () => resolve();
            speechSynthesis.cancel();
            speechSynthesis.speak(utterance);
            setTimeout(resolve, 15000);  // never hold the alarm up for long
        });
    }

    async function play(ctx) {
        if (pending) await Promise.race([pending, new Promise(r => setTimeout(r, 4000))]);
        if (buffer && ctx) {
            try {
                if (ctx.state === "suspended") await ctx.resume();
                const source = ctx.createBufferSource();
                source.buffer = buffer;
                source.connect(ctx.destination);
                const done = new Promise(resolve => { source.onended = resolve; });
                source.start();
                await Promise.race([done, new Promise(r => setTimeout(r, 20000))]);
                return;
            } catch (error) { /* fall through to the browser voice */ }
        }
        await speakWithBrowser(message || "Time to wake up!");
    }

    // Resolves once the message (and audio) has loaded, or after a few seconds at most.
    const ready = () => Promise.race([pending || Promise.resolve(), new Promise(r => setTimeout(r, 4000))]);

    return {prepare, play, ready, get message() { return message; }};
})();
