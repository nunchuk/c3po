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
