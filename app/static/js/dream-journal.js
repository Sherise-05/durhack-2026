/* Dream journal: speak or type a dream, save it, read it back, delete it. */
document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-dream-form]");
    if (!form) return;
    const title = document.getElementById("dream-title");
    const content = document.getElementById("dream-content");
    const status = document.getElementById("dream-status");
    const interim = form.querySelector("[data-interim]");
    const mic = document.getElementById("record-dream");
    const micLabel = form.querySelector("[data-mic-label]");
    const micHint = form.querySelector("[data-mic-hint]");
    const save = form.querySelector("[data-save]");

    // ---- read aloud (browser voice) ----
    function speak(text) {
        if (!("speechSynthesis" in window)) { status.textContent = "Reading aloud isn't supported in this browser."; return; }
        if (!text.trim()) { status.textContent = "Write or say something first."; return; }
        speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "en-GB"; utterance.rate = 0.9; utterance.pitch = 0.95;
        speechSynthesis.speak(utterance);
    }
    document.getElementById("read-dream").addEventListener("click", () => speak(content.value));
    document.querySelectorAll(".read-entry").forEach(button => button.addEventListener("click", () => {
        if (speechSynthesis.speaking) { speechSynthesis.cancel(); return; }
        speak(button.dataset.dreamContent || "");
    }));

    // ---- speech to text, with live words as you speak ----
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (Recognition) {
        const recognition = new Recognition();
        recognition.lang = "en-GB"; recognition.continuous = true; recognition.interimResults = true;
        let listening = false;
        const setListening = on => {
            listening = on;
            mic.dataset.state = on ? "listening" : "idle";
            micLabel.textContent = on ? "Listening… tap to stop" : "Tap and tell Doze your dream";
            micHint.textContent = on ? "Take your time. Pauses are fine." : "Speak naturally. Your words appear below.";
            if (!on) interim.textContent = "";
        };
        recognition.onresult = event => {
            let finalText = "", live = "";
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const piece = event.results[i][0].transcript;
                if (event.results[i].isFinal) finalText += piece; else live += piece;
            }
            if (finalText) {
                const spacer = content.value && !/\s$/.test(content.value) ? " " : "";
                content.value += spacer + finalText.trim().replace(/^./, c => c.toUpperCase()) + ". ";
            }
            interim.textContent = live ? "… " + live : "";
        };
        recognition.onend = () => setListening(false);
        recognition.onerror = event => {
            status.textContent = event.error === "not-allowed" ? "Allow microphone access to speak your dream." : "Microphone error: " + event.error;
            setListening(false);
        };
        mic.addEventListener("click", () => {
            if (listening) { recognition.stop(); return; }
            try { recognition.start(); setListening(true); status.textContent = ""; }
            catch (error) { status.textContent = "Couldn't start the microphone."; }
        });
    } else {
        mic.disabled = true;
        micLabel.textContent = "Voice isn't available in this browser";
        micHint.textContent = "Type your dream below instead (Chrome and Safari support voice).";
    }

    // ---- save ----
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!content.value.trim()) { status.textContent = "Tell Doze a little about your dream first."; return; }
        save.disabled = true;
        status.textContent = "Illustrating your dream…";
        try {
            const response = await fetch("/api/dreams", {
                method: "POST",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                credentials: "same-origin",
                body: JSON.stringify({title: title.value.trim(), content: content.value.trim()}),
            });
            if (response.status === 401) { window.location.href = "/login?next=/dream-journal"; return; }
            if (!response.ok) throw new Error("Couldn't save your dream. Try again.");
            const result = await response.json();
            status.textContent = `Saved “${result.title}”.`;
            window.location.reload();
        } catch (error) {
            status.textContent = error.message;
            save.disabled = false;
        }
    });

    // ---- delete ----
    document.querySelectorAll(".delete-entry").forEach(button => button.addEventListener("click", async () => {
        if (!window.confirm("Delete this dream? This can't be undone.")) return;
        const response = await fetch(`/api/dreams/${encodeURIComponent(button.dataset.dreamId)}`, {method: "DELETE", credentials: "same-origin"});
        if (response.ok) button.closest(".dream-card").remove();
        else status.textContent = "Couldn't delete that dream.";
    }));
});
