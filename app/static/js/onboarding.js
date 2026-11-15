/* Same-origin conversational setup. Secrets and model calls stay on the server. */
(() => {
    "use strict";
    const root = document.querySelector("[data-onboarding]");
    if (!root || !window.fetch) return;
    const form = root.querySelector("[data-chat-form]");
    const answer = root.querySelector("#onboarding-message");
    const send = root.querySelector("[data-chat-send]");
    const transcript = root.querySelector("#onboarding-conversation");
    const status = root.querySelector("[data-chat-status]");
    const errorBox = root.querySelector("[data-chat-error]");
    const errorText = root.querySelector("[data-chat-error-text]");
    const signIn = root.querySelector("[data-chat-signin]");
    const review = root.querySelector("[data-chat-review]");
    let state;
    try { state = JSON.parse(root.querySelector("#onboarding-state").textContent); }
    catch (_) { return; }
    const sendLabel = send.textContent;
    let pending = false;

    function messageElement(message) {
        const item = document.createElement("li");
        item.className = `flow-message ${message.role === "user" ? "flow-message-user" : "flow-message-assistant"}`;
        const speaker = document.createElement("span"); speaker.className = "flow-speaker";
        speaker.textContent = message.role === "user" ? "You" : "Doze";
        const content = document.createElement("p"); content.textContent = message.content;
        item.append(speaker, content);
        return item;
    }

    function render(next) {
        const previousMessages = Array.isArray(state.messages) ? state.messages : [];
        const nextMessages = Array.isArray(next.messages) ? next.messages : previousMessages;
        const prefixMatches = previousMessages.length <= nextMessages.length && previousMessages.every((message, index) => message.role === nextMessages[index].role && message.content === nextMessages[index].content);
        if (!prefixMatches) transcript.replaceChildren();
        const start = prefixMatches ? previousMessages.length : 0;
        nextMessages.slice(start).forEach(message => transcript.append(messageElement(message)));
        const draft = next.draft && typeof next.draft === "object" ? next.draft : state.draft || {};
        root.querySelectorAll("[data-draft-field]").forEach(field => {
            const key = field.dataset.draftField;
            let value = draft[key];
            if (value === null || value === undefined || value === "") value = "Not shared yet";
            else if (key === "morning_routine_min") value = `${value} min`;
            else if (key === "usual_bedtime" || key === "usual_wake") value = String(value).slice(0, 5);
            field.textContent = String(value);
        });
        const missing = Array.isArray(next.missing_fields) ? next.missing_fields : state.missing_fields || [];
        const count = Math.max(0, Math.min(5, 5 - missing.length));
        root.querySelector("[data-progress-text]").textContent = `${count} of 5 routine details collected`;
        root.querySelector("[data-profile-progress]").value = count;
        const ready = next.ready === true;
        review.hidden = !ready;
        root.querySelector("[data-profile-ready]").hidden = !ready;
        state = {...state, ...next, messages: nextMessages, draft, missing_fields: missing};
        transcript.scrollTop = transcript.scrollHeight;
    }

    function showError(message) {
        errorText.textContent = message;
        errorBox.hidden = false;
    }

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (pending || !form.reportValidity()) return;
        const message = answer.value.trim();
        if (!message) { answer.setCustomValidity("Write a short answer before sending."); answer.reportValidity(); return; }
        answer.setCustomValidity("");
        pending = true;
        send.disabled = true; answer.readOnly = true;
        form.setAttribute("aria-busy", "true");
        send.textContent = "Thinking…";
        status.textContent = "Doze is thinking about your answer…";
        errorBox.hidden = true;
        signIn.hidden = true;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 60000);
        try {
            const response = await fetch("/onboarding/message", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                body: JSON.stringify({message}), signal: controller.signal,
            });
            const isJson = (response.headers.get("content-type") || "").includes("application/json");
            if (!isJson) { signIn.hidden = false; throw new Error("Your session may have expired. Log in again to continue; your saved conversation will be waiting."); }
            const result = await response.json();
            if (Array.isArray(result.messages)) render(result);
            if (!response.ok) {
                if (response.status === 401) signIn.hidden = false;
                const detail = typeof result.error === "string" ? result.error : typeof result.detail === "string" ? result.detail : "Doze could not answer just now. Try again, or enter your routine yourself.";
                throw new Error(detail);
            }
            answer.value = "";
            status.textContent = result.ready ? "Your starting profile is ready to review." : "Your progress is saved.";
        } catch (error) {
            status.textContent = "Your answer is still here so you can retry.";
            showError(error.name === "AbortError" ? "That answer is taking longer than expected. Try again, or enter your routine yourself." : error.message || "The conversation could not be reached. Try again when you are ready.");
        } finally {
            window.clearTimeout(timeout);
            pending = false; send.disabled = false; answer.readOnly = false;
            form.removeAttribute("aria-busy"); send.textContent = sendLabel;
            answer.focus();
        }
    });
    answer.addEventListener("input", () => answer.setCustomValidity(""));
    render(state);
})();
