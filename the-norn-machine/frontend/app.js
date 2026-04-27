/* ═══════════════════════════════════════════════════════════
   The Norn Machine — Application Logic
   Stateless client-side personality test engine
   v2: Added hesitation timer + API integration
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ─── Config ──────────────────────────────────────────────
  const API_ENDPOINT = 'https://kqdr3gfbs2.execute-api.ap-northeast-1.amazonaws.com/prod'; // Will be set after backend deployment
  const DIALOGUE_REVEAL_DELAY_MS = 1600;
  const EMPTY_SELECTION_READING = [
    '你没有选择任何一张画面，这本身也是一次选择。',
    '织机把这看作一种停在门槛上的姿势：你没有急着把自己交给某个符号，也没有让一瞬间的吸引替你决定方向。也许此刻最像你的，不是某个答案，而是对答案保持距离的那一下迟疑。',
    '这不是空白。它更像一枚没有落下的骰子：命运已经被拿在手里，只是你暂时不愿让它发出声音。',
  ].join('\n\n');

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
    dom.revealOverlay  = document.getElementById('reveal-overlay');
    dom.particleCanvas = document.getElementById('particle-canvas');
    dom.summaryRounds  = document.getElementById('summary-rounds');
    dom.summaryCards   = document.getElementById('summary-cards');
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
    initParticles();
    resetReading();
    resetDialoguePanel();

    if (state.selectedCards.length === 0) {
      renderEmptySelectionReading(payload);
      return;
    }

    // Call API (if endpoint configured)
    if (API_ENDPOINT) {
      callAnalyzeAPI(payload);
    }
  }

  // ─── API Call ────────────────────────────────────────────
  async function callAnalyzeAPI(payload) {
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
      // Graceful degradation: particle animation still plays
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

  function renderReading(reading) {
    if (!dom.readingText || !reading) return;
    dom.readingText.innerHTML = reading
      .split('\n\n')
      .map(p => `<p>${p}</p>`)
      .join('');
    dom.readingText.classList.add('visible');
    scheduleDialoguePanel();
  }

  function resetReading() {
    if (!dom.readingText) return;
    dom.readingText.classList.remove('visible');
    dom.readingText.innerHTML = '';
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
    return {
      'Content-Type': 'application/json',
      'x-api-key': 'zbwUtOMX0W8gKty6g9V1i9zWDf0IZBJ487ZdIP60'
    };
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

  // ─── Particle System ─────────────────────────────────────
  function initParticles() {
    const canvas = dom.particleCanvas;
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const maxDim = Math.max(canvas.width, canvas.height);

    // Phase 1: convergence particles (move inward)
    const convergeParticles = [];
    const CONVERGE_COUNT = 80;
    for (let i = 0; i < CONVERGE_COUNT; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = maxDim * 0.6 + Math.random() * 200;
      convergeParticles.push({
        x: cx + Math.cos(angle) * dist,
        y: cy + Math.sin(angle) * dist,
        targetX: cx + (Math.random() - 0.5) * 60,
        targetY: cy + (Math.random() - 0.5) * 60,
        size: 1 + Math.random() * 2.5,
        alpha: 0,
        speed: 0.003 + Math.random() * 0.008,
        progress: 0,
        startX: 0,
        startY: 0,
      });
      convergeParticles[i].startX = convergeParticles[i].x;
      convergeParticles[i].startY = convergeParticles[i].y;
    }

    // Phase 2: dissipation particles (scatter outward after convergence)
    const dissipateParticles = [];
    const DISSIPATE_COUNT = 50;
    let phase2Started = false;

    function spawnDissipation() {
      for (let i = 0; i < DISSIPATE_COUNT; i++) {
        const angle = Math.random() * Math.PI * 2;
        const endDist = maxDim * 0.5 + Math.random() * 300;
        dissipateParticles.push({
          x: cx + (Math.random() - 0.5) * 40,
          y: cy + (Math.random() - 0.5) * 40,
          targetX: cx + Math.cos(angle) * endDist,
          targetY: cy + Math.sin(angle) * endDist,
          size: 0.6 + Math.random() * 1.8,
          alpha: 0.6 + Math.random() * 0.4,
          speed: 0.001 + Math.random() * 0.004,
          progress: 0,
          startX: cx + (Math.random() - 0.5) * 40,
          startY: cy + (Math.random() - 0.5) * 40,
          delay: Math.random() * 120, // stagger start in frames
          frame: 0,
        });
      }
    }

    function animate() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      let anyActive = false;

      // Draw converging particles
      let allConverged = true;
      convergeParticles.forEach(p => {
        p.progress = Math.min(1, p.progress + p.speed);
        const ease = easeInOutCubic(p.progress);
        p.x = lerp(p.startX, p.targetX, ease);
        p.y = lerp(p.startY, p.targetY, ease);
        p.alpha = p.progress < 0.3 ? p.progress / 0.3 : 1;
        if (p.progress < 1) { allConverged = false; anyActive = true; }

        // After convergence, fade out converge particles slowly
        let drawAlpha = p.alpha;
        if (phase2Started) {
          p.alpha = Math.max(0, p.alpha - 0.008);
          drawAlpha = p.alpha;
        }
        if (drawAlpha > 0.01) {
          anyActive = true;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(201, 168, 76, ${drawAlpha * 0.7})`;
          ctx.fill();
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(201, 168, 76, ${drawAlpha * 0.08})`;
          ctx.fill();
        }
      });

      // Start phase 2 once most converge particles arrive
      if (allConverged && !phase2Started) {
        phase2Started = true;
        spawnDissipation();
      }

      // Draw dissipating particles (scatter outward)
      dissipateParticles.forEach(p => {
        p.frame++;
        if (p.frame < p.delay) { anyActive = true; return; }

        p.progress = Math.min(1, p.progress + p.speed);
        const ease = easeInOutCubic(p.progress);
        p.x = lerp(p.startX, p.targetX, ease);
        p.y = lerp(p.startY, p.targetY, ease);

        // Fade in then fade out
        let drawAlpha;
        if (p.progress < 0.15) {
          drawAlpha = (p.progress / 0.15) * p.alpha;
        } else {
          drawAlpha = p.alpha * (1 - (p.progress - 0.15) / 0.85);
        }
        drawAlpha = Math.max(0, drawAlpha);

        if (p.progress < 1 && drawAlpha > 0.01) {
          anyActive = true;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(201, 168, 76, ${drawAlpha * 0.5})`;
          ctx.fill();
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size * 4, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(201, 168, 76, ${drawAlpha * 0.04})`;
          ctx.fill();
        }
      });

      if (anyActive) requestAnimationFrame(animate);
    }
    animate();
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

  function lerp(a, b, t) { return a + (b - a) * t; }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  // ─── Boot ────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', init);
})();
