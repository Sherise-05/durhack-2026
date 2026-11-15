
document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("dream-form");
    const title = document.getElementById("dream-title");
    const content = document.getElementById("dream-content");
    const status = document.getElementById("dream-status");

    const recordBtn = document.getElementById("record-dream");
    const readBtn = document.getElementById("read-dream");
    const stopBtn = document.getElementById("stop-reading");

    // READ ALOUD
    function speak(text) {
        if (!("speechSynthesis" in window)) {
            status.textContent = "Text-to-speech is not supported.";
            return;
        }

        if (!text.trim()) {
            status.textContent = "Write something first.";
            return;
        }

        speechSynthesis.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "en-GB";
        utterance.rate = 0.9;

        speechSynthesis.speak(utterance);
    }

    readBtn.addEventListener("click", () => {
        speak(content.value);
    });

    stopBtn.addEventListener("click", () => {
        speechSynthesis.cancel();
    });

    // READ SAVED DREAMS
    document.querySelectorAll(".read-entry").forEach(button => {
        button.addEventListener("click", () => {
            speak(button.dataset.dreamContent || "");
        });
    });

    // SPEECH TO TEXT
    const SpeechRecognition =
        window.SpeechRecognition || window.webkitSpeechRecognition;

    if (SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.lang = "en-GB";
        recognition.continuous = true;
        recognition.interimResults = false;

        let recording = false;

        recognition.onresult = (event) => {
            let transcript = "";

            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    transcript += event.results[i][0].transcript + " ";
                }
            }

            content.value += transcript;
        };

        recognition.onend = () => {
            recording = false;
            recordBtn.textContent = "🎙 Start speaking";
        };

        recognition.onerror = (event) => {
            status.textContent = "Microphone error: " + event.error;
        };

        recordBtn.addEventListener("click", () => {
            if (recording) {
                recognition.stop();
            } else {
                try {
                    recognition.start();
                    recording = true;
                    recordBtn.textContent = "Stop speaking";
                } catch (error) {
                    status.textContent = "Could not start recording.";
                }
            }
        });
    } else {
        recordBtn.disabled = true;
        recordBtn.textContent = "Speech recognition unavailable";
    }

    // SAVE DREAM TO SQLITE THROUGH FASTAPI
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        status.textContent = "Saving...";

        try {
            const response = await fetch("/api/dreams", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    title: title.value.trim(),
                    content: content.value.trim()
                })
            });

            if (!response.ok) {
                throw new Error("Could not save dream.");
            }

            status.textContent = "Dream saved!";
            form.reset();

            window.location.reload();
        } catch (error) {
            status.textContent = error.message;
        }
    });
});
