/* ScreenToStream Concierge — Alexa+ simulator UI.
   This page is only a client. Every answer is produced by the MCP server,
   over the same Streamable HTTP endpoint and OAuth bearer token Alexa+ uses. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STORAGE_KEY = "s2s_demo_v1";
const state = {
  bootstrap: null,
  sessionId: null,
  tripId: null,
  messages: [],
  trace: [],
  scene: null,
  busy: false,
  muted: false,
  linked: false,
  linkedUser: null,
  streamStartedAt: 0,
  overlayTimer: null,
  recognition: null,
  recording: false,
};

const els = {
  sceneArt: $("#sceneArt"),
  tvScreen: $("#tvScreen"),
  tvShow: $("#tvShow"),
  tvTime: $("#tvTime"),
  tvSceneLabel: $("#tvSceneLabel"),
  tvSceneTitle: $("#tvSceneTitle"),
  tvSceneSummary: $("#tvSceneSummary"),
  tvDish: $("#tvDish"),
  tvDest: $("#tvDest"),
  tvMood: $("#tvMood"),
  tvProgress: $("#tvProgress"),
  tvOverlay: $("#tvOverlay"),
  overlayContent: $("#overlayContent"),
  overlayKicker: $("#overlayKicker"),
  overlayClose: $("#overlayClose"),
  sceneThumbs: $("#sceneThumbs"),
  chat: $("#chat"),
  chatEmpty: $("#chatEmpty"),
  promptChips: $("#promptChips"),
  chatInput: $("#chatInput"),
  sendBtn: $("#sendBtn"),
  micBtn: $("#micBtn"),
  statusLine: $("#statusLine"),
  latencyLine: $("#latencyLine"),
  tracePanel: $("#tracePanel"),
  traceList: $("#traceList"),
  traceSummary: $("#traceSummary"),
  maxLatency: $("#maxLatency"),
  traceToggle: $("#traceToggle"),
  agentBadge: $("#agentBadge"),
  mcpChip: $("#mcpChip"),
  linkBtn: $("#linkBtn"),
  linkLabel: $("#linkLabel"),
  footerDot: $("#footerDot"),
  footerStatus: $("#footerStatus"),
  providerReasoning: $("#providerReasoning"),
  providerVision: $("#providerVision"),
  footerEndpoint: $("#footerEndpoint"),
  frameInput: $("#frameInput"),
  analyzing: $("#analyzing"),
  toasts: $("#toasts"),
  playDemoBtn: $("#playDemoBtn"),
  ribbonText: $("#ribbonText"),
  resetBtn: $("#resetBtn"),
  muteBtn: $("#muteBtn"),
};

/* ------------------------------------------------------------------ utilities */

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(cents) {
  if (cents == null || Number.isNaN(Number(cents))) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(cents) / 100);
}

function clock() {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function cleanSpeech(text) {
  return String(text || "")
    .replace(/[*_`#>•]/g, " ")
    .replace(/[“”]/g, '"')
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 480);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error_description || payload.error || detail;
    } catch {
      /* ignore non-JSON errors */
    }
    throw new Error(detail);
  }
  return response.json();
}

function toast(message, { bad = false, title = "" } = {}) {
  const node = document.createElement("div");
  node.className = `toast${bad ? " bad" : ""}`;
  node.innerHTML = `<span class="toast-icon">${bad ? "!" : "✓"}</span><div>${
    title ? `<b>${escapeHtml(title)}</b><br>` : ""
  }${escapeHtml(message)}</div>`;
  els.toasts.append(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 250);
  }, 5200);
}

/* ------------------------------------------------------------------ persistence */

function saveState() {
  try {
    const payload = {
      sessionId: state.sessionId,
      tripId: state.tripId,
      messages: state.messages.slice(-14),
      trace: state.trace.slice(-14),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    /* localStorage can be unavailable in private windows */
  }
}

function restoreState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    state.sessionId = saved.sessionId || null;
    state.tripId = saved.tripId || null;
    state.messages = Array.isArray(saved.messages) ? saved.messages : [];
    state.trace = Array.isArray(saved.trace) ? saved.trace : [];
  } catch {
    state.messages = [];
  }
}

/* ------------------------------------------------------------------ TV scene */

function sceneViewModel(scene) {
  const destination = scene.destination || {};
  const visual = scene.visual || {};
  return {
    id: scene.id || scene.scene_id,
    show: scene.show || "Fire TV",
    episode: scene.episode || "",
    title: scene.scene_title || scene.title || "A moment worth booking",
    summary: scene.summary || "",
    dish: scene.dish || "",
    timestamp: scene.timestamp_label || scene.timestamp || "",
    seconds: scene.timestamp_seconds || 0,
    destination: destination.name || "",
    destinationId: destination.id || "",
    emoji: visual.emoji || destination.emoji || "✦",
    palette: visual.palette || destination.palette || [],
    mood: visual.mood || "",
    location: visual.location_label || destination.name || "",
    image: destination.image || scene.hero_image || "/static/assets/kyoto.svg",
    anchors: scene.anchors || {},
  };
}

function applyScene(scene, { animate = true } = {}) {
  if (!scene) return;
  const view = sceneViewModel(scene);
  state.scene = view;

  const swap = () => {
    els.sceneArt.src = view.image;
    els.sceneArt.alt = `Stylized ${view.location || view.destination || "travel"} scene from ${view.show}`;
    els.tvShow.textContent = view.show;
    els.tvTime.textContent = `Fire TV · ${view.timestamp || view.episode || "LIVE"}`;
    els.tvSceneLabel.textContent = [view.location, view.timestamp].filter(Boolean).join(" · ");
    els.tvSceneTitle.textContent = view.title;
    els.tvSceneSummary.textContent = view.summary;
    els.tvDish.textContent = `${view.emoji} ${view.dish}`;
    els.tvDest.textContent = `${view.emoji} ${view.destination || "Featured"}`;
    els.tvMood.textContent = view.mood;
    const progress = Math.max(12, Math.min(94, view.seconds ? (view.seconds % 1800) / 18 : 34));
    els.tvProgress.style.width = `${progress}%`;
    els.ribbonText.textContent = `“Alexa, book a weekend stay in ${view.destination || "this place"}.”`;
    markActiveThumb(view.id);
    if (animate) {
      els.tvScreen.classList.add("changing");
      setTimeout(() => els.tvScreen.classList.remove("changing"), 120);
    }
  };

  if (animate && view.image) {
    const preload = new Image();
    preload.onload = swap;
    preload.onerror = swap;
    preload.src = view.image;
    setTimeout(() => {
      if (!preload.complete) swap();
    }, 180);
  } else {
    swap();
  }
}

function markActiveThumb(sceneId) {
  $$(".scene-thumb").forEach((button) => {
    button.classList.toggle("active", button.dataset.sceneId === sceneId);
  });
}

function renderSceneThumbs(scenes) {
  els.sceneThumbs.innerHTML = "";
  scenes.forEach((scene) => {
    const button = document.createElement("button");
    button.className = "scene-thumb";
    button.dataset.sceneId = scene.id;
    button.type = "button";
    button.title = `${scene.show} — ${scene.scene_title}`;
    button.setAttribute("role", "option");
    const image = scene.destination?.image || scene.hero_image || "/static/assets/kyoto.svg";
    const emoji = scene.visual?.emoji || scene.destination?.emoji || "✦";
    button.innerHTML = `<img src="${escapeHtml(image)}" alt="" loading="lazy" />
      <em>${escapeHtml(emoji)}</em><span>${escapeHtml(scene.show)}</span>`;
    button.addEventListener("click", () => switchScene(scene));
    els.sceneThumbs.append(button);
  });
  if (state.scene?.id) markActiveThumb(state.scene.id);
}

async function switchScene(scene) {
  if (state.busy) return;
  applyScene(scene);
  markActiveThumb(scene.id);
  try {
    const data = await api("/api/viewing/start", {
      method: "POST",
      body: JSON.stringify({
        show: scene.show,
        episode: scene.episode,
        position_seconds: scene.timestamp_seconds || 0,
        source: "firetv",
      }),
    });
    applyScene(data.scene);
    toast(`${scene.show} is now playing on your Fire TV.`, { title: "Viewing context updated" });
    const c = data.confidence != null ? ` · ${Math.round(data.confidence * 100)}% match` : "";
    addSystemTurn(`Fire TV context: ${scene.show} — ${scene.scene_title}${c}. Ask “book a weekend here” to turn it into a trip.`);
  } catch (error) {
    toast(error.message, { bad: true, title: "Could not change the scene" });
  }
}

/* ------------------------------------------------------------------ TV overlay */

function hideTvOverlay() {
  clearTimeout(state.overlayTimer);
  els.tvOverlay.classList.remove("show");
  els.tvOverlay.setAttribute("aria-hidden", "true");
  els.tvScreen.classList.remove("has-overlay");
}

function scheduleOverlayHide(ms = 16000) {
  clearTimeout(state.overlayTimer);
  state.overlayTimer = setTimeout(hideTvOverlay, ms);
}

function showTvCard(envelope) {
  const card = envelope?.payload?.payload || {};
  const kind = envelope?.payload?.card || card.type || "itinerary";
  let html = "";
  let kicker = "Sent to your Fire TV";

  if (kind === "booking" || card.type === "booking") {
    kicker = "Booking confirmed · on your TV";
    const details = card.details || {};
    const dates = card.dates?.label || details.dates?.label || "Your dates";
    const title = card.title || details.title || "Your trip is booked";
    const total = card.total_label || details.total_label || "";
    html = `
      <h2 class="tv-card-title">${escapeHtml(title)}</h2>
      <p class="tv-card-sub">${escapeHtml(dates)}${total ? ` · ${escapeHtml(total)}` : ""}</p>
      <div class="booking-big">${escapeHtml(card.confirmation_code || "S2S")}</div>
      <div class="tv-card-grid">
        <div class="tv-stat"><span>Confirmation</span><strong>${escapeHtml(card.confirmation_code || "—")}</strong></div>
        <div class="tv-stat"><span>Status</span><strong>${escapeHtml(card.status || "confirmed")}</strong></div>
        <div class="tv-stat"><span>Total</span><strong>${escapeHtml(total || "—")}</strong></div>
      </div>
      <div class="tv-card-foot"><span>Simulated purchase · no payment taken</span><b>ScreenToStream</b></div>`;
  } else if (kind === "quote" || card.type === "quote") {
    kicker = "Quote sent to your Fire TV";
    html = `
      <h2 class="tv-card-title">${escapeHtml(card.title || "Your held quote")}</h2>
      <p class="tv-card-sub">Held for ${escapeHtml(String(card.hold_minutes || 15))} minutes · simulated checkout</p>
      <div class="tv-card-grid">
        <div class="tv-stat"><span>Total</span><strong>${escapeHtml(card.total_label || "—")}</strong></div>
        <div class="tv-stat"><span>Line items</span><strong>${escapeHtml(String(card.line_items?.length || 0))}</strong></div>
        <div class="tv-stat"><span>Payment</span><strong>Simulated</strong></div>
      </div>
      <div class="tv-card-foot"><span>Say “book it” on your Echo Show to confirm</span><b>Alexa+</b></div>`;
  } else {
    const stay = card.stay || {};
    const budget = card.budget || {};
    const days = (card.days || []).slice(0, 3);
    const dayHtml = days
      .map(
        (day) => `<div class="tv-day"><b>Day ${escapeHtml(String(day.day))}</b><span>${escapeHtml(
          day.title || day.date || ""
        )}</span></div>`
      )
      .join("");
    html = `
      <h2 class="tv-card-title">${escapeHtml(card.title || "Your weekend, planned")}</h2>
      <p class="tv-card-sub">${escapeHtml(card.subtitle || "")}</p>
      <div class="tv-card-grid">
        <div class="tv-stat"><span>Stay</span><strong>${escapeHtml(stay.name || "—")}</strong></div>
        <div class="tv-stat"><span>Total</span><strong>${escapeHtml(budget.total_label || "—")}</strong></div>
        <div class="tv-stat"><span>Dates</span><strong>${escapeHtml(card.subtitle?.split("·")[1]?.trim() || "—")}</strong></div>
      </div>
      <div class="tv-day-row">${dayHtml}</div>
      <div class="tv-card-foot"><span>Want it cheaper, or should I quote it?</span><b>On your Fire TV</b></div>`;
  }

  els.overlayKicker.textContent = kicker;
  els.overlayContent.innerHTML = html;
  els.tvOverlay.setAttribute("aria-hidden", "false");
  els.tvOverlay.classList.add("show");
  els.tvScreen.classList.add("has-overlay");
  scheduleOverlayHide();
  toast("Alexa+ sent a card to the Fire TV.", { title: "Multi-device" });
}

/* ------------------------------------------------------------------ cards */

function actionButton(label, utterance, style = "") {
  return `<button class="btn ${style}" data-say="${escapeHtml(utterance || label)}">${escapeHtml(label)}</button>`;
}

function renderCard(card) {
  if (!card || typeof card !== "object") return "";
  if (card.type === "scene" || card.type === "tv_sent") return "";

  if (card.type === "trip") {
    const stay = card.stay || {};
    const budget = card.budget || {};
    const days = card.days || [];
    const visual = card.visual || {};
    const hero = visual.hero_image || card.hero_image || "/static/assets/kyoto.svg";
    const eyebrow = visual.eyebrow || card.subtitle?.split("·")[0]?.trim() || "Your trip";
    const headline = visual.headline || card.title || "Your trip";
    const subhead = visual.subhead || card.subtitle || "";
    const badges = (visual.badges?.length ? visual.badges : card.badges) || [];
    const why = (visual.why?.length ? visual.why[0] : (card.why_this || [])[0]) || "";
    const accent = /^#[0-9a-f]{6}$/i.test(visual.accent || "") ? visual.accent : "";
    const dayHtml = days
      .map(
        (day) => `
        <div class="day-block">
          <div class="day-title"><b>Day ${escapeHtml(String(day.day))}</b><span>${escapeHtml(day.title || day.date || "")}</span></div>
          ${(day.items || [])
            .slice(0, 4)
            .map(
              (item) =>
                `<div class="day-item"><time>${escapeHtml(item.time || "—")}</time><span>${escapeHtml(
                  item.title || ""
                )}</span></div>`
            )
            .join("")}
        </div>`
      )
      .join("");
    return `
      <article class="mcp-card"${accent ? ` style="--card-accent:${accent}"` : ""}>
        <div class="card-trip-hero" style="background-image:url('${escapeHtml(hero)}')">
          <div class="card-trip-head">
            <span>${escapeHtml(eyebrow)}</span>
            <h3>${escapeHtml(headline)}</h3>
          </div>
        </div>
        <div class="card-body">
          <p class="card-sub">${escapeHtml(subhead)}</p>
          <div class="badges">${badges.map((b) => `<span>${escapeHtml(b)}</span>`).join("")}</div>
          ${why ? `<p class="card-why">${escapeHtml(why)}</p>` : ""}
          <div class="card-stay">
            <div class="stay-thumb">⌂</div>
            <div class="stay-info"><b>${escapeHtml(stay.name || "Selected stay")}</b><span>${escapeHtml(
              [stay.kind, stay.neighborhood].filter(Boolean).join(" · ") || "Curated by the concierge"
            )}</span></div>
            <div class="stay-price"><b>${escapeHtml(stay.price_label || budget.total_label || "")}</b><span>${escapeHtml(
              stay.rating ? `${stay.rating}★ · ${stay.reviews || 0} reviews` : "vetted"
            )}</span></div>
          </div>
          <div class="card-budget"><span>${escapeHtml(budget.total_label || "—")} all-in</span><span>${escapeHtml(
            budget.per_person_label || ""
          )}</span></div>
        </div>
        ${
          days.length
            ? `<details class="card-days"><summary>${days.length}-day itinerary</summary>${dayHtml}</details>`
            : ""
        }
        <div class="card-actions">
          ${actionButton("Make it cheaper", "Make it cheaper")}
          ${actionButton("Quote it", "Quote it")}
          ${actionButton("Send to TV", "Send the itinerary to my TV")}
          ${actionButton("Book it", "Book it", "btn-amber")}
        </div>
      </article>`;
  }

  if (card.type === "quote") {
    const rows = (card.line_items || [])
      .map(
        (item) =>
          `<div class="quote-row"><span>${escapeHtml(item.label || "")}</span><b>${escapeHtml(
            money(item.amount_cents)
          )}</b></div>`
      )
      .join("");
    return `
      <article class="mcp-card">
        <div class="quote-head"><b>${escapeHtml(card.title || "Your quote")}</b><span>Held ${escapeHtml(
          String(card.hold_minutes || 15)
        )} min</span></div>
        <div class="quote-lines">${rows}</div>
        <div class="quote-total"><span>Total · simulated checkout</span><b>${escapeHtml(card.total_label || "—")}</b></div>
        <div class="card-actions">
          ${actionButton("Book it", "Book it", "btn-amber")}
          ${actionButton("Make it cheaper", "Make it cheaper")}
        </div>
      </article>`;
  }

  if (card.type === "booking") {
    const dates = card.dates?.label || "Your dates";
    const bookingId = card.booking_id || card.id || "";
    return `
      <article class="mcp-card booking-card">
        <div class="booking-banner"><span class="check">✓</span> Simulated booking confirmed</div>
        <div class="booking-code"><span>Confirmation</span><b>${escapeHtml(card.confirmation_code || "—")}</b></div>
        <div class="booking-detail"><b>${escapeHtml(card.title || "Your trip")}</b><br>${escapeHtml(dates)} · ${escapeHtml(
          card.total_label || ""
        )}<br><span style="color:var(--muted-2)">No payment was taken. This is a demo booking.</span></div>
        <div class="card-actions">
          ${
            bookingId
              ? `<a class="btn" href="/api/bookings/${encodeURIComponent(
                  bookingId
                )}/calendar.ics" download>Add to calendar</a>`
              : ""
          }
          ${actionButton("Send to TV", "Send the booking to my Fire TV")}
        </div>
      </article>`;
  }

  if (card.type === "dish") {
    return `
      <article class="mcp-card">
        <div class="dish-top">
          <div class="dish-emoji">${escapeHtml(card.image || "🍽")}</div>
          <div class="dish-head">
            <span>${escapeHtml(card.destination || "From the scene")}</span>
            <h3>${escapeHtml(card.title || card.dish || "The dish")}</h3>
            <p>${escapeHtml(card.origin || "")}</p>
          </div>
        </div>
        <div class="dish-flavors">${(card.flavor_notes || [])
          .slice(0, 5)
          .map((note) => `<span>${escapeHtml(note)}</span>`)
          .join("")}</div>
        <details class="card-days" open>
          <summary>Recipe &amp; pairing</summary>
          <div class="dish-section"><h4>Ingredients</h4><ul>${(card.ingredients || [])
            .slice(0, 8)
            .map((item) => `<li>${escapeHtml(item)}</li>`)
            .join("")}</ul></div>
          <div class="dish-section"><h4>Method</h4><ol>${(card.steps || [])
            .slice(0, 6)
            .map((item) => `<li>${escapeHtml(item)}</li>`)
            .join("")}</ol></div>
          <div class="dish-pairing"><b>Pair it with</b> ${escapeHtml(card.pairing || "something cold")}</div>
        </details>
      </article>`;
  }

  if (card.type === "planning") {
    return `
      <article class="mcp-card planning-card">
        <div class="planning-top">
          <div class="planning-spinner"></div>
          <div><b>Building the trip</b><span>Checking stays, tables, and the good stuff in between.</span></div>
        </div>
        <div class="skeleton-line w80"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line w60"></div>
      </article>`;
  }

  if (card.type === "confirmation") {
    return `
      <article class="mcp-card">
        <div class="quote-head"><b>Confirm the booking</b><span>Simulated</span></div>
        <div class="quote-total"><span>${escapeHtml(card.quote?.total_label || "Your held quote")}</span><b>Say “book it”</b></div>
        <div class="card-actions">${actionButton("Book it", "Book it", "btn-amber")}</div>
      </article>`;
  }

  return "";
}

function renderCards(cards) {
  if (!cards || !cards.length) return "";
  return `<div class="cards">${cards.map(renderCard).join("")}</div>`;
}

/* ------------------------------------------------------------------ chat */

function hideEmpty() {
  els.chat.classList.add("has-messages");
  if (els.chatEmpty) els.chatEmpty.setAttribute("aria-hidden", "true");
}

function renderMessage(message, { animate = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = `turn ${message.role}`;
  if (!animate) wrap.style.animation = "none";

  if (message.role === "user") {
    wrap.innerHTML = `<div class="bubble">${escapeHtml(message.text)}</div>`;
  } else if (message.role === "system") {
    wrap.className = "turn user";
    wrap.innerHTML = `<div class="bubble" style="border-color:var(--line);background:rgba(255,255,255,.03);color:var(--muted);font-size:10.5px">${escapeHtml(
      message.text
    )}</div>`;
  } else {
    wrap.innerHTML = `
      <div class="assistant-avatar" aria-hidden="true"></div>
      <div class="turn-body">
        <div class="speaker"><b>Alexa+</b><span>${escapeHtml(message.meta || "")}</span><time>${escapeHtml(
          message.time || clock()
        )}</time></div>
        <div class="assistant-bubble">${escapeHtml(message.text)}</div>
        ${renderCards(message.cards)}
      </div>`;
  }
  els.chat.append(wrap);
  hideEmpty();
  scrollChat();
  return wrap;
}

function scrollChat(force = false) {
  const nearBottom = els.chat.scrollHeight - els.chat.scrollTop - els.chat.clientHeight < 180;
  if (force || nearBottom) {
    requestAnimationFrame(() => {
      els.chat.scrollTop = els.chat.scrollHeight;
    });
  }
}

function addUserTurn(text) {
  const message = { role: "user", text, time: clock() };
  state.messages.push(message);
  renderMessage(message, { animate: true });
  saveState();
}

function addAssistantTurn(text, cards = [], meta = "") {
  const message = { role: "assistant", text, cards, meta, time: clock() };
  state.messages.push(message);
  renderMessage(message, { animate: true });
  saveState();
  return message;
}

function addSystemTurn(text) {
  const message = { role: "system", text, time: clock() };
  state.messages.push(message);
  renderMessage(message, { animate: true });
  scrollChat(true);
  setTimeout(() => scrollChat(true), 180);
  saveState();
}

function rerenderTranscript() {
  els.chat.innerHTML = "";
  renderTranscript();
}

function applyVisualCard(display) {
  if (!display || display.type !== "trip") return false;
  let updated = false;
  for (const message of state.messages) {
    if (!Array.isArray(message.cards)) continue;
    const index = message.cards.findIndex(
      (card) => card?.type === "trip" && (!display.trip_id || card.trip_id === display.trip_id)
    );
    if (index >= 0) {
      message.cards[index] = { ...message.cards[index], ...display };
      updated = true;
    }
  }
  if (updated) {
    saveState();
    rerenderTranscript();
  }
  return updated;
}

function renderTranscript() {
  if (!state.messages.length) {
    els.chat.classList.remove("has-messages");
    return;
  }
  hideEmpty();
  state.messages.forEach((message) => renderMessage(message));
  renderTrace(state.trace, { quiet: true });
  if (state.trace.length) {
    const max = Math.max(...state.trace.map((item) => Number(item.ms) || 0));
    els.latencyLine.textContent = `${max} ms max tool · restored trace`;
    els.latencyLine.className = `latency ${max < 500 ? "good" : max < 1500 ? "warn" : ""}`;
  }
  scrollChat(true);
  setTimeout(() => scrollChat(true), 220);
}

function showTyping() {
  const node = document.createElement("div");
  node.className = "turn assistant";
  node.id = "typingTurn";
  node.innerHTML = `
    <div class="assistant-avatar" aria-hidden="true"></div>
    <div class="turn-body">
      <div class="speaker"><b>Alexa+</b><span>thinking</span><time>${clock()}</time></div>
      <div class="typing"><i></i><i></i><i></i></div>
    </div>`;
  els.chat.append(node);
  hideEmpty();
  scrollChat(true);
  return node;
}

/* ------------------------------------------------------------------ trace */

function renderTrace(trace, { quiet = false } = {}) {
  state.trace = Array.isArray(trace) ? trace : [];
  if (!state.trace.length) {
    els.traceList.innerHTML =
      '<div class="trace-empty">The agent\'s tool calls, arguments, payload summaries and timings will stream in here.</div>';
    els.traceSummary.textContent = "No calls yet";
    els.maxLatency.textContent = "—";
    els.maxLatency.className = "latency-chip";
    return;
  }

  const total = state.trace.reduce((sum, item) => sum + (Number(item.ms) || 0), 0);
  const max = Math.max(...state.trace.map((item) => Number(item.ms) || 0));
  els.traceSummary.textContent = `${state.trace.length} call${state.trace.length === 1 ? "" : "s"} · ${total} ms MCP`;
  els.maxLatency.textContent = `${max} ms max`;
  els.maxLatency.className = `latency-chip ${max < 500 ? "good" : max < 1500 ? "warn" : ""}`;

  els.traceList.innerHTML = state.trace
    .map((item, index) => {
      const ok = item.ok !== false;
      const ms = Number(item.ms) || 0;
      const args = JSON.stringify(item.arguments || {}, null, 2);
      const clipped = args.length > 900 ? `${args.slice(0, 900)}…` : args;
      return `<details class="trace-item" ${ok ? "" : "open"}>
        <summary>
          <span class="trace-status ${ok ? "" : "bad"}">${ok ? "✓" : "×"}</span>
          <span class="trace-name">${escapeHtml(item.name || "tool")}</span>
          <span class="trace-ms ${ms < 500 ? "good" : ms < 1500 ? "warn" : ""}">${ms} ms</span>
          <span class="trace-chev">›</span>
        </summary>
        <div class="trace-body">
          <div class="trace-args">${escapeHtml(clipped)}</div>
          <div class="trace-speech">${escapeHtml(item.speech || "—")}</div>
        </div>
      </details>`;
    })
    .join("");

  if (!quiet) saveState();
}

function updateLatency(data) {
  const trace = data.trace || [];
  const max = trace.length ? Math.max(...trace.map((item) => Number(item.ms) || 0)) : 0;
  const turn = Number(data.elapsed_ms) || 0;
  els.latencyLine.textContent = `${turn} ms turn · ${max} ms max tool`;
  els.latencyLine.className = `latency ${max && max < 500 ? "good" : max < 1500 ? "warn" : ""}`;
}

/* ------------------------------------------------------------------ status */

function setStatus(text, mode = "ok") {
  els.statusLine.className = `status-line ${mode}`;
  els.statusLine.innerHTML = `<i class="dot"></i> ${escapeHtml(text)}`;
}

function setMcpStatus(text, mode = "ok") {
  els.mcpChip.className = `chip chip-live ${mode === "warn" ? "warn" : mode === "bad" ? "bad" : ""}`;
  els.mcpChip.innerHTML = `<i class="dot"></i><span>${escapeHtml(text)}</span>`;
}

function applyProviders(providers, agent) {
  if (!providers) return;
  const bedrock = providers.bedrock || {};
  const deepseek = providers.deepseek || {};
  const minimax = providers.minimax || {};
  const bedrockLive = bedrock.configured && bedrock.mode === "live";
  els.providerReasoning.textContent = bedrockLive
    ? "Bedrock text · live"
    : deepseek.mode === "live"
      ? "DeepSeek · live"
      : "Reasoning · fallback";
  els.providerVision.textContent = bedrockLive
    ? "Bedrock vision · live"
    : minimax.mode === "live"
      ? "MiniMax · live"
      : "Vision · fallback";
  els.providerReasoning.style.color =
    bedrockLive || deepseek.mode === "live" ? "var(--teal)" : "";
  els.providerVision.style.color =
    bedrockLive || minimax.mode === "live" ? "var(--teal)" : "";
  const activeAgent = agent || providers.simulator_agent || "rules";
  els.agentBadge.textContent = activeAgent;
  els.agentBadge.className = `agent-badge ${activeAgent !== "rules" ? "live" : ""}`;
  els.agentBadge.title =
    activeAgent === "bedrock"
      ? "Amazon Bedrock is driving the tool loop through the Converse API."
      : activeAgent === "deepseek"
        ? "DeepSeek is driving the tool loop live."
        : "Deterministic agent is driving the same MCP tools. Set BEDROCK_MODE=live or add a provider key.";
}

function setLinked(linked, user = null) {
  state.linked = Boolean(linked);
  state.linkedUser = user;
  if (linked) {
    els.linkBtn.classList.add("linked");
    els.linkLabel.textContent = `Linked${user ? ` · ${user}` : ""}`;
    els.linkBtn.title = "The OAuth 2.1 PKCE flow is complete.";
  } else {
    els.linkBtn.classList.remove("linked");
    els.linkLabel.textContent = "Link account";
    els.linkBtn.title = "Run the real OAuth consent + token flow";
  }
}

/* ------------------------------------------------------------------ send */

async function sendMessage(raw, { viaVoice = false } = {}) {
  const text = String(raw || "").trim();
  if (!text || state.busy) return;
  state.busy = true;
  setBusy(true);
  hideEmpty();
  addUserTurn(text);
  els.chatInput.value = "";
  const typing = showTyping();
  setStatus("Alexa+ is working through MCP…", "working");

  try {
    const data = await api("/api/simulator/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    typing.remove();
    state.sessionId = data.session_id;
    state.tripId = data.trip_id || state.tripId;

    let cards = Array.isArray(data.cards) ? data.cards : [];
    // A scene card is a TV update, not a chat card.
    const sceneCard = cards.find((card) => card?.type === "scene");
    if (sceneCard) {
      const current = state.scene;
      const different = sceneCard.scene_id && sceneCard.scene_id !== current?.id;
      if (different) {
        applyScene({
          id: sceneCard.scene_id,
          show: sceneCard.show,
          episode: sceneCard.episode,
          timestamp_label: sceneCard.timestamp,
          scene_title: sceneCard.title,
          summary: sceneCard.summary,
          dish: sceneCard.dish,
          destination: sceneCard.destination,
          visual: { emoji: sceneCard.destination?.emoji, mood: sceneCard.palette ? "from the current scene" : "" },
        });
      }
      cards = cards.filter((card) => card?.type !== "scene");
    }
    if (cards.some((card) => card?.type === "booking")) {
      cards = cards.filter((card) => card?.type === "booking");
    }
    const unique = [];
    const seen = new Set();
    for (const card of cards) {
      const key = `${card?.type}:${card?.trip_id || card?.confirmation_code || card?.title || ""}`;
      if (!seen.has(key)) {
        seen.add(key);
        unique.push(card);
      }
    }

    addAssistantTurn(data.reply || "I'm here. Where would you like to go?", unique, data.agent || "");
    renderTrace(data.trace || []);
    updateLatency(data);
    applyProviders(data.provider, data.agent);
    setStatus(
      `${data.mcp?.tool_calls ?? (data.trace || []).length} MCP tool call${
        (data.mcp?.tool_calls ?? (data.trace || []).length) === 1 ? "" : "s"
      } · ${data.mcp?.transport || "streamable-http"}`,
      "ok"
    );
    if (data.error) toast(data.error, { bad: true, title: "Provider fallback" });
    if (!state.muted) speak(data.reply);
    if (viaVoice) els.chatInput.placeholder = "Ask a follow-up…";
  } catch (error) {
    typing.remove();
    addAssistantTurn(
      "I couldn't reach the MCP server. Make sure the concierge service is running, then try again.",
      [],
      "error"
    );
    setStatus("MCP request failed", "error");
    toast(error.message, { bad: true, title: "Concierge unavailable" });
  } finally {
    state.busy = false;
    setBusy(false);
    scrollChat(true);
    setTimeout(() => scrollChat(true), 260);
  }
}

function setBusy(busy) {
  els.sendBtn.disabled = busy;
  els.micBtn.disabled = busy || !state.recognition;
  els.chatInput.disabled = busy;
  $$("[data-say]").forEach((button) => {
    button.disabled = busy;
  });
  if (els.playDemoBtn) els.playDemoBtn.disabled = busy;
}

/* ------------------------------------------------------------------ voice */

function setupVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    els.micBtn.hidden = true;
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    state.recording = true;
    els.micBtn.classList.add("recording");
    els.chatInput.placeholder = "Listening…";
    setStatus("Listening on the Echo Show mic…", "working");
  };
  recognition.onresult = (event) => {
    let interim = "";
    let final = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) final += transcript;
      else interim += transcript;
    }
    if (interim) els.chatInput.value = interim;
    if (final) {
      els.chatInput.value = final;
      setTimeout(() => sendMessage(final, { viaVoice: true }), 250);
    }
  };
  recognition.onerror = (event) => {
    if (event.error !== "aborted" && event.error !== "no-speech") {
      toast(`Microphone error: ${event.error}`, { bad: true, title: "Voice input" });
    }
    setStatus("Voice input stopped", "ok");
  };
  recognition.onend = () => {
    state.recording = false;
    els.micBtn.classList.remove("recording");
    els.chatInput.placeholder = "Book a weekend here, ask about the dish…";
    if (!state.busy) setStatus("Connected to /mcp · OAuth bearer", "ok");
  };
  state.recognition = recognition;

  els.micBtn.addEventListener("click", () => {
    if (state.busy) return;
    if (state.recording) {
      recognition.stop();
      return;
    }
    try {
      recognition.start();
    } catch {
      recognition.stop();
    }
  });
}

function speak(text) {
  if (!("speechSynthesis" in window) || state.muted) return;
  const clean = cleanSpeech(text);
  if (!clean) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 1.01;
  utterance.pitch = 1.0;
  utterance.volume = 0.95;
  const voices = window.speechSynthesis.getVoices();
  const preferred =
    voices.find((voice) => /Samantha|Ava|Allison|Google US English/i.test(voice.name)) ||
    voices.find((voice) => voice.lang?.startsWith("en-US"));
  if (preferred) utterance.voice = preferred;
  window.speechSynthesis.speak(utterance);
}

/* ------------------------------------------------------------------ boot */

async function loadBootstrap() {
  const data = await api("/api/demo/bootstrap");
  state.bootstrap = data;
  applyProviders(data.providers, data.providers?.simulator_agent);
  setMcpStatus("MCP live", "ok");
  setStatus("Connected to /mcp · OAuth bearer", "ok");
  els.footerStatus.textContent = `${data.app.name} · ${data.app.version} · ${data.mcp.transport}`;
  els.footerEndpoint.textContent = data.mcp.path;
  els.footerDot.classList.remove("bad");
  els.footerEndpoint.title = data.mcp.endpoint;
  if ($("#toolCount")) $("#toolCount").textContent = String(data.mcp.tools?.length || 14);

  renderSceneThumbs(data.scenes || []);
  const scene = data.scene || data.viewing?.scene;
  if (scene) applyScene(scene, { animate: false });
  renderPromptChips(data.demo_script || data.suggestions || []);
  return data;
}

function renderPromptChips(scripts) {
  els.promptChips.innerHTML = "";
  const prompts = scripts
    .map((item) => (typeof item === "string" ? { label: item, prompt: item } : item))
    .slice(0, 4);
  prompts.forEach((item, index) => {
    const button = document.createElement("button");
    button.className = "prompt-chip";
    button.type = "button";
    button.innerHTML = `<b>0${index + 1}</b><span>${escapeHtml(item.prompt || item.label)}</span>`;
    button.title = item.label || item.prompt;
    button.addEventListener("click", () => sendMessage(item.prompt || item.label));
    els.promptChips.append(button);
  });
}

async function checkLinkStatus() {
  try {
    const data = await api("/api/demo/link-status");
    setLinked(data.linked, data.user_id === "demo-user" ? "Ava" : data.user_id);
    return data.linked;
  } catch {
    setLinked(false);
    return false;
  }
}

async function startOAuth() {
  if (state.linked) {
    toast(`Already linked as ${state.linkedUser || "Ava"}. The MCP client is using an OAuth bearer token.`, {
      title: "OAuth 2.1",
    });
    return;
  }
  const popup = window.open("about:blank", "s2s_oauth", "width=560,height=760");
  try {
    const data = await api("/api/demo/oauth-url");
    if (popup) {
      popup.location.href = data.url;
      popup.focus();
    } else {
      window.location.href = data.url;
      return;
    }
    setStatus("Waiting for OAuth consent…", "working");
    let attempts = 0;
    let settled = false;
    const finish = async () => {
      if (settled) return;
      if (await checkLinkStatus()) {
        settled = true;
        clearInterval(timer);
        setStatus("OAuth bearer token linked", "ok");
        toast("OAuth 2.1 authorization code + PKCE completed. The simulator now has a bearer token.", {
          title: "Account linked",
        });
        return true;
      }
      return false;
    };
    const timer = setInterval(async () => {
      attempts += 1;
      if (await finish()) return;
      if (attempts > 40) {
        clearInterval(timer);
        setStatus("Connected to /mcp · OAuth bearer", "ok");
      }
    }, 1200);
    // Browsers throttle timers in background windows; re-check as soon as the user returns.
    const onFocus = () => { finish(); };
    const onVisible = () => { if (!document.hidden) finish(); };
    window.addEventListener("focus", onFocus, { once: true });
    document.addEventListener("visibilitychange", onVisible, { once: true });
  } catch (error) {
    if (popup) popup.close();
    toast(error.message, { bad: true, title: "OAuth unavailable" });
  }
}

function connectEvents() {
  if (!("EventSource" in window)) return;
  state.streamStartedAt = Date.now();
  const source = new EventSource("/api/events/stream?user_id=demo-user");
  source.onopen = () => setMcpStatus("MCP live", "ok");
  source.onerror = () => setMcpStatus("MCP reconnecting", "warn");
  source.addEventListener("tv_card", (event) => {
    try {
      const data = JSON.parse(event.data);
      const when = data.at ? new Date(data.at).getTime() : Date.now();
      if (when < state.streamStartedAt - 1500) return;
      showTvCard(data);
    } catch {
      /* ignore malformed events */
    }
  });
  source.addEventListener("trip_card_ready", (event) => {
    try {
      const data = JSON.parse(event.data);
      const when = data.at ? new Date(data.at).getTime() : Date.now();
      if (when < state.streamStartedAt - 1500) return;
      if (applyVisualCard(data.payload?.display)) {
        toast("The visual itinerary card is ready.", { title: "Card synthesis" });
      }
    } catch {
      /* ignore malformed events */
    }
  });
  source.addEventListener("booking_confirmed", (event) => {
    try {
      const data = JSON.parse(event.data);
      const when = data.at ? new Date(data.at).getTime() : Date.now();
      if (when < state.streamStartedAt - 1500) return;
      toast(`Confirmation ${data.payload?.code || ""} · ${data.payload?.title || "Your trip"}`, {
        title: "Booking confirmed",
      });
    } catch {
      /* ignore */
    }
  });
}

async function uploadFrame(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    toast("Choose a JPEG, PNG, or WebP frame.", { bad: true, title: "Not an image" });
    return;
  }
  els.analyzing.hidden = false;
  const form = new FormData();
  form.append("file", file);
  form.append("show", state.scene?.show || "");
  form.append("position_seconds", String(state.scene?.seconds || 0));
  try {
    const response = await fetch("/api/viewing/frame", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Frame analysis failed.");
    applyScene(data.scene);
    const analysis = data.analysis || {};
    const provider = data.provider || {};
    const live = provider.live;
    const providerName = provider.provider || "rules";
    const providerLabel =
      providerName === "bedrock"
        ? "Bedrock"
        : providerName === "minimax"
          ? "MiniMax"
          : "the vision provider";
    const text = analysis.description || analysis.location_guess || "A frame from the current scene.";
    addSystemTurn(
      `Frame read by ${live ? providerLabel : "the deterministic vision fallback"}: ${text} Matched ${
        data.scene?.show || "the scene"
      } · ${data.scene?.scene_title || ""}.`
    );
    toast(
      live
        ? `${providerLabel} read the frame and matched the scene.`
        : "No vision provider is configured — the deterministic vision fallback matched the frame.",
      { title: "Frame analysis", bad: false }
    );
    setStatus(`Frame analyzed · ${data.scene?.show || "scene"}`, "ok");
  } catch (error) {
    toast(error.message, { bad: true, title: "Frame analysis failed" });
  } finally {
    els.analyzing.hidden = true;
    els.frameInput.value = "";
  }
}

function bindEvents() {
  els.sendBtn.addEventListener("click", () => sendMessage(els.chatInput.value));
  els.chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(els.chatInput.value);
    }
  });
  els.playDemoBtn.addEventListener("click", () => {
    const first = state.bootstrap?.demo_script?.[0]?.prompt || "Book a weekend stay here next spring.";
    els.chatInput.value = first;
    sendMessage(first);
  });
  els.overlayClose.addEventListener("click", hideTvOverlay);
  els.tvOverlay.addEventListener("mouseenter", () => clearTimeout(state.overlayTimer));
  els.tvOverlay.addEventListener("mouseleave", () => scheduleOverlayHide(6000));
  els.traceToggle.addEventListener("click", () => {
    const collapsed = els.tracePanel.classList.toggle("collapsed");
    els.traceToggle.setAttribute("aria-expanded", String(!collapsed));
    els.traceToggle.style.transform = collapsed ? "rotate(180deg)" : "";
  });
  els.linkBtn.addEventListener("click", startOAuth);
  els.muteBtn.addEventListener("click", () => {
    state.muted = !state.muted;
    els.muteBtn.classList.toggle("muted", state.muted);
    if (state.muted && "speechSynthesis" in window) window.speechSynthesis.cancel();
    toast(state.muted ? "Spoken replies muted." : "Spoken replies on.", { title: "Audio" });
  });
  els.frameInput.addEventListener("change", (event) => uploadFrame(event.target.files?.[0]));
  els.resetBtn.addEventListener("click", async () => {
    if (!window.confirm("Reset the demo? This clears trips, quotes, bookings, and this conversation.")) return;
    try {
      await api("/api/simulator/reset", { method: "POST", body: JSON.stringify({ full: true }) });
      // The beforeunload handler would otherwise write the old conversation
      // back into localStorage immediately after this removal.
      window.removeEventListener("beforeunload", saveState);
      localStorage.removeItem(STORAGE_KEY);
      window.location.reload();
    } catch (error) {
      toast(error.message, { bad: true, title: "Reset failed" });
    }
  });
  // Card actions are delegated so dynamically rendered cards stay interactive.
  els.chat.addEventListener("click", (event) => {
    const button = event.target.closest("[data-say]");
    if (!button || state.busy) return;
    sendMessage(button.dataset.say || button.textContent.trim());
  });
  window.addEventListener("beforeunload", saveState);
}

async function init() {
  bindEvents();
  setupVoice();
  restoreState();
  try {
    await loadBootstrap();
    renderTranscript();
    if (!state.messages.length) {
      els.chat.classList.remove("has-messages");
      if (els.chatEmpty) els.chatEmpty.setAttribute("aria-hidden", "false");
    }
    connectEvents();
    checkLinkStatus();
    setBusy(false);
    setTimeout(() => els.chatInput.focus({ preventScroll: true }), 250);
  } catch (error) {
    setMcpStatus("MCP offline", "bad");
    setStatus("Could not reach the MCP server", "error");
    els.footerDot.classList.add("bad");
    els.footerStatus.textContent = "Concierge unavailable — start the server, then reload.";
    toast(error.message, { bad: true, title: "Boot failed" });
  }
}

document.addEventListener("DOMContentLoaded", init);
