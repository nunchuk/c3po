// c3po reply-suggestion panel, injected into the WhisperLiveKit page.
// Builds a fixed right-side panel: an "About me" + "Meeting background" editor
// (saved server-side, collapsed once saved) and two reply cards -- a quick
// reply and a full answer, each with its Chinese translation -- streamed over a
// WebSocket, with question highlighting and a manual-refresh hotkey. The
// transcription UI stays WLK's own.

(function () {
  "use strict";

  // Card order and labels. The kinds match Suggester._update_card.
  const CARDS = [
    { kind: "quick", label: "QUICK REPLY · 快速应答" },
    { kind: "full", label: "FULL ANSWER · 完整回答" },
  ];

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "c3po-panel";
    panel.innerHTML = `
      <h2>💡 Reply suggestions</h2>
      <div id="c3po-context-summary" hidden>
        <span id="c3po-context-summary-text"></span>
        <button class="c3po-link" id="c3po-context-edit">Edit</button>
      </div>
      <div id="c3po-context-editor">
        <label for="c3po-me">About me <span class="c3po-note">kept across meetings</span></label>
        <textarea id="c3po-me" placeholder="Name, role, team, what you own, how you like to come across..."></textarea>
        <label for="c3po-meeting">Meeting background <span class="c3po-note">paste before each meeting</span></label>
        <textarea id="c3po-meeting" class="tall" placeholder="Agenda, participants, project history, open questions..."></textarea>
        <div id="c3po-context-row">
          <button class="c3po-btn" id="c3po-save">Save</button>
        </div>
      </div>
      <div id="c3po-status-row">
        <span id="c3po-status">connecting...</span>
        <button class="c3po-link" id="c3po-refresh">Refresh ⌘J</button>
      </div>
      <div id="c3po-question-flag">🔔 You may be asked something</div>
      <div id="c3po-cards"></div>
    `;
    document.body.appendChild(panel);

    // The two cards exist once and are updated in place: they stream several
    // times a second, and rebuilding them would flicker and drop a click.
    const container = panel.querySelector("#c3po-cards");
    for (const { kind, label } of CARDS) {
      const card = document.createElement("div");
      card.className = "c3po-card";
      card.dataset.kind = kind;
      card.hidden = true;
      card.innerHTML = `<div class="angle"></div><div class="text"></div><div class="zh"></div>`;
      card.querySelector(".angle").textContent = label;
      card.addEventListener("click", () => copyCard(card, card.querySelector(".text").textContent));
      container.appendChild(card);
    }
  }

  function setStatus(text) {
    const el = document.getElementById("c3po-status");
    if (el) el.textContent = text;
  }

  function renderCards(suggestions) {
    for (const s of suggestions) {
      const card = document.querySelector(`.c3po-card[data-kind="${s.kind}"]`);
      if (!card) continue;
      card.hidden = false;
      card.classList.toggle("streaming", !s.done);
      card.querySelector(".text").textContent = s.text || "";
      card.querySelector(".zh").textContent = s.zh || "";
    }
  }

  function copyCard(card, text) {
    navigator.clipboard.writeText(text).then(() => {
      card.classList.add("copied");
      setTimeout(() => card.classList.remove("copied"), 700);
    });
  }

  function connectSuggestions() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/suggestions`);
    ws.onopen = () => setStatus("listening for suggestions...");
    ws.onclose = () => {
      setStatus("disconnected, retrying...");
      setTimeout(connectSuggestions, 2000);
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type !== "suggestions") return;
      const cards = data.suggestions || [];
      renderCards(cards);
      const flag = document.getElementById("c3po-question-flag");
      if (flag) flag.classList.toggle("active", !!data.is_question);
      if (cards.length && cards.every((c) => c.done)) {
        setStatus(`updated ${new Date().toLocaleTimeString()}`);
      }
    };
  }

  // Once saved, the editor folds into one line: during the meeting it is dead
  // space, and the cards need the room.
  function showContextEditor(open) {
    const me = document.getElementById("c3po-me").value.trim();
    const meeting = document.getElementById("c3po-meeting").value.trim();
    const parts = [me ? "About me ✓" : "About me —"];
    parts.push(meeting ? `Meeting background (${meeting.length.toLocaleString()} chars)` : "No meeting background");
    document.getElementById("c3po-context-summary-text").textContent = parts.join(" · ");
    document.getElementById("c3po-context-editor").hidden = !open;
    document.getElementById("c3po-context-summary").hidden = open;
  }

  async function loadContext() {
    try {
      const saved = await (await fetch("/context")).json();
      document.getElementById("c3po-me").value = saved.me || "";
      document.getElementById("c3po-meeting").value = saved.meeting || "";
      showContextEditor(!(saved.me || saved.meeting));
    } catch (e) {
      report("could not load context: " + e);
    }
  }

  async function saveContext() {
    await fetch("/context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        me: document.getElementById("c3po-me").value,
        meeting: document.getElementById("c3po-meeting").value,
      }),
    });
    showContextEditor(false);
    setStatus("context saved");
  }

  async function forceRefresh() {
    await fetch("/refresh", { method: "POST" });
    setStatus("refreshing...");
  }

  // Floating-subtitle toggle, placed right after WLK's settings gear.
  //
  // Reuses WLK's own `.settings-toggle` class so it inherits the 40px circle,
  // the theme-aware colours and the `.active` state for free — no duplicated
  // styling to keep in sync.
  const OVERLAY_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960"' +
    ' width="24px" fill="currentColor"><path d="M240-400h280v-80H240v80Zm320 0h160v-80H560v80Z' +
    'M240-560h160v-80H240v80Zm240 0h240v-80H480v80ZM160-160q-33 0-56.5-23.5T80-240v-480q0-33 ' +
    '23.5-56.5T160-800h640q33 0 56.5 23.5T880-720v480q0 33-23.5 56.5T800-160H160Zm0-80h640v-480' +
    'H160v480Zm0 0v-480 480Z"/></svg>';

  function addOverlayButton() {
    const container = document.querySelector(".buttons-container");
    if (!container) {
      setTimeout(addOverlayButton, 300);
      return;
    }
    const btn = document.createElement("button");
    btn.id = "c3po-overlay-toggle";
    btn.className = "settings-toggle";
    btn.title = "Show/hide floating subtitles";
    btn.innerHTML = OVERLAY_ICON;
    container.appendChild(btn);

    const paint = (visible) => btn.classList.toggle("active", !!visible);

    btn.addEventListener("click", async () => {
      // Derive the next state from the button, then trust the server's reply —
      // the overlay may have been quit from its own right-click menu.
      const next = !btn.classList.contains("active");
      try {
        const res = await fetch("/overlay", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ visible: next }),
        });
        paint((await res.json()).visible);
      } catch (e) {
        report("overlay toggle failed: " + e);
      }
    });

    // Restore state after a page reload.
    fetch("/overlay")
      .then((r) => r.json())
      .then((s) => paint(s.visible))
      .catch(() => {});
  }

  // Power button: quit c3po entirely.
  //
  // Closing the browser only drops the WebSocket -- the server keeps running and
  // holds the port. Same `.settings-toggle` styling as the subtitle button.
  const POWER_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960"' +
    ' width="24px" fill="currentColor"><path d="M440-520v-320h80v320h-80ZM480-80' +
    'q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-80 29.5-151T192-756l57 57' +
    'q-44 44-66.5 100T160-480q0 134 93 227t227 93q134 0 227-93t93-227q0-63-22.5-119' +
    'T711-699l57-57q53 54 82.5 125T880-480q0 83-31.5 156T763-197q-54 54-127 85.5' +
    'T480-80Z"/></svg>';

  function addPowerButton() {
    const container = document.querySelector(".buttons-container");
    if (!container) {
      setTimeout(addPowerButton, 300);
      return;
    }
    const btn = document.createElement("button");
    btn.id = "c3po-power";
    btn.className = "settings-toggle";
    btn.title = "Quit c3po (stops the server)";
    btn.innerHTML = POWER_ICON;
    container.appendChild(btn);

    btn.addEventListener("click", async () => {
      // Confirm: this stops transcription mid-meeting, and the only way back is
      // relaunching the app and waiting ~80s for the model to load.
      if (!window.confirm("Quit c3po? This stops the server and the subtitles.")) {
        return;
      }
      btn.classList.add("active");
      btn.disabled = true;
      try {
        await fetch("/shutdown", { method: "POST" });
      } catch (e) {
        // A dropped connection here is the expected outcome, not a failure:
        // the server may finish exiting before the response arrives.
      }
      // The socket is gone, so no further server state is reachable. Say so in
      // the page rather than leaving a stale transcript that looks live.
      document.body.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;' +
        'height:100vh;font:600 18px -apple-system,sans-serif;color:#888;' +
        'text-align:center;line-height:1.6">c3po has stopped.<br>' +
        '<span style="font-weight:400;font-size:15px">You can close this tab. ' +
        'Launch C-3PO.app to start again.</span></div>';
    });
  }

  // Bridge to C-3PO.app, the native window. Its toolbar replaces the in-page
  // button row (hidden by panel.css), so it needs the buttons' state and a way
  // to press them. Pressing the existing buttons keeps WLK's and our own click
  // handlers the only code path. No-op in a browser: no "c3po" message handler.
  function connectNativeShell() {
    const handlers = window.webkit && window.webkit.messageHandlers;
    if (!handlers || !handlers.c3po) return;
    const rec = document.getElementById("recordButton");
    const gear = document.getElementById("settingsToggle");
    const overlay = document.getElementById("c3po-overlay-toggle");
    const timer = rec && rec.querySelector(".timer");
    if (!rec || !gear || !overlay || !timer) {
      setTimeout(connectNativeShell, 300);
      return;
    }

    // Waveform for the toolbar; WLK draws its own into the hidden record button.
    // `analyser` is WLK's top-level binding (live_transcription.js), visible to
    // this script, and null until recording starts. Each point is the extreme
    // sample of its slice, so speech reads as a waveform rather than noise.
    const WAVE_POINTS = 48;
    // Speech through BlackHole is quiet in time-domain terms: measured frame
    // peaks of ~0.10 of full scale for typical speech (p90), 0.39 at the loudest,
    // which drew a nearly flat line. Boost it, and soft-clip with tanh so loud
    // passages round off instead of flat-topping (0.10 -> 0.38, 0.39 -> 0.92).
    const WAVE_GAIN = 4;
    let waveTimer = null;
    const postWave = () => {
      if (typeof analyser === "undefined" || !analyser) return;
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(data);
      const step = data.length / WAVE_POINTS;
      const wave = [];
      for (let i = 0; i < WAVE_POINTS; i++) {
        let peak = 0;
        for (let j = Math.floor(i * step); j < Math.floor((i + 1) * step); j++) {
          const v = data[j] / 128 - 1;
          if (Math.abs(v) > Math.abs(peak)) peak = v;
        }
        wave.push(Math.tanh(peak * WAVE_GAIN));
      }
      handlers.c3po.postMessage({ wave });
    };

    // Record/Stop was pressed and WLK has not flipped state yet (it does so only
    // once the socket and microphone are up). Guards against a double start.
    let pending = false;
    let wasRecording = rec.classList.contains("recording");
    const post = () => {
      const recording = rec.classList.contains("recording");
      if (recording !== wasRecording) {
        wasRecording = recording;
        pending = false;
      }
      if (recording && !waveTimer) {
        waveTimer = setInterval(postWave, 50);
      } else if (!recording && waveTimer) {
        clearInterval(waveTimer);
        waveTimer = null;
      }
      handlers.c3po.postMessage({
        recording,
        pending,
        busy: rec.disabled,  // WLK disables it while the final audio drains
        timer: timer.textContent,
        settings: gear.classList.contains("active"),
        overlay: overlay.classList.contains("active"),
        panel: !document.body.classList.contains("c3po-panel-hidden"),
      });
    };
    const observer = new MutationObserver(post);
    for (const el of [rec, gear, overlay, document.body]) {
      observer.observe(el, { attributes: true, attributeFilter: ["class", "disabled"] });
    }
    observer.observe(timer, { childList: true, characterData: true, subtree: true });

    // Keep WLK's error messages visible: panel.css hides the status line in the
    // app, since the window subtitle shows the state. Every WLK error message
    // contains one of these words, and none of its routine ones do.
    const status = document.getElementById("status");
    if (status) {
      const markError = () => status.classList.toggle(
        "c3po-status-error", /error|invalid|disconnected|could not/i.test(status.textContent));
      new MutationObserver(markError).observe(
        status, { childList: true, characterData: true, subtree: true });
      markError();
    }

    window.c3poNative = {
      // Idempotent, so a toolbar acting on stale state (e.g. right after a page
      // reload) cannot start a recording by pressing "Stop".
      setRecording: (on) => {
        if (on === rec.classList.contains("recording")) return;
        if (pending || rec.disabled) return;
        pending = true;
        post();
        rec.click();
        // Starting can fail without the state ever flipping (e.g. microphone
        // denied); do not leave the toolbar disabled for good.
        setTimeout(() => {
          if (pending) {
            pending = false;
            post();
          }
        }, 5000);
      },
      toggleSettings: () => gear.click(),
      toggleOverlay: () => overlay.click(),
      togglePanel: () => document.body.classList.toggle("c3po-panel-hidden"),
    };
    post();
  }

  function wireEvents() {
    document.getElementById("c3po-save").addEventListener("click", saveContext);
    document
      .getElementById("c3po-context-edit")
      .addEventListener("click", () => showContextEditor(true));
    document
      .getElementById("c3po-refresh")
      .addEventListener("click", forceRefresh);
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        forceRefresh();
      }
    });
  }

  // Smooth "follow the latest line" auto-scroll.
  //
  // WLK calls `scrollTo({ behavior: "smooth" })` on every transcript update, and
  // updates arrive several times a second (plus one more each time an async
  // Bedrock translation lands). Native smooth scrolling restarts from scratch on
  // every call, so the animations keep interrupting each other and the result
  // reads as stepping rather than gliding.
  //
  // Instead we run ONE continuous rAF loop that eases the scroll position toward
  // the TOP every frame -- the transcript is newest-first (panel.css reverses
  // it). The animation in flight is never restarted, so motion stays fluid at
  // any update rate. Following pauses when the user scrolls down to read
  // history and resumes when they come back near the top. While paused, new
  // lines still land above what they are reading, so the loop shifts the
  // scroll position by the growth and the text under their eyes stays put.
  function tameScrolling() {
    const container = document.querySelector(".transcript-container");
    const lines = document.getElementById("linesTranscript");
    if (!container || !lines) {
      setTimeout(tameScrolling, 300);
      return;
    }

    const NEAR_TOP_PX = 120;
    // Fraction of the remaining distance covered per frame. Higher = snappier,
    // lower = more languid. 0.18 keeps up with speech without visible lag.
    const EASE = 0.18;
    // Below this many pixels, finish the move outright — easing the last
    // fraction of a pixel forever would look like a permanent shiver.
    const SNAP_PX = 0.5;

    let following = true;
    let selfScrolling = false;
    let lastHeight = container.scrollHeight;

    container.addEventListener(
      "scroll",
      () => {
        if (selfScrolling) return;  // ignore our own animation
        following = container.scrollTop <= NEAR_TOP_PX;
      },
      { passive: true }
    );

    function frame() {
      const grown = container.scrollHeight - lastHeight;
      lastHeight = container.scrollHeight;
      if (following) {
        const delta = -container.scrollTop;  // target is scrollTop 0
        if (Math.abs(delta) > SNAP_PX) {
          selfScrolling = true;
          container.scrollTop += delta * EASE;
          selfScrolling = false;
        } else if (delta !== 0) {
          selfScrolling = true;
          container.scrollTop = 0;
          selfScrolling = false;
        }
      } else if (grown !== 0) {
        selfScrolling = true;
        container.scrollTop += grown;
        selfScrolling = false;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);

    // Swallow WLK's own scrollTo: the loop above owns scroll position, and
    // letting native smooth animations run alongside it causes the stepping.
    // Defined on the element so it shadows Element.prototype.scrollTo whenever
    // WLK grabs its reference.
    Object.defineProperty(container, "scrollTo", {
      value: function () {},
      writable: false,
      configurable: false,
    });

    window.__c3poScrollPinned = true;
    report(
      "smooth follow active; container=" + container.className +
      " ease=" + EASE + " anchor=" + getComputedStyle(container).overflowAnchor
    );
  }

  // Send a diagnostic line to the server log (the browser console is not
  // reachable from the terminal, and AppleScript JS execution is disabled).
  function report(msg) {
    try {
      fetch("/clientlog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ msg: String(msg) }),
      });
    } catch (e) {
      /* diagnostics only */
    }
  }

  function init() {
    buildPanel();
    loadContext();
    addOverlayButton();
    addPowerButton();
    connectNativeShell();
    wireEvents();
    connectSuggestions();
    tameScrolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
