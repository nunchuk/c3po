// c3po reply-suggestion panel, injected into the WhisperLiveKit page.
// Builds a fixed right-side panel: pre-meeting context editor + always-on
// reply suggestions streamed over a WebSocket, with question highlighting and
// a manual-refresh hotkey. The transcription UI stays WLK's own.

(function () {
  "use strict";

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "c3po-panel";
    panel.innerHTML = `
      <h2>💡 Reply suggestions</h2>
      <textarea id="c3po-context" placeholder="Pre-meeting context: topic, project background, your role, key terms..."></textarea>
      <div id="c3po-context-row">
        <button class="c3po-btn" id="c3po-save">Save context</button>
        <button class="c3po-btn secondary" id="c3po-refresh">Refresh (⌘J)</button>
      </div>
      <div id="c3po-status">connecting...</div>
      <div id="c3po-question-flag">🔔 You may be asked something</div>
      <div id="c3po-cards"></div>
    `;
    document.body.appendChild(panel);
  }

  function setStatus(text) {
    const el = document.getElementById("c3po-status");
    if (el) el.textContent = text;
  }

  function renderCards(suggestions) {
    const container = document.getElementById("c3po-cards");
    if (!container) return;
    container.innerHTML = "";
    // Short replies first, the full-answer script last (visually distinct).
    const ordered = [...suggestions].sort(
      (a, b) => (a.kind === "long" ? 1 : 0) - (b.kind === "long" ? 1 : 0)
    );
    ordered.forEach((s) => {
      const card = document.createElement("div");
      card.className = "c3po-card";
      // Title combines the English angle with the Chinese hint, e.g.
      // "BUY TIME · 争取时间". Same layout for every card (short and long).
      const en = s.kind === "long" ? "FULL ANSWER" : (s.angle || "REPLY");
      const title = escapeHtml(en).toUpperCase() +
        (s.hint_zh ? ` · ${escapeHtml(s.hint_zh)}` : "");
      card.innerHTML =
        `<div class="angle">${title}</div><div class="text">${escapeHtml(s.text)}</div>`;
      card.addEventListener("click", () => copyCard(card, s.text));
      container.appendChild(card);
    });
  }

  function copyCard(card, text) {
    navigator.clipboard.writeText(text).then(() => {
      card.classList.add("copied");
      setTimeout(() => card.classList.remove("copied"), 700);
    });
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
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
      renderCards(data.suggestions || []);
      const flag = document.getElementById("c3po-question-flag");
      if (flag) flag.classList.toggle("active", !!data.is_question);
      const stamp = new Date().toLocaleTimeString();
      setStatus(`updated ${stamp}`);
    };
  }

  async function saveContext() {
    const ctx = document.getElementById("c3po-context").value;
    await fetch("/context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context: ctx }),
    });
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

  function wireEvents() {
    document.getElementById("c3po-save").addEventListener("click", saveContext);
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
  // the bottom every frame. New content just moves the target; the animation in
  // flight is never restarted, so motion stays fluid at any update rate.
  // Following pauses when the user scrolls up to read history and resumes when
  // they come back near the bottom.
  function tameScrolling() {
    const container = document.querySelector(".transcript-container");
    const lines = document.getElementById("linesTranscript");
    if (!container || !lines) {
      setTimeout(tameScrolling, 300);
      return;
    }

    const NEAR_BOTTOM_PX = 120;
    // Fraction of the remaining distance covered per frame. Higher = snappier,
    // lower = more languid. 0.18 keeps up with speech without visible lag.
    const EASE = 0.18;
    // Below this many pixels, finish the move outright — easing the last
    // fraction of a pixel forever would look like a permanent shiver.
    const SNAP_PX = 0.5;

    let following = true;
    let selfScrolling = false;

    container.addEventListener(
      "scroll",
      () => {
        if (selfScrolling) return;  // ignore our own animation
        const distanceFromBottom =
          container.scrollHeight - container.scrollTop - container.clientHeight;
        following = distanceFromBottom <= NEAR_BOTTOM_PX;
      },
      { passive: true }
    );

    function frame() {
      if (following) {
        const target = container.scrollHeight - container.clientHeight;
        const delta = target - container.scrollTop;
        if (Math.abs(delta) > SNAP_PX) {
          selfScrolling = true;
          container.scrollTop += delta * EASE;
          selfScrolling = false;
        } else if (delta !== 0) {
          selfScrolling = true;
          container.scrollTop = target;
          selfScrolling = false;
        }
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
    addOverlayButton();
    addPowerButton();
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
