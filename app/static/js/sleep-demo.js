
const ZOONIVERSE_URL =
  "https://www.zooniverse.org/projects/bcosentino/squirrelmapper/classify/workflow/30253";

const sleepButton = document.getElementById("sleep-now-btn");
const statusText = document.getElementById("sleep-demo-status");
const stopButton = document.getElementById("stop-demo-btn");

let taskWindow = null;
let alarmContext = null;
let alarmInterval = null;
let windowChecker = null;
let countdownTimer = null;

function setStatus(message) {
  statusText.textContent = message;
}

// Play a short repeated beep using the Web Audio API.
function beep() {
  if (!alarmContext || alarmContext.state !== "running") return;

  const oscillator = alarmContext.createOscillator();
  const gain = alarmContext.createGain();

  oscillator.type = "sine";
  oscillator.frequency.value = 750;

  gain.gain.setValueAtTime(0.0001, alarmContext.currentTime);
  gain.gain.exponentialRampToValueAtTime(
    0.15,
    alarmContext.currentTime + 0.02
  );
  gain.gain.exponentialRampToValueAtTime(
    0.0001,
    alarmContext.currentTime + 0.3
  );

  oscillator.connect(gain);
  gain.connect(alarmContext.destination);

  oscillator.start();
  oscillator.stop(alarmContext.currentTime + 0.3);
}

async function stopAlarm(message = "Alarm stopped. Good morning!") {
  clearInterval(alarmInterval);
  clearInterval(windowChecker);
  clearInterval(countdownTimer);

  alarmInterval = null;
  windowChecker = null;
  countdownTimer = null;

  if (alarmContext) {
    await alarmContext.close().catch(() => {});
    alarmContext = null;
  }

  sleepButton.disabled = false;
  stopButton.hidden = true;
  setStatus(message);
}

async function startAlarm() {
  setStatus("Wake up! Help researchers identify squirrels.");

  stopButton.hidden = false;

  // Navigate the tab opened during the original button click.
  if (taskWindow && !taskWindow.closed) {
    taskWindow.location.replace(ZOONIVERSE_URL);
    taskWindow.focus();
  } else {
    setStatus(
      "Your task tab was closed. Open it again to continue."
    );
    await stopAlarm("Demo stopped: task tab was closed.");
    return;
  }

  // Start the alarm sound.
  if (alarmContext.state === "suspended") {
    await alarmContext.resume().catch(() => {});
  }

  beep();
  alarmInterval = setInterval(beep, 650);

  // Check every 500 ms whether the task tab was closed.
  windowChecker = setInterval(() => {
    if (taskWindow.closed) {
      stopAlarm("Task tab closed! Alarm stopped.");
      taskWindow = null;
    }
  }, 500);
}

sleepButton.addEventListener("click", async () => {
  sleepButton.disabled = true;

  // Open immediately during the click to avoid pop-up blockers.
  taskWindow = window.open(
    "about:blank",
    "_blank"
  );

  if (!taskWindow) {
    sleepButton.disabled = false;
    setStatus(
      "Pop-up blocked. Please allow pop-ups for Doze."
    );
    return;
  }

  taskWindow.document.title = "Doze — Waking up soon";
  taskWindow.document.body.textContent =
    "Your science challenge will begin when the alarm rings.";

  // Create and resume audio during a real user interaction.
  const AudioContextClass =
    window.AudioContext || window.webkitAudioContext;

  if (!AudioContextClass) {
    taskWindow.close();
    sleepButton.disabled = false;
    setStatus("Your browser does not support Web Audio.");
    return;
  }

  alarmContext = new AudioContextClass();
  await alarmContext.resume().catch(() => {});

  let seconds = 3;
  setStatus(`Sleeping... ${seconds}`);

  countdownTimer = setInterval(async () => {
    seconds -= 1;

    if (seconds > 0) {
      setStatus(`Sleeping... ${seconds}`);
      return;
    }

    clearInterval(countdownTimer);
    countdownTimer = null;

    await startAlarm();
  }, 1000);
});

// Safety / accessibility escape hatch.
stopButton.addEventListener("click", () => {
  stopAlarm("Demo alarm manually stopped.");
});

// Clean up when leaving the dashboard.
window.addEventListener("pagehide", () => {
  clearInterval(alarmInterval);
  clearInterval(windowChecker);
  clearInterval(countdownTimer);

  if (alarmContext) {
    alarmContext.close().catch(() => {});
  }
});
