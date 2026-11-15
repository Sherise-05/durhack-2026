/* "Sleep now": count down to tonight's wake time, then ring and open a
   citizen-science task (Astha's sleep demo) that you have to get up and do.
   The task tab is opened during the tap so pop-up blockers allow it. */
const ZOONIVERSE_URL =
  "https://www.zooniverse.org/projects/bcosentino/squirrelmapper/classify/workflow/30253";

const box = document.querySelector("[data-sleep-now]");
const sleepButton = document.getElementById("sleep-now-btn");
const statusText = document.getElementById("sleep-demo-status");
const stopButton = document.getElementById("stop-demo-btn");
const demoButton = document.getElementById("demo-wake-btn");
const titleText = box ? box.querySelector("[data-sleep-title]") : null;
const subText = box ? box.querySelector("[data-sleep-sub]") : null;

let taskWindow = null;
let alarmContext = null;
let alarmInterval = null;
let windowChecker = null;
let countdownTimer = null;
let wakeAt = null;

const clock = new Intl.DateTimeFormat("en-GB", {timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});

function setStatus(message) {
  if (statusText) statusText.textContent = message;
}

function remaining(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// Play a short repeated beep using the Web Audio API.
function beep() {
  if (!alarmContext || alarmContext.state !== "running") return;
  const oscillator = alarmContext.createOscillator();
  const gain = alarmContext.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = 750;
  gain.gain.setValueAtTime(0.0001, alarmContext.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.15, alarmContext.currentTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, alarmContext.currentTime + 0.3);
  oscillator.connect(gain);
  gain.connect(alarmContext.destination);
  oscillator.start();
  oscillator.stop(alarmContext.currentTime + 0.3);
}

function resetButton() {
  box.dataset.state = "idle";
  titleText.textContent = "Sleep now";
  subText.textContent = `Doze wakes you at ${clock.format(new Date(box.dataset.alarm))}`;
  sleepButton.disabled = false;
  demoButton.hidden = true;
}

async function stopAlarm(message = "Alarm stopped. Good morning!") {
  clearInterval(alarmInterval);
  clearInterval(windowChecker);
  clearInterval(countdownTimer);
  alarmInterval = windowChecker = countdownTimer = null;
  if (alarmContext) {
    await alarmContext.close().catch(() => {});
    alarmContext = null;
  }
  if (taskWindow && !taskWindow.closed && box.dataset.state !== "ringing") taskWindow.close();
  taskWindow = null;
  stopButton.hidden = true;
  resetButton();
  setStatus(message);
}

async function startAlarm() {
  box.dataset.state = "ringing";
  titleText.textContent = "Wake up!";
  subText.textContent = "Help researchers identify squirrels to switch the alarm off";
  demoButton.hidden = true;
  setStatus("Wake up! Your task tab is open: help researchers identify squirrels.");
  const taskOpen = taskWindow && !taskWindow.closed;
  if (taskOpen) {
    taskWindow.focus();  // already on Zooniverse; bring it to the front
  } else {
    // The task tab was closed while sleeping: ring here and offer the link instead.
    setStatus("Wake up! Open the squirrel task, or tap “I'm awake: stop”.");
    statusText.insertAdjacentHTML("beforeend", ` <a href="${ZOONIVERSE_URL}" target="_blank" rel="noopener">Open the task</a>`);
  }
  if (alarmContext && alarmContext.state === "suspended") {
    await alarmContext.resume().catch(() => {});
  }
  // Speak a wake-up message (ElevenLabs) first, then start the beeps.
  if (window.DozeWakeVoice) {
    await window.DozeWakeVoice.play(alarmContext);
    if (box.dataset.state !== "ringing") return;  // stopped while it was speaking
  }
  beep();
  alarmInterval = setInterval(beep, 650);
  // The alarm stops when the task tab is closed.
  if (taskOpen) {
    windowChecker = setInterval(() => {
      if (!taskWindow || taskWindow.closed) {
        taskWindow = null;
        stopAlarm("Task done, alarm off. Good morning!");
      }
    }, 500);
  }
}

function tick() {
  const left = wakeAt - Date.now();
  if (left <= 0) {
    clearInterval(countdownTimer);
    countdownTimer = null;
    startAlarm();
    return;
  }
  titleText.textContent = remaining(left);
  subText.textContent = `until your ${clock.format(new Date(wakeAt))} alarm · sleep well`;
}

if (box && sleepButton) {
  sleepButton.addEventListener("click", async () => {
    if (box.dataset.state === "sleeping" || box.dataset.state === "ringing") return;
    sleepButton.disabled = true;

    // Open the Zooniverse task straight away, during the click, so pop-up blockers allow it.
    taskWindow = window.open(ZOONIVERSE_URL, "doze-wake-task");
    if (!taskWindow) {
      sleepButton.disabled = false;
      setStatus("Pop-ups are blocked. Allow pop-ups for Doze so the Zooniverse task can open.");
      return;
    }

    // Create and resume audio during a real user interaction.
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      alarmContext = new AudioContextClass();
      await alarmContext.resume().catch(() => {});
    }
    // Fetch the spoken wake-up message now, while the tap has unlocked audio.
    if (window.DozeWakeVoice) window.DozeWakeVoice.prepare(alarmContext, "sleep");

    wakeAt = Date.parse(box.dataset.alarm);
    while (wakeAt <= Date.now()) wakeAt += 24 * 3600 * 1000; // pressed after today's wake time
    const demoSeconds = Number(document.body.dataset.demoTimer || 0);
    if (demoSeconds > 0) wakeAt = Date.now() + demoSeconds * 1000;  // demo: ring soon
    box.dataset.state = "sleeping";
    sleepButton.disabled = false;
    stopButton.hidden = false;
    demoButton.hidden = demoSeconds > 0;  // the demo timer already rings soon
    setStatus(demoSeconds > 0
      ? `Demo timer: the alarm rings in ${demoSeconds} seconds. Zooniverse is open in a new tab.`
      : "Zooniverse is open in a new tab. Keep this Doze tab open too, so the alarm can ring.");
    tick();
    countdownTimer = setInterval(tick, 1000);
  });

  // For presenting: skip ahead so the alarm rings in 10 seconds.
  demoButton.addEventListener("click", () => {
    wakeAt = Date.now() + 10000;
    tick();
  });

  // Safety / accessibility escape hatch.
  stopButton.addEventListener("click", () => {
    stopAlarm("Alarm cancelled.");
  });

  // Clean up when leaving the dashboard.
  window.addEventListener("pagehide", () => {
    clearInterval(alarmInterval);
    clearInterval(windowChecker);
    clearInterval(countdownTimer);
    if (alarmContext) alarmContext.close().catch(() => {});
  });
}
