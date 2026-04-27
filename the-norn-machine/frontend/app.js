/* ═══════════════════════════════════════════════════════════
   The Norn Machine — Application Logic
   Stateless client-side personality test engine
   v2: Added hesitation timer + API integration
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ─── Config ──────────────────────────────────────────────
  const runtimeConfig = (window.__NORN_CONFIG__ && typeof window.__NORN_CONFIG__ === 'object')
    ? window.__NORN_CONFIG__
    : {};
  const API_ENDPOINT = normalizeApiEndpoint(runtimeConfig.apiEndpoint);
  const API_KEY = normalizeConfigValue(runtimeConfig.apiKey);
  const DIALOGUE_REVEAL_DELAY_MS = 1600;
  const EMPTY_SELECTION_READING = [
    '你没有选择任何一张画面，这本身也是一次选择。',
    '织机把这看作一种停在门槛上的姿势：你没有急着把自己交给某个符号，也没有让一瞬间的吸引替你决定方向。也许此刻最像你的，不是某个答案，而是对答案保持距离的那一下迟疑。',
    '这不是空白。它更像一枚没有落下的骰子：命运已经被拿在手里，只是你暂时不愿让它发出声音。',
  ].join('\n\n');
  const CONFIG_MISSING_READING = [
    '织机暂时还没有接上后端入口。',
    '这不是你的选择失效，而是页面还缺少可用的 API 配置。',
    '等部署完成，结果就会继续展开。',
  ].join('\n\n');

  function normalizeConfigValue(value) {
    return typeof value === 'string' ? value.trim() : '';
  }

  function normalizeApiEndpoint(value) {
    return normalizeConfigValue(value).replace(/\/+$/, '');
  }

  function hasBackendConfig() {
    return Boolean(API_ENDPOINT && API_KEY);
  }

  // ─── State ───────────────────────────────────────────────
  const state = {
    allCards: [],
    usedCardIds: new Set(),
    selectedCards: [],      // { id, round } accumulated across rounds
    deselectionEvents: [],  // { id, round } every time a selected card is cancelled
    currentRoundCards: [],
    roundNumber: 0,
    cardsPerRound: { min: 4, max: 6 },

    // Hesitation tracking
    roundStartTime: 0,                // timestamp when current round cards appeared
    roundDurations: [],               // [{ round: 1, duration_ms: 8200 }, ...]

    // Final dialogue
    analysisResult: null,
    dialogueTurns: 0,
    dialogueLimit: 1,
    dialogueHistory: [],
    dialogueLocked: false,
    dialogueRevealTimer: null,
    audioStarted: false,
    particleController: null,
    ambientController: null,
  };

  // ─── DOM ─────────────────────────────────────────────────
  const dom = {};

  // ─── Init ────────────────────────────────────────────────
  async function init() {
    dom.welcomeOverlay = document.getElementById('welcome-overlay');
    dom.startBtn       = document.getElementById('start-btn');
    dom.testArea       = document.getElementById('test-area');
    dom.cardGrid       = document.getElementById('card-grid');
    dom.roundNum       = document.getElementById('round-num');
    dom.totalSelected  = document.getElementById('total-selected');
    dom.nextBtn        = document.getElementById('next-btn');
    dom.revealBtn      = document.getElementById('reveal-btn');
    dom.ambientCanvas  = document.getElementById('ambient-canvas');
    dom.revealOverlay  = document.getElementById('reveal-overlay');
    dom.particleCanvas = document.getElementById('particle-canvas');
    dom.summaryRounds  = document.getElementById('summary-rounds');
    dom.summaryCards   = document.getElementById('summary-cards');
    dom.restartBlock   = document.getElementById('restart-block');
    dom.restartBtn     = document.getElementById('restart-btn');
    dom.revealHeading  = document.getElementById('reveal-heading');
    dom.revealSubtitle = document.getElementById('reveal-subtitle');
    dom.readingText    = document.getElementById('reading-text');
    dom.dialoguePanel  = document.getElementById('dialogue-panel');
    dom.dialogueLog    = document.getElementById('dialogue-log');
    dom.dialogueForm   = document.getElementById('dialogue-form');
    dom.dialogueInput  = document.getElementById('dialogue-input');
    dom.dialogueSend   = document.getElementById('dialogue-send');
    dom.dialogueCount  = document.getElementById('dialogue-count');
    dom.dialogueTurns  = document.getElementById('dialogue-turns');
    dom.dialogueStatus = document.getElementById('dialogue-status');
    dom.ambientAudio   = document.getElementById('ambient-audio');

    state.ambientController = initAmbientBackground();

    try {
      const resp = await fetch('./data/cards.json');
      state.allCards = await resp.json();
      console.log(`[Norn] Loaded ${state.allCards.length} cards`);
    } catch (err) {
      console.error('[Norn] Failed to load cards:', err);
      return;
    }

    dom.startBtn.addEventListener('click', startTest);
    dom.nextBtn.addEventListener('click', nextRound);
    dom.revealBtn.addEventListener('click', revealDestiny);
    if (dom.restartBtn) {
      dom.restartBtn.addEventListener('click', restartTest);
    }
    dom.dialogueForm.addEventListener('submit', handleDialogueSubmit);
    dom.dialogueInput.addEventListener('input', syncDialogueCounter);
    dom.dialogueInput.addEventListener('keydown', handleDialogueKeydown);
  }

  // ─── Welcome → Test ──────────────────────────────────────
  function startTest() {
    dom.welcomeOverlay.classList.add('dismissed');
    dom.testArea.classList.add('active');
    startAmbientAudio();
    drawRound();
  }

  // ─── Draw a Round ────────────────────────────────────────
  function drawRound() {
    // Record duration of PREVIOUS round (if any)
    if (state.roundStartTime > 0 && state.roundNumber > 0) {
      const duration = Date.now() - state.roundStartTime;
      state.roundDurations.push({
        round: state.roundNumber,
        duration_ms: duration,
      });
    }

    state.roundNumber++;
    dom.roundNum.textContent = state.roundNumber;

    const count = randInt(state.cardsPerRound.min, state.cardsPerRound.max);
    const available = state.allCards.filter(c => !state.usedCardIds.has(c.id));

    if (available.length < count) {
      state.usedCardIds.clear();
      const refreshed = state.allCards.filter(c => !state.usedCardIds.has(c.id));
      state.currentRoundCards = pickDiverseCards(refreshed, count);
    } else {
      state.currentRoundCards = pickDiverseCards(available, count);
    }

    state.currentRoundCards.forEach(c => state.usedCardIds.add(c.id));
    renderCards(state.currentRoundCards);

    // Start hesitation timer for this new round
    state.roundStartTime = Date.now();
  }

  // ─── Render Cards ────────────────────────────────────────
  function renderCards(cards) {
    const existing = dom.cardGrid.querySelectorAll('.card');
    if (existing.length > 0) {
      existing.forEach(el => el.classList.add('exiting'));
      setTimeout(() => {
        dom.cardGrid.innerHTML = '';
        appendCards(cards);
      }, 400);
    } else {
      dom.cardGrid.innerHTML = '';
      appendCards(cards);
    }
  }

  function appendCards(cards) {
    cards.forEach((card, index) => {
      const el = document.createElement('div');
      el.className = 'card entering';
      el.dataset.id = card.id;
      el.style.animationDelay = `${index * 0.12}s`;

      const isSelected = state.selectedCards.some(s => s.id === card.id);
      if (isSelected) el.classList.add('selected');

      const img = document.createElement('img');
      img.src = card.url;
      img.alt = card.id;
      img.draggable = false;
      img.onload = () => el.classList.add('loaded');
      img.onerror = () => {
        el.classList.add('loaded');
        el.style.background = 'linear-gradient(135deg, #1a1a2e, #2a1a3e)';
      };

      const symbol = document.createElement('div');
      symbol.className = 'card-symbol';
      symbol.textContent = '✦';

      el.appendChild(img);
      el.appendChild(symbol);
      el.addEventListener('click', () => toggleCard(card, el));

      dom.cardGrid.appendChild(el);
    });
  }

  // ─── Toggle Selection ────────────────────────────────────
  function toggleCard(card, el) {
    const idx = state.selectedCards.findIndex(s => s.id === card.id);
    if (idx === -1) {
      // Store with round number for backend weighting
      state.selectedCards.push({ id: card.id, round: state.roundNumber });
      el.classList.add('selected');
    } else {
      state.deselectionEvents.push({ id: card.id, round: state.roundNumber });
      state.selectedCards.splice(idx, 1);
      el.classList.remove('selected');
    }
    dom.totalSelected.textContent = state.selectedCards.length;
  }

  // ─── Next Round ──────────────────────────────────────────
  function nextRound() {
    drawRound();
  }

  // ─── Reveal Destiny ──────────────────────────────────────
  function revealDestiny() {
    // Record final round duration
    if (state.roundStartTime > 0) {
      state.roundDurations.push({
        round: state.roundNumber,
        duration_ms: Date.now() - state.roundStartTime,
      });
    }

    // Build API payload
    const payload = {
      selections: state.selectedCards,       // [{ id, round }]
      total_rounds: state.roundNumber,
      round_durations: state.roundDurations, // [{ round, duration_ms }]
      deselection_events: state.deselectionEvents,
    };

    // Console log for debugging
    console.log('═══════════════════════════════════════');
    console.log('✦ THE NORN MACHINE — Payload ✦');
    console.log(JSON.stringify(payload, null, 2));
    console.log('═══════════════════════════════════════');

    // Update summary
    dom.summaryRounds.textContent = state.roundNumber;
    dom.summaryCards.textContent = state.selectedCards.length;

    // Show reveal overlay + particles
    dom.revealOverlay.classList.add('active');
    stopParticles();
    state.particleController = initParticles();
    resetReading();
    resetDialoguePanel();

    if (state.selectedCards.length === 0) {
      renderEmptySelectionReading(payload);
      return;
    }

    if (!hasBackendConfig()) {
      renderMissingBackendReading(payload);
      return;
    }

    // Call API (if endpoint configured)
    callAnalyzeAPI(payload);
  }

  // ─── API Call ────────────────────────────────────────────
  async function callAnalyzeAPI(payload) {
    if (!hasBackendConfig()) {
      console.warn('[Norn] Backend config missing; skipping analysis request.');
      return;
    }

    try {
      const resp = await fetch(`${API_ENDPOINT}/analyze`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: JSON.stringify(payload),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const data = await resp.json();
      console.log('[Norn] API response:', data);
      state.analysisResult = data;
      if (data.max_dialogue_turns) {
        state.dialogueLimit = resolveDialogueLimit(data.max_dialogue_turns);
        updateDialogueMeta();
        if (!state.dialogueLocked) {
          setDialogueStatus(getDialogueReadyText());
        }
      }

      // Render the reading into the reveal overlay
      renderReading(data.reading);
    } catch (err) {
      console.error('[Norn] API error:', err);
      releaseParticles(0);
    }
  }

  function renderEmptySelectionReading(payload) {
    state.analysisResult = {
      mbti_type: 'XXXX',
      confidence: {},
      reading: EMPTY_SELECTION_READING,
      mode: 'empty',
      total_rounds: payload.total_rounds,
      total_selections: 0,
      empty_selection: true,
      max_dialogue_turns: getDialogueLimit(payload.total_rounds),
    };
    state.dialogueLimit = getDialogueLimit(payload.total_rounds);
    updateDialogueMeta();
    setDialogueStatus(getDialogueReadyText());
    renderReading(EMPTY_SELECTION_READING);
  }

  function renderMissingBackendReading(payload) {
    state.analysisResult = {
      mbti_type: 'XXXX',
      confidence: {},
      reading: CONFIG_MISSING_READING,
      mode: 'unconfigured',
      total_rounds: payload.total_rounds,
      total_selections: state.selectedCards.length,
      config_missing: true,
      max_dialogue_turns: getDialogueLimit(payload.total_rounds),
    };
    state.dialogueLimit = getDialogueLimit(payload.total_rounds);
    updateDialogueMeta();
    setDialogueStatus('后端入口尚未配置。');
    renderReading(CONFIG_MISSING_READING);
  }

  function renderReading(reading) {
    if (!dom.readingText || !reading) return;
    setRevealTitle('命运已经织成', 'The threads have taken shape');
    dom.readingText.innerHTML = reading
      .split('\n\n')
      .map(p => `<p>${p}</p>`)
      .join('');
    dom.readingText.classList.add('visible');
    showRestartBlock();
    releaseParticles(350);
    scheduleDialoguePanel();
  }

  function resetReading() {
    if (!dom.readingText) return;
    setRevealTitle('命运正在编织……', 'The threads of fate are converging');
    dom.readingText.classList.remove('visible');
    dom.readingText.innerHTML = '';
    hideRestartBlock();
  }

  function setRevealTitle(heading, subtitle) {
    if (dom.revealHeading) dom.revealHeading.textContent = heading;
    if (dom.revealSubtitle) dom.revealSubtitle.textContent = subtitle;
  }

  function showRestartBlock() {
    if (!dom.restartBlock) return;
    dom.restartBlock.classList.add('visible');
  }

  function hideRestartBlock() {
    if (!dom.restartBlock) return;
    dom.restartBlock.classList.remove('visible');
  }

  function resetDialoguePanel() {
    if (state.dialogueRevealTimer) {
      clearTimeout(state.dialogueRevealTimer);
      state.dialogueRevealTimer = null;
    }

    state.dialogueTurns = 0;
    state.dialogueLimit = getDialogueLimit(state.roundNumber);
    state.dialogueHistory = [];
    state.dialogueLocked = false;
    state.analysisResult = null;

    if (!dom.dialoguePanel) return;

    dom.dialoguePanel.classList.remove('active');
    dom.dialoguePanel.setAttribute('aria-hidden', 'true');
    dom.dialogueLog.innerHTML = '';
    dom.dialogueInput.value = '';
    dom.dialogueInput.disabled = true;
    dom.dialogueSend.disabled = true;
    dom.dialogueStatus.textContent = getDialogueReadyText();
    updateDialogueMeta();
    syncDialogueCounter();
  }

  function scheduleDialoguePanel() {
    if (!dom.dialoguePanel || state.dialogueRevealTimer) return;
    if (state.analysisResult && state.analysisResult.config_missing) return;

    state.dialogueRevealTimer = setTimeout(() => {
      state.dialogueRevealTimer = null;
      dom.dialoguePanel.setAttribute('aria-hidden', 'false');
      dom.dialogueInput.disabled = false;
      dom.dialogueSend.disabled = false;
      dom.dialoguePanel.classList.add('active');
      appendDialogueBubble('system', `织机愿意回应${formatTurnLabel(state.dialogueLimit)}追问。`);
    }, DIALOGUE_REVEAL_DELAY_MS);
  }

  async function handleDialogueSubmit(event) {
    event.preventDefault();
    if (state.dialogueLocked) return;
    if (!hasBackendConfig()) {
      setDialogueStatus('后端入口尚未配置。');
      return;
    }

    const message = dom.dialogueInput.value.trim();
    if (!message) return;
    if (message.length > 100) {
      setDialogueStatus('输入不能超过 100 字。');
      return;
    }

    const turnCount = state.dialogueTurns + 1;
    appendDialogueBubble('user', message);
    dom.dialogueInput.value = '';
    syncDialogueCounter();
    setDialogueBusy(true);
    setDialogueStatus('命运正在回声中……');

    const payload = {
      user_message: message,
      turn_count: turnCount,
      total_rounds: state.roundNumber,
      max_turns: state.dialogueLimit,
      history: state.dialogueHistory,
      analysis: state.analysisResult || {},
    };

    try {
      const resp = await fetch(`${API_ENDPOINT}/dialogue`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: JSON.stringify(payload),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }

      const reply = data.reply || data.text || '命运暂时没有更多话要说。';
      appendDialogueBubble('assistant', reply);
      state.dialogueHistory.push({ role: 'user', content: message });
      state.dialogueHistory.push({ role: 'assistant', content: reply });
      state.dialogueTurns = turnCount;
      if (data.max_turns) {
        state.dialogueLimit = resolveDialogueLimit(data.max_turns);
      }
      updateDialogueMeta();

      if (data.ended || state.dialogueTurns >= state.dialogueLimit) {
        lockDialogue(data.blocked ? (data.reply || data.text || '') : `${formatTurnLabel(state.dialogueLimit)}已满，织机不再继续回声。`);
      } else {
        setDialogueStatus(`还剩 ${state.dialogueLimit - state.dialogueTurns} 轮。`);
      }
    } catch (err) {
      console.error('[Norn] Dialogue error:', err);
      setDialogueStatus(formatDialogueError(err));
    } finally {
      setDialogueBusy(false);
    }
  }

  function handleDialogueKeydown(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;

    event.preventDefault();
    if (typeof dom.dialogueForm.requestSubmit === 'function') {
      dom.dialogueForm.requestSubmit();
    } else {
      handleDialogueSubmit(event);
    }
  }

  function lockDialogue(message) {
    state.dialogueLocked = true;
    dom.dialogueInput.disabled = true;
    dom.dialogueSend.disabled = true;
    if (message) {
      setDialogueStatus(message);
    }
  }

  function setDialogueBusy(isBusy) {
    dom.dialogueInput.disabled = isBusy || state.dialogueLocked;
    dom.dialogueSend.disabled = isBusy || state.dialogueLocked;
  }

  function appendDialogueBubble(role, text) {
    if (!dom.dialogueLog) return;
    const bubble = document.createElement('div');
    bubble.className = `dialogue-bubble ${role}`;
    bubble.textContent = text;
    dom.dialogueLog.appendChild(bubble);
    dom.dialogueLog.scrollTop = dom.dialogueLog.scrollHeight;
  }

  function syncDialogueCounter() {
    const length = dom.dialogueInput.value.length;
    dom.dialogueCount.textContent = `${length}/100`;
  }

  function updateDialogueMeta() {
    dom.dialogueTurns.textContent = `${state.dialogueTurns}/${state.dialogueLimit}`;
  }

  function setDialogueStatus(text) {
    dom.dialogueStatus.textContent = text;
  }

  function buildApiHeaders() {
    const headers = {
      'Content-Type': 'application/json',
    };
    if (API_KEY) {
      headers['x-api-key'] = API_KEY;
    }
    return headers;
  }

  function formatDialogueError(err) {
    const message = err && err.message ? err.message : '';
    if (message.includes('403') || message.includes('404')) {
      return '最后对话接口还没接到当前 API，部署后就会回应。';
    }
    if (message.includes('Failed to fetch')) {
      return '最后对话接口暂时没有回应。';
    }
    return message || '命运一时失语。';
  }

  function getDialogueLimit(totalRounds) {
    const rounds = Number.parseInt(totalRounds, 10) || 1;
    if (rounds <= 5) return 1;
    if (rounds <= 20) return 2;
    return 3;
  }

  function resolveDialogueLimit(apiLimit) {
    const localLimit = getDialogueLimit(state.roundNumber);
    const parsedLimit = Number.parseInt(apiLimit, 10);
    if (!Number.isFinite(parsedLimit) || parsedLimit < 1) return localLimit;
    return Math.min(localLimit, parsedLimit);
  }

  function formatTurnLabel(turns) {
    const labels = { 1: '一轮', 2: '两轮', 3: '三轮' };
    return labels[turns] || `${turns}轮`;
  }

  function getDialogueReadyText() {
    const variants = {
      1: '与织机进行对话，窥探命运之绳。',
      2: '与织机低声交谈，听命运回响两次。',
      3: '与织机继续对话，直到命运开始松动。',
    };
    return variants[state.dialogueLimit] || '与织机进行对话，窥探命运之绳。';
  }

  function startAmbientAudio() {
    if (state.audioStarted || !dom.ambientAudio) return;
    dom.ambientAudio.volume = 0.32;
    const playPromise = dom.ambientAudio.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise
        .then(() => {
          state.audioStarted = true;
        })
        .catch(err => {
          console.warn('[Norn] Ambient audio could not start:', err);
        });
    } else {
      state.audioStarted = true;
    }
  }

  function restartTest() {
    window.location.reload();
  }

  // ─── Ambient Starfield ──────────────────────────────────
  function initAmbientBackground() {
    const canvas = dom.ambientCanvas;
    if (!canvas) return null;

    const ctx = canvas.getContext('2d');
    const GOLD = '201, 168, 76';
    let frameId = null;
    let width = 0;
    let height = 0;
    let dpr = 1;
    let stars = [];
    let constellations = [];
    let stopped = false;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seedStars();
    }

    function seedStars() {
      const count = Math.max(120, Math.min(220, Math.round((width * height) / 3900)));
      stars = Array.from({ length: count }, () => {
        const edgeDistance = Math.random() < 0.45 ? Math.random() ** 1.8 : Math.random();
        const side = Math.floor(Math.random() * 4);
        const x = side === 0 ? width * edgeDistance
          : side === 1 ? width * (1 - edgeDistance)
          : Math.random() * width;
        const y = side === 2 ? height * edgeDistance
          : side === 3 ? height * (1 - edgeDistance)
          : Math.random() * height;
        return {
          x,
          y,
          size: 0.55 + Math.random() * 1.45,
          baseAlpha: 0.1 + Math.random() * 0.18,
          phase: Math.random() * Math.PI * 2,
          speed: 0.0012 + Math.random() * 0.0022,
          amplitude: 0.34 + Math.random() * 0.32,
        };
      });

      const borderStars = stars
        .slice()
        .sort((a, b) => {
          const edgeA = Math.min(a.x, width - a.x, a.y, height - a.y);
          const edgeB = Math.min(b.x, width - b.x, b.y, height - b.y);
          return edgeA - edgeB;
        })
        .slice(0, 30);

      constellations = borderStars.slice(0, 9).map((star, index) => {
        const neighborA = borderStars[(index * 4 + 5) % borderStars.length];
        const neighborB = borderStars[(index * 7 + 13) % borderStars.length];
        return [star, neighborA, neighborB].filter(Boolean);
      });
    }

    function drawStar(star, time, alphaScale = 1) {
      const twinkle = 1 - star.amplitude + (Math.sin(time * star.speed + star.phase) + 1) * 0.5 * star.amplitude;
      const alpha = star.baseAlpha * twinkle * alphaScale;
      if (alpha <= 0.004) return;

      ctx.beginPath();
      ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${GOLD}, ${alpha})`;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.size * 4.2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${GOLD}, ${alpha * 0.08})`;
      ctx.fill();
    }

    function animate(time) {
      if (stopped) return;
      ctx.clearRect(0, 0, width, height);
      stars.forEach(star => drawStar(star, time, 1.05));
      constellations.forEach(chain => {
        if (chain.length < 3) return;
        ctx.beginPath();
        chain.forEach((star, index) => {
          if (index === 0) ctx.moveTo(star.x, star.y);
          else ctx.lineTo(star.x, star.y);
        });
        const shimmer = 0.58 + Math.sin(time / 1400 + chain[0].phase) * 0.28;
        ctx.strokeStyle = `rgba(${GOLD}, ${0.085 * shimmer})`;
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });
      frameId = requestAnimationFrame(animate);
    }

    resize();
    window.addEventListener('resize', resize);
    frameId = requestAnimationFrame(animate);

    return {
      stop() {
        stopped = true;
        window.removeEventListener('resize', resize);
        if (frameId) cancelAnimationFrame(frameId);
        ctx.clearRect(0, 0, width, height);
      },
    };
  }

  // ─── Particle System ─────────────────────────────────────
  function initParticles() {
    const canvas = dom.particleCanvas;
    const ctx = canvas.getContext('2d');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const cx = width / 2;
    const cy = height / 2;
    const maxDim = Math.max(width, height);
    const CONVERGE_MS = 1650;
    const MIN_HOLD_MS = 320;
    const DISSIPATE_MS = 2900;
    const AUTO_RELEASE_MS = 9000;
    const PARTICLE_COUNT = 120;
    const AMBIENT_STAR_COUNT = 144;
    let frameId = null;
    let startTime = null;
    let releaseAt = null;
    let stopped = false;

    const particles = Array.from({ length: PARTICLE_COUNT }, () => {
      const angle = Math.random() * Math.PI * 2;
      const startDist = maxDim * (0.55 + Math.random() * 0.35);
      const clusterAngle = Math.random() * Math.PI * 2;
      const clusterDist = Math.random() * 38;
      const marginX = Math.min(72, width * 0.1);
      const marginY = Math.min(72, height * 0.1);
      const settleX = marginX + Math.random() * Math.max(1, width - marginX * 2);
      const settleY = marginY + Math.random() * Math.max(1, height - marginY * 2);
      return {
        startX: cx + Math.cos(angle) * startDist,
        startY: cy + Math.sin(angle) * startDist,
        clusterX: cx + Math.cos(clusterAngle) * clusterDist,
        clusterY: cy + Math.sin(clusterAngle) * clusterDist,
        endX: settleX,
        endY: settleY,
        size: 0.9 + Math.random() * 2.4,
        alpha: 0.6 + Math.random() * 0.4,
        settleAlpha: 0.12 + Math.random() * 0.18,
        settleSize: 0.45 + Math.random() * 1.15,
        drift: Math.random() * Math.PI * 2,
        twinkleSpeed: 0.002 + Math.random() * 0.0025,
      };
    });

    const ambientStars = Array.from({ length: AMBIENT_STAR_COUNT }, () => {
      const x = Math.random() * width;
      const y = Math.random() * height;
      return {
        x,
        y,
        size: 0.35 + Math.random() * 1.35,
        alpha: 0.05 + Math.random() * 0.1,
        twinkle: Math.random() * Math.PI * 2,
        twinkleSpeed: 0.001 + Math.random() * 0.002,
        twinkleAmp: 0.28 + Math.random() * 0.24,
      };
    });

    const borderStars = ambientStars
      .slice()
      .sort((a, b) => {
        const edgeA = Math.min(a.x, width - a.x, a.y, height - a.y);
        const edgeB = Math.min(b.x, width - b.x, b.y, height - b.y);
        return edgeA - edgeB;
      })
      .slice(0, 24);

    const constellations = borderStars.length >= 3
      ? borderStars.slice(0, 8).map((star, index) => {
          const neighborA = borderStars[(index * 3 + 5) % borderStars.length];
          const neighborB = borderStars[(index * 5 + 11) % borderStars.length];
          return [star, neighborA, neighborB];
        })
      : [];

    function drawParticle(x, y, size, alpha) {
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(201, 168, 76, ${alpha * 0.95})`;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, size * 5.2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(201, 168, 76, ${alpha * 0.14})`;
      ctx.fill();
    }

    function drawCore(radius, alpha) {
      const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      gradient.addColorStop(0, `rgba(201, 168, 76, ${alpha * 0.42})`);
      gradient.addColorStop(0.38, `rgba(201, 168, 76, ${alpha * 0.16})`);
      gradient.addColorStop(1, 'rgba(201, 168, 76, 0)');
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    function drawAmbientField(alphaScale, time) {
      const frameAlpha = Math.max(0, alphaScale);
      ambientStars.forEach(star => {
        const flicker = 1 - star.twinkleAmp + (Math.sin(time * star.twinkleSpeed + star.twinkle) + 1) * 0.5 * star.twinkleAmp;
        const edgeDistance = Math.min(star.x, width - star.x, star.y, height - star.y);
        const rimBoost = Math.max(0, 1 - edgeDistance / (maxDim * 0.22));
        const alpha = star.alpha * frameAlpha * flicker * (1 + rimBoost * 0.5);
        if (alpha <= 0.004) return;
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(201, 168, 76, ${alpha * 0.95})`;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size * 4.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(201, 168, 76, ${alpha * 0.08})`;
        ctx.fill();
      });

      constellations.forEach(chain => {
        ctx.beginPath();
        chain.forEach((star, index) => {
          if (index === 0) {
            ctx.moveTo(star.x, star.y);
          } else {
            ctx.lineTo(star.x, star.y);
          }
        });
        ctx.strokeStyle = `rgba(201, 168, 76, ${frameAlpha * (0.045 + Math.sin(time / 1500 + chain[0].twinkle) * 0.018)})`;
        ctx.lineWidth = 0.8;
        ctx.stroke();
      });
    }

    function drawConvergence(elapsed) {
      const t = clamp01(elapsed / CONVERGE_MS);
      const eased = easeOutCubic(t);
      const fadeIn = clamp01(elapsed / 520);
      const pulse = t >= 1 ? 1 + Math.sin(elapsed / 180) * 0.18 : 1;

      particles.forEach(p => {
        const x = lerp(p.startX, p.clusterX, eased);
        const y = lerp(p.startY, p.clusterY, eased);
        drawParticle(x, y, p.size * pulse, p.alpha * fadeIn);
      });
      drawAmbientField(0.12 + eased * 0.06, elapsed);
      drawCore(22 + 34 * eased * pulse, fadeIn * (0.35 + eased * 0.45));
    }

    function drawDissipation(elapsed) {
      const t = clamp01(elapsed / DISSIPATE_MS);
      const eased = easeOutQuint(t);
      const trailFade = Math.pow(1 - t, 1.6);

      particles.forEach(p => {
        const wave = Math.sin(t * Math.PI * 3 + p.drift) * 14 * Math.pow(1 - t, 1.4);
        const x = lerp(p.clusterX, p.endX, eased) + wave;
        const y = lerp(p.clusterY, p.endY, eased) - wave * 0.4;
        ctx.beginPath();
        ctx.moveTo(p.clusterX, p.clusterY);
        ctx.lineTo(x, y);
        ctx.strokeStyle = `rgba(201, 168, 76, ${p.alpha * trailFade * 0.1})`;
        ctx.lineWidth = 0.6 + p.size * 0.12;
        ctx.stroke();
        const alpha = lerp(p.alpha, p.settleAlpha, easeOutCubic(t));
        const size = lerp(p.size, p.settleSize, easeOutCubic(t));
        drawParticle(x, y, size, alpha);
      });
      drawAmbientField(0.22 + t * 0.35, elapsed);
      drawCore(44 + 120 * eased, trailFade * 0.45);
    }

    function drawSettledParticles(time) {
      particles.forEach(p => {
        const twinkle = 0.62 + (Math.sin(time * p.twinkleSpeed + p.drift) + 1) * 0.19;
        drawParticle(p.endX, p.endY, p.settleSize, p.settleAlpha * twinkle);
      });
    }

    function animate(timestamp) {
      if (stopped) return;
      if (startTime === null) startTime = timestamp;
      const elapsed = timestamp - startTime;

      if (releaseAt === null && elapsed > AUTO_RELEASE_MS) {
        releaseAt = timestamp;
      }

      ctx.clearRect(0, 0, width, height);
      if (releaseAt === null || timestamp < releaseAt) {
        drawConvergence(elapsed);
      } else {
        const releaseElapsed = timestamp - releaseAt;
        if (releaseElapsed >= DISSIPATE_MS) {
          drawAmbientField(0.92, timestamp);
          drawSettledParticles(timestamp);
          drawCore(Math.min(maxDim * 0.7, 220), 0.22);
        } else {
          drawDissipation(releaseElapsed);
        }
      }

      frameId = requestAnimationFrame(animate);
    }

    frameId = requestAnimationFrame(animate);
    return {
      release(delayMs = 0) {
        const now = performance.now();
        const earliest = (startTime || now) + CONVERGE_MS + MIN_HOLD_MS;
        releaseAt = releaseAt || Math.max(now + delayMs, earliest);
      },
      stop() {
        stopped = true;
        if (frameId) cancelAnimationFrame(frameId);
        ctx.clearRect(0, 0, width, height);
      },
    };
  }

  function releaseParticles(delayMs = 0) {
    if (state.particleController && typeof state.particleController.release === 'function') {
      state.particleController.release(delayMs);
    }
  }

  function stopParticles() {
    if (state.particleController && typeof state.particleController.stop === 'function') {
      state.particleController.stop();
      state.particleController = null;
    }
  }

  // ─── Utilities ───────────────────────────────────────────
  function pickDiverseCards(arr, n) {
    const pool = shuffle(arr);
    const picked = [];
    while (picked.length < n && pool.length > 0) {
      let bestIndex = 0;
      let bestScore = -Infinity;
      pool.forEach((card, index) => {
        const score = scoreCardDiversity(card, picked) + Math.random() * 0.2;
        if (score > bestScore) {
          bestScore = score;
          bestIndex = index;
        }
      });
      picked.push(pool.splice(bestIndex, 1)[0]);
    }
    return picked;
  }

  function scoreCardDiversity(card, picked) {
    if (picked.length === 0) return 0;

    const sameTypeCount = picked.filter(item => item.type === card.type).length;
    const sameGroupCount = picked.filter(item => getTypeGroup(item.type) === getTypeGroup(card.type)).length;
    const distanceScore = picked.reduce((sum, item) => sum + coordinateDistance(card, item), 0) / picked.length;

    return (
      distanceScore
      + (sameTypeCount === 0 ? 4 : -8 * sameTypeCount)
      + (sameGroupCount === 0 ? 2 : -1.5 * sameGroupCount)
    );
  }

  function coordinateDistance(a, b) {
    if (!a.coords || !b.coords) return 0;
    const dims = ['E_I', 'S_N', 'T_F', 'J_P'];
    const total = dims.reduce((sum, dim) => sum + Math.abs((a.coords[dim] || 0) - (b.coords[dim] || 0)), 0);
    return total / (dims.length * 16);
  }

  function getTypeGroup(type) {
    if (!type || type.length < 4) return 'unknown';
    const sn = type[1];
    const tf = type[2];
    const jp = type[3];
    if (sn === 'N' && tf === 'T') return 'analyst';
    if (sn === 'N' && tf === 'F') return 'diplomat';
    if (sn === 'S' && jp === 'J') return 'sentinel';
    if (sn === 'S' && jp === 'P') return 'explorer';
    return 'unknown';
  }

  function shuffleAndPick(arr, n) {
    return shuffle(arr).slice(0, n);
  }

  function shuffle(arr) {
    const shuffled = [...arr];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  }

  function randInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  function clamp01(value) {
    return Math.max(0, Math.min(1, value));
  }

  function lerp(a, b, t) { return a + (b - a) * t; }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function easeOutQuint(t) {
    return 1 - Math.pow(1 - t, 5);
  }

  // ─── Boot ────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', init);
})();
