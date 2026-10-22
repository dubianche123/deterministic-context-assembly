/* ═══════════════════════════════════════════════════════════
   The Norn Machine — Application Logic
   Offline-first selection experience with temporary dialogue
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const i18n = window.NornI18n;
  // The bare event fixture and legacy embeds can still execute without i18n.js.
  const tr = i18n ? i18n.t : (input, ...values) => typeof input === 'string' ? input
    : input.reduce((text, part, index) => text + part + (index < values.length ? values[index] : ''), '');
  const currentLanguage = () => i18n ? i18n.language : 'zh';

  // ─── Config ──────────────────────────────────────────────
  let API_ENDPOINT = '';
  let API_KEY = '';
  let PROVIDER = '';
  let HOSTING = '';
  let COLLECT = false;
  let CONSENT_VERSION = '';
  let PILOT_PROTOCOL = null;
  // A consented browser keeps one random key; the server stores only its hash.
  const VISITOR_STORAGE_KEY = 'norn.visitor.v1';
  const THREADS_STORAGE_KEY = 'norn.threads.v1';
  let pageVisitorKey = null;        // used when browser storage is unavailable
  let cloudSessionReady = null;
  const DIALOGUE_REVEAL_DELAY_MS = 1600;
  const RESTART_REVEAL_DELAY_MS = DIALOGUE_REVEAL_DELAY_MS + 1200;
  const FALLBACK_RESTART_REVEAL_DELAY_MS = 900;
  const MAX_SESSION_ROUNDS = 256;
  const MAX_SESSION_EVENTS = 10000;
  const EVENT_SCHEMA_VERSION = 'norn.events.v5';
  // Each session is randomized once to a display policy (engine display_policy.py).
  // A mixed round shows half its cards, rounded down, ranked by the visitor's
  // scoring-v5 candidate; runtime config displayMixedShare: 0 turns it off.
  const DISPLAY_RANDOM = 'norn.display.random.v1';
  const DISPLAY_MIXED = 'norn.display.mixed.v1';
  const DISPLAY_FEATURES_URL = './data/display-features-v1.json';
  const DISPLAY_FEATURE_COUNT = 12;
  let DISPLAY_MIXED_SHARE = 0.5;
  // A research deployment serves a research deck (engine research_deck) instead of
  // Norn's own cards: display v2 ranks with research v6 and adds a popular/unpopular contrast.
  const DECK_RANDOM = 'norn.display.random.v2';
  const DECK_MIXED = 'norn.display.mixed.v2';
  const DECK_FEATURE_COUNT = 9;
  let DECK = '';
  let LAST_CARD = false;
  // A resource/experience bound, never a threshold for having learned a person.
  const LAST_CARD_MAX_ROUNDS = 12;
  const LAST_CARD_PRODUCT = Object.freeze({ schema_version: 'norn.last-card.v1', pacing_policy: 'norn.pacing.v1' });
  // Pilot only (?pilot=new-material-v1, consented visitors): after the reveal
  // seals the main journal, a few rounds of frozen new images are recorded in a
  // separate probe journal before the reading appears. See norn/probe.py.
  const PROBE_SCHEMA_VERSION = 'norn.probe.v1';
  const PROBE_PROTOCOLS = { 'new-material-v1': './data/probe-new-material-v1.json' };
  const MAX_ROUND_MS = 86400000;
  // A hung image must not keep a round unchoosable; it is reported unloaded.
  const ROUND_READY_TIMEOUT_MS = 8000;
  const SEEN_RATIO = 0.5;
  // Hover segments per round; a sweeping pointer stops being recorded beyond this.
  const HOVER_SEGMENT_LIMIT = 512;
  const PARTIAL_SAVE_DELAY_MS = 500;
  const PARTIAL_PULSE_MS = 10000;
  // Keep below the browser's aggregate 64 KiB keepalive body allowance.
  const PARTIAL_KEEPALIVE_BYTES = 60 * 1024;
  const POINTER_INPUTS = new Set(['mouse', 'touch', 'pen']);
  const missingBackendReading = () => [
    tr('织机暂时还没有接上后端入口。'),
    tr('请使用 norn serve --mock 启动本地服务，再打开终端显示的地址。'),
    tr('你的选择仍在本页保留，服务恢复后可以重试。'),
  ].join('\n\n');

  function normalizeConfigValue(value) {
    return typeof value === 'string' ? value.trim() : '';
  }

  function normalizeApiEndpoint(value) {
    return normalizeConfigValue(value).replace(/\/+$/, '');
  }

  function readRuntimeConfig() {
    const runtimeConfig = (window.__NORN_CONFIG__ && typeof window.__NORN_CONFIG__ === 'object')
      ? window.__NORN_CONFIG__
      : {};
    API_ENDPOINT = normalizeApiEndpoint(runtimeConfig.apiEndpoint);
    API_KEY = normalizeConfigValue(runtimeConfig.apiKey);
    PROVIDER = normalizeConfigValue(runtimeConfig.provider);
    HOSTING = normalizeConfigValue(runtimeConfig.hosting);
    // The weaving choice exists only when the server will really store records.
    COLLECT = runtimeConfig.collect === true && Boolean(normalizeConfigValue(runtimeConfig.consentVersion));
    CONSENT_VERSION = normalizeConfigValue(runtimeConfig.consentVersion);
    const share = runtimeConfig.displayMixedShare;
    DISPLAY_MIXED_SHARE = typeof share === 'number' && share >= 0 && share <= 1 ? share : 0.5;
    const search = (window.location && window.location.search) || '';
    const pilot = typeof URLSearchParams === 'function' ? new URLSearchParams(search).get('pilot') : null;
    PILOT_PROTOCOL = Object.prototype.hasOwnProperty.call(PROBE_PROTOCOLS, pilot) ? pilot : null;
    const deck = normalizeConfigValue(runtimeConfig.deck);
    DECK = /^[a-z0-9-]{1,40}$/.test(deck) ? deck : '';
    LAST_CARD = Boolean(DECK && runtimeConfig.lastCard === true);
    if (DECK) PILOT_PROTOCOL = null;  // the research deck is itself new material
  }

  function hasBackendConfig() {
    return Boolean(API_ENDPOINT) && window.location.protocol !== 'file:';
  }

  async function ensureBackendConfig() {
    readRuntimeConfig();
    if (!hasBackendConfig()) return false;
    if (HOSTING === 'cloudflare') {
      if (!cloudSessionReady) {
        cloudSessionReady = fetch(`${API_ENDPOINT}/health`, { credentials: 'same-origin' })
          .then(response => {
            if (!response.ok) throw new Error(tr('在线服务暂时无法连接，请稍后重试。'));
          }).catch(error => { cloudSessionReady = null; throw error; });
      }
      await cloudSessionReady;
    }
    return true;
  }

  readRuntimeConfig();

  // ─── State ───────────────────────────────────────────────
  const state = {
    allCards: [],
    usedCardIds: new Set(),
    selectedCards: [],      // UI projection only; the server replays the journal
    currentRoundCards: [],
    roundNumber: 0,
    cardsPerRound: { min: 4, max: 6 },
    displayPolicy: null,              // drawn once per session, before its first round
    displayFeatures: null,            // card id -> display features (v1: twelve; research deck: u + x8), or null
    deckLabel: null,                  // "<deck>@<version>" of a research deck

    eventJournal: { schema_version: EVENT_SCHEMA_VERSION, events: [] },
    sessionSealed: false,
    roundOpen: false,
    roundTransitioning: false,
    roundStartTime: null,             // monotonic timestamp after the cards render
    roundAttention: null,             // readiness, visibility and tab-idle facts of this round
    visibilityTracking: false,
    feedback: { score: null, closest: null, busy: false },
    consent: false,                   // chosen once at the entrance, never assumed
    entryLocked: false,
    playMode: false,                  // precommitted guessing sidecar; no effect on model fitting
    roundFeedbackEnabled: false,      // independent, default-off exposure opt-in
    experience: null,
    experienceStartedAt: null,
    feedbackExposurePending: new Set(),
    playForecasts: new Map(),
    playResults: new Map(),
    jevAvailable: false,
    jevEnabled: false,
    jevChoiceTouched: false,
    jevStart: null,
    jevPredictions: new Map(),
    jevRevision: 0,
    jevMessage: null,
    jevStatusPromise: null,
    collectionEpoch: 0,               // invalidate snapshots queued before a withdrawal
    partialSaveTimer: null,
    partialPulseTimer: null,
    partialPending: null,             // one latest immutable checkpoint, never a backlog
    partialInFlight: null,
    probe: null,                      // pilot new-image phase: deck, rounds, journal, saved main state
    record: null,                     // { record_id, unconfirmed? } of this reading, when woven in
    sessionKey: null,                 // random id of this visit: its partial record, then its full one
    withdrawBusy: false,
    finaleChallenge: null,
    finaleResult: null,
    finaleRequest: null,
    finaleChoiceRequest: null,
    finaleSelection: null,
    finaleStartedAt: null,
    finaleReadyAt: null,
    finaleHiddenSince: null,
    finaleHiddenMs: 0,
    finaleInput: 'unknown',
    finaleLayout: null,
    finaleBusy: false,
    finaleImagesReady: false,
    finaleJevSettled: true,
    finaleJevAttempted: false,
    finaleRenderEpoch: 0,
    finaleError: null,
    finaleBasePayload: null,
    pacing: null,
    shareBusy: false,

    // Final dialogue
    analysisResult: null,
    pendingAnalysisPayload: null,
    analysisBusy: false,
    exportBusy: false,
    dialogueBusy: false,
    dialogueTurns: 0,
    dialogueLimit: 1,
    dialogueHistory: [],
    pendingDialoguePayload: null,
    dialogueLocked: false,
    dialogueRevealTimer: null,
    restartRevealTimer: null,
    audioStarted: false,
    masterVolume: 0.32,
    particleController: null,
    ambientController: null,
    xrayOpen: false,
    xraySource: 'live',
    xrayEpoch: 0,
    xrayTimer: null,
    xrayRequestedKey: null,
    xrayImported: null,
    xrayFollow: false,
    xrayTrajectory: null,
    xrayTrajectorySize: null,
    xrayRound: -1,
    xrayPlayTimer: null,
    xrayCells: new Map(),
    xrayCardUrls: null,
    xrayWeaveSignature: null,
    xrayMarker: null,
    xrayBars: null,
    xrayDetailsFor: null,
    xrayDetailNodes: null,
    xrayOfflineComparison: null,
    xrayRecordText: null,
  };

  // ─── DOM ─────────────────────────────────────────────────
  const dom = {};

  // ─── Init ────────────────────────────────────────────────
  async function init() {
    dom.welcomeOverlay = document.getElementById('welcome-overlay');
    dom.startBtn       = document.getElementById('start-btn');
    dom.startPrivate   = document.getElementById('start-private');
    dom.entryLead      = document.getElementById('entry-lead');
    dom.entryDetail    = document.getElementById('entry-detail');
    dom.deckCredit     = document.getElementById('deck-credit');
    dom.entryForget    = document.getElementById('entry-forget');
    dom.entryForgetText = document.getElementById('entry-forget-text');
    dom.forgetVisitor  = document.getElementById('forget-visitor');
    dom.testArea       = document.getElementById('test-area');
    dom.cardGrid       = document.getElementById('card-grid');
    dom.roundNum       = document.getElementById('round-num');
    dom.roundLabel     = document.getElementById('round-label');
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
    dom.volumeSlider   = document.getElementById('volume-slider');
    dom.runtimeNotice  = document.getElementById('runtime-notice');
    dom.resultMode     = document.getElementById('result-mode');
    dom.analysisError  = document.getElementById('analysis-error');
    dom.retryAnalysis  = document.getElementById('retry-analysis');
    dom.assetStatus    = document.getElementById('asset-status');
    dom.retryAssets    = document.getElementById('retry-assets');
    dom.roundStatus    = document.getElementById('round-status');
    dom.recordExport   = document.getElementById('record-export');
    dom.exportRecord   = document.getElementById('export-record');
    dom.profileVersion = document.getElementById('profile-version');
    dom.exportStatus   = document.getElementById('export-status');
    dom.recordNote     = document.getElementById('record-note');
    dom.recordNoteText = document.getElementById('record-note-text');
    dom.withdrawRecord = document.getElementById('withdraw-record');
    dom.feedbackPanel  = document.getElementById('feedback-panel');
    dom.feedbackRating = document.getElementById('feedback-rating');
    dom.feedbackClosestBlock = document.getElementById('feedback-closest-block');
    dom.feedbackClosest = document.getElementById('feedback-closest');
    dom.feedbackSave   = document.getElementById('feedback-save');
    dom.feedbackSkip   = document.getElementById('feedback-skip');
    dom.feedbackStatus = document.getElementById('feedback-status');
    dom.xrayToggle    = document.getElementById('xray-toggle');
    dom.xrayPanel     = document.getElementById('xray-panel');
    dom.xrayTitle     = document.getElementById('xray-title');
    dom.xrayClose     = document.getElementById('xray-close');
    dom.xrayRefresh   = document.getElementById('xray-refresh');
    dom.xrayImport    = document.getElementById('xray-import');
    dom.xrayLive      = document.getElementById('xray-live');
    dom.xraySource    = document.getElementById('xray-source');
    dom.xrayStatus    = document.getElementById('xray-status');
    dom.xrayPlay      = document.getElementById('xray-play');
    dom.xrayRound     = document.getElementById('xray-round');
    dom.xrayTicks     = document.getElementById('xray-ticks');
    dom.xrayRoundLabel = document.getElementById('xray-round-label');
    dom.xrayContent   = document.getElementById('xray-content');
    dom.xrayLastCard = document.getElementById('xray-last-card');
    dom.xrayComparison = document.getElementById('xray-comparison');
    dom.xrayComparisonHeading = document.getElementById('xray-comparison-heading');
    dom.xrayComparisonMode = document.getElementById('xray-comparison-mode');
    dom.xrayComparisonScope = document.getElementById('xray-comparison-scope');
    dom.xrayComparisonLegend = document.getElementById('xray-comparison-legend');
    dom.xrayComparisonContext = document.getElementById('xray-comparison-context');
    dom.xrayComparisonCards = document.getElementById('xray-comparison-cards');
    dom.xrayComparisonResult = document.getElementById('xray-comparison-result');
    dom.xrayComparisonLoss = document.getElementById('xray-comparison-loss');
    dom.xrayComparisonNote = document.getElementById('xray-comparison-note');
    dom.xrayComparisonOnline = document.getElementById('xray-comparison-online');
    dom.xrayComparisonImport = document.getElementById('xray-comparison-import');
    dom.xrayComparisonImportControl = document.getElementById('xray-comparison-import-control');
    dom.xrayLossHeading = document.getElementById('xray-loss-heading');
    dom.xrayLossValue = document.getElementById('xray-loss-value');
    dom.xrayModelView = document.getElementById('xray-model-view');
    dom.xrayModelSummary = document.getElementById('xray-model-summary');
    dom.xrayWeave     = document.getElementById('xray-weave');
    dom.xrayWeaveHeading = document.getElementById('xray-weave-heading');
    dom.xrayAxisTop   = document.getElementById('xray-axis-top');
    dom.xrayAxisBottom = document.getElementById('xray-axis-bottom');
    dom.xrayAxisLeft  = document.getElementById('xray-axis-left');
    dom.xrayAxisRight = document.getElementById('xray-axis-right');
    dom.xrayLegend    = document.getElementById('xray-legend');
    dom.xrayBars      = document.getElementById('xray-bars');
    dom.xrayBarsHeading = document.getElementById('xray-bars-heading');
    dom.xrayBarsNote  = document.getElementById('xray-bars-note');
    dom.xrayRoundHeading = document.getElementById('xray-round-heading');
    dom.xrayStrip     = document.getElementById('xray-strip');
    dom.xrayRoundNote = document.getElementById('xray-round-note');
    dom.xrayTypes     = document.getElementById('xray-types');
    dom.xrayGuesses   = document.getElementById('xray-guesses');
    dom.xrayGuessHeading = document.getElementById('xray-guess-heading');
    dom.xrayGuessNote = document.getElementById('xray-guess-note');
    dom.xrayEngineering = document.getElementById('xray-engineering');
    dom.xrayDetails   = document.getElementById('xray-details');
    dom.xrayTip       = document.getElementById('xray-tip');
    dom.languageToggle = document.getElementById('language-toggle');
    dom.languageHint = document.getElementById('language-hint');
    dom.jevOptIn = document.getElementById('jev-opt-in');
    dom.xraySessionOptions = document.getElementById('xray-session-options');
    dom.roundFeedbackOptIn = document.getElementById('round-feedback-opt-in');
    dom.roundFeedbackStatus = document.getElementById('round-feedback-status');
    dom.jevStatus = document.getElementById('jev-status');
    dom.playFeedback = document.getElementById('play-feedback');
    dom.portrait = document.getElementById('portrait');
    dom.collectChoice = document.getElementById('collect-choice');
    dom.collectOptIn = document.getElementById('collect-opt-in');
    dom.welcomeSubtitle = document.getElementById('welcome-subtitle');
    dom.welcomePromise = document.getElementById('welcome-promise');
    dom.welcomeScope = document.getElementById('welcome-scope');
    dom.welcomeLength = document.getElementById('welcome-length');
    dom.entryPlayNote = document.getElementById('entry-play-note');
    dom.gameProgress = document.getElementById('game-progress');
    dom.finaleOverlay = document.getElementById('finale-overlay');
    dom.finaleTitle = document.getElementById('finale-title');
    dom.finaleDescription = document.getElementById('finale-description');
    dom.finaleSeal = document.getElementById('finale-seal');
    dom.finaleGrid = document.getElementById('finale-grid');
    dom.finaleStatus = document.getElementById('finale-status');
    dom.finaleRetry = document.getElementById('finale-retry');
    dom.finaleFlip = document.getElementById('finale-flip');
    dom.finaleContinue = document.getElementById('finale-continue');
    dom.finaleProof = document.getElementById('finale-proof');
    dom.finaleProofText = document.getElementById('finale-proof-text');
    dom.finaleRestart = document.getElementById('finale-restart');
    dom.sharePanel = document.getElementById('share-panel');
    dom.shareCard = document.getElementById('share-card');
    dom.shareStatus = document.getElementById('share-status');
    dom.inviteFriend = document.getElementById('invite-friend');
    dom.jevEntryChoice = document.getElementById('jev-entry-choice');
    dom.jevEntryOptIn = document.getElementById('jev-entry-opt-in');

    if (i18n) i18n.bind(dom.languageToggle, () => {
      updateRuntimeNotice(state.analysisResult);
      syncEntryChoice();
      updateRecordNote();
      if (state.analysisResult) updateRecordExport(state.analysisResult);
      syncJevChoice();
      renderPlayFeedback();
      if (state.analysisResult) renderProductResult(state.analysisResult);
      if (LAST_CARD) { syncGameProgress(); renderFinaleText(); }
      if (state.finaleResult) renderFinaleXray(state.finaleResult);
      if (state.analysisResult || state.pendingAnalysisPayload) {
        dom.languageHint.hidden = false;
        dom.languageHint.textContent = tr('界面语言已切换；现有判词和追问保持原语言，下一次编织使用新语言。');
      }
      if (state.xrayTrajectory) {
        const round = state.xrayRound;
        state.xrayDetailsFor = null;
        showXrayTrajectory(state.xrayTrajectory);
        state.xrayRound = -1;
        showXrayRound(round);
      }
      syncDialogueCounter();
    });

    state.ambientController = initAmbientBackground();
    updateRuntimeNotice();

    dom.startBtn.addEventListener('click', () => startTest(LAST_CARD ? COLLECT && dom.collectOptIn.checked === true : COLLECT));
    dom.startPrivate.addEventListener('click', () => startTest(false));
    dom.jevOptIn.addEventListener('change', () => {
      if (state.entryLocked || !state.jevAvailable) {
        dom.jevOptIn.checked = state.jevEnabled;
        return;
      }
      state.jevEnabled = dom.jevOptIn.checked === true;
      state.jevChoiceTouched = true;
      syncEntryChoice();
      syncJevChoice();
    });
    dom.roundFeedbackOptIn.addEventListener('change', () => {
      if (state.entryLocked || !DECK) {
        dom.roundFeedbackOptIn.checked = state.roundFeedbackEnabled;
        return;
      }
      state.roundFeedbackEnabled = dom.roundFeedbackOptIn.checked === true;
      syncFeedbackChoice();
    });
    dom.withdrawRecord.addEventListener('click', withdrawRecord);
    dom.forgetVisitor.addEventListener('click', forgetVisitor);
    window.addEventListener('storage', onVisitorStorageChange);
    dom.nextBtn.addEventListener('click', nextRound);
    dom.revealBtn.addEventListener('click', revealDestiny);
    if (dom.restartBtn) {
      dom.restartBtn.addEventListener('click', restartTest);
    }
    dom.dialogueForm.addEventListener('submit', handleDialogueSubmit);
    dom.dialogueInput.addEventListener('input', syncDialogueCounter);
    dom.dialogueInput.addEventListener('keydown', handleDialogueKeydown);
    dom.retryAnalysis.addEventListener('click', retryAnalysis);
    dom.retryAssets.addEventListener('click', loadCards);
    dom.finaleRetry.addEventListener('click', retryFinale);
    dom.finaleFlip.addEventListener('click', flipFinale);
    dom.finaleContinue.addEventListener('click', continueFinaleReading);
    dom.finaleRestart.addEventListener('click', restartTest);
    dom.shareCard.addEventListener('click', downloadShareCard);
    dom.inviteFriend.addEventListener('click', inviteFriend);
    dom.jevEntryOptIn.addEventListener('change', () => {
      if (state.entryLocked || !state.jevAvailable) { syncJevChoice(); return; }
      state.jevChoiceTouched = true;
      state.jevEnabled = dom.jevEntryOptIn.checked === true;
      syncJevChoice();
    });
    dom.exportRecord.addEventListener('click', exportSessionRecord);
    dom.feedbackSave.addEventListener('click', submitFeedback);
    dom.feedbackSkip.addEventListener('click', skipFeedback);
    initializeXray();
    initializeAudioControls();
    state.jevStatusPromise = initializeJev();
    await loadCards();
  }

  function syncEntryChoice() {
    if (LAST_CARD) {
      dom.startBtn.textContent = tr('开始读心游戏');
      dom.restartBtn.textContent = tr('再来一局');
      dom.finaleRestart.textContent = tr('再来一局');
      dom.startPrivate.hidden = true;
      dom.entryLead.hidden = true;
      dom.collectChoice.hidden = !COLLECT;
      dom.entryDetail.hidden = !COLLECT;
      dom.welcomeSubtitle.textContent = tr('命运底牌 · 看图读心');
      dom.welcomePromise.textContent = tr('选几轮让你心动的图，最后考一考织机：它能猜中你的最爱吗？再带走一张属于你的命运角色卡。');
      dom.welcomeScope.textContent = tr('AI 画面 · 娱乐性格解读 · 无需登录');
      dom.welcomeLength.textContent = tr('想继续就继续，想揭晓就进入终局。每局只有一次翻牌，最多探索 12 轮。');
      dom.entryPlayNote.textContent = COLLECT
        ? tr('匿名贡献由你决定，不勾选也能获得完整解读与分享卡。')
        : tr('本次选择只用于这一局，不留研究档案。');
      syncForgetChoice();
      return;
    }
    dom.collectChoice.hidden = true;
    // Without storage there is nothing to consent to: one plain entrance.
    dom.startBtn.textContent = COLLECT ? tr('织 入 命 运') : tr('开始猜牌试玩');
    dom.startPrivate.textContent = tr('私密体验 · 不留学习记录');
    [dom.startPrivate, dom.entryLead, dom.entryDetail].forEach(element => { element.hidden = !COLLECT; });
    syncForgetChoice();
  }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (_) { /* private mode: the key lives only for this page */ }
  }

  function visitorKey(create = false) {
    const known = storageGet(VISITOR_STORAGE_KEY) || pageVisitorKey;
    if (known && /^[A-Za-z0-9_-]{22,64}$/.test(known)) return known;
    if (!create) return null;
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    pageVisitorKey = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    storageSet(VISITOR_STORAGE_KEY, pageVisitorKey);
    return pageVisitorKey;
  }

  function threadCount() {
    return Math.max(0, parseInt(storageGet(THREADS_STORAGE_KEY), 10) || 0);
  }

  function setThreadCount(count) {
    storageSet(THREADS_STORAGE_KEY, count > 0 ? String(count) : null);
  }

  function syncForgetChoice(text = null) {
    const known = COLLECT && Boolean(visitorKey());
    dom.entryForget.hidden = !known && !text;
    dom.forgetVisitor.hidden = !known;
    const threads = threadCount();
    dom.entryForgetText.textContent = text
      || (threads ? tr`这台浏览器已留下 ${threads} 根线。` : tr('这台浏览器留有一个匿名编号。'));
  }

  function stopCollection() {
    state.consent = false;
    state.collectionEpoch += 1;
    state.sessionKey = null;
    state.record = null;
    cancelPartialVisit();
  }

  function onVisitorStorageChange(event) {
    if (event.key !== null && event.key !== VISITOR_STORAGE_KEY) return;
    if (event.key !== null && event.oldValue === event.newValue) return;
    // Another tab withdrew or replaced the identity this visit consented with.
    // Do not silently continue this visit under a newly created identity.
    stopCollection();
    pageVisitorKey = null;
    syncForgetChoice();
    updateRecordNote();
  }

  async function forgetVisitor() {
    const key = visitorKey();
    if (!key || !hasBackendConfig()) return;
    // Stop immediately, including while the withdrawal request is in flight or
    // its result is unknown. Keep the key until confirmation so retry is possible.
    stopCollection();
    updateRecordNote();
    dom.forgetVisitor.disabled = true;
    try {
      const resp = await fetch(`${API_ENDPOINT}/record/withdraw`, {
        method: 'POST', headers: buildApiHeaders(), body: JSON.stringify({ visitor: key, all: true }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.withdrawn !== true) throw new Error(data.error || `HTTP ${resp.status}`);
      storageSet(VISITOR_STORAGE_KEY, null);
      setThreadCount(0);
      pageVisitorKey = null;
      syncForgetChoice(tr('已全部撤回，这台浏览器也不再记得你。'));
    } catch (error) {
      syncForgetChoice(tr`撤回没有完成：${error.message || tr('服务暂时不可用')}，可以再试一次。`);
    } finally {
      dom.forgetVisitor.disabled = false;
    }
  }

  async function loadCards() {
    dom.startBtn.disabled = true;
    dom.startPrivate.disabled = true;
    dom.retryAssets.hidden = true;
    dom.assetStatus.textContent = tr('正在准备画面……');
    try {
      if (DECK) {
        await loadResearchDeck();
      } else {
        const resp = await fetch('./data/cards.json');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const cards = await resp.json();
        if (!Array.isArray(cards) || cards.length === 0) throw new Error('Card manifest is empty');
        state.allCards = cards;
        state.displayFeatures = await loadDisplayFeatures();
      }
      await ensureBackendConfig();
      if (LAST_CARD && state.jevStatusPromise) await state.jevStatusPromise;
      syncEntryChoice();
      dom.startBtn.disabled = false;
      dom.startPrivate.disabled = false;
      dom.assetStatus.textContent = '';
    } catch (err) {
      console.error('[Norn] Failed to load cards:', err);
      dom.assetStatus.textContent = window.location.protocol === 'file:'
        ? tr('请先运行 norn serve --mock，再打开终端中的本地地址。')
        : tr('画面清单暂时无法读取。请确认本地服务正在运行，再重试。');
      dom.retryAssets.hidden = false;
    }
  }

  // A research deck ships its cards, features and credit in one file; without it there is nothing to show.
  async function loadResearchDeck() {
    const resp = await fetch(`./data/decks/${DECK}.json`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const doc = await resp.json();
    if (!doc || doc.schema_version !== 'norn.research-deck.browser.v1' || doc.deck !== DECK
        || !Array.isArray(doc.cards) || !doc.cards.length
        || !doc.cards.every(card => Array.isArray(card.x2) && card.x2.length === DECK_FEATURE_COUNT
                                    && card.x2.every(Number.isFinite))) {
      throw new Error('Invalid research deck');
    }
    state.allCards = doc.cards.map(card => ({ id: card.id, url: card.url }));
    state.displayFeatures = new Map(doc.cards.map(card => [card.id, card.x2]));
    state.deckLabel = `${doc.deck}@${doc.deck_version}`;
    state.slotContext = slotContext(doc.context);
    if (dom.deckCredit) {
      dom.deckCredit.textContent = tr`画面来自 ${doc.credit}。`;
      dom.deckCredit.hidden = false;
    }
  }

  // research_deck's population context (offsets per card count, stop slope), or null without one.
  function slotContext(context) {
    if (context === undefined || context === null) return null;
    const valid = context && context.schema_version === 'norn.slot-context.v1' && Number.isFinite(context.gamma)
      && context.offsets && Object.entries(context.offsets).every(([count, values]) =>
        Array.isArray(values) && values.length === Number(count) && values.every(Number.isFinite));
    if (!valid) throw new Error('Invalid deck population context');
    return { offsets: context.offsets, gamma: context.gamma };
  }

  // Without the features a mixed session still runs, every round logged as random.
  async function loadDisplayFeatures() {
    try {
      const resp = await fetch(DISPLAY_FEATURES_URL);
      if (!resp.ok) return null;
      const doc = await resp.json();
      if (!doc || doc.schema_version !== 'norn.display-features.v1' || !doc.cards) return null;
      const features = new Map();
      for (const [id, row] of Object.entries(doc.cards)) {
        if (!Array.isArray(row) || row.length !== DISPLAY_FEATURE_COUNT || !row.every(Number.isFinite)) return null;
        features.set(id, row);
      }
      return features;
    } catch (_) {
      return null;
    }
  }

  function updateRuntimeNotice(result = null) {
    const simulated = result ? result.simulated === true || result.provider === 'mock' : PROVIDER === 'mock';
    const label = simulated ? tr`${HOSTING === 'cloudflare' ? tr('在线演示') : tr('本地演示')} · 模拟文案 · 无模型调用`
      : HOSTING === 'cloudflare' ? tr('DeepSeek V4.1 Flash · 实时解读') : tr('文案由所配置的模型生成');
    dom.runtimeNotice.textContent = label;
    dom.resultMode.textContent = label;
  }

  // ─── Audio FX ────────────────────────────────────────────
  let audioCtx = null;

  function getAudioContext() {
    if (audioCtx) return audioCtx;
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return null;
    audioCtx = new AudioCtor();
    return audioCtx;
  }

  function clampVolume(value) {
    const parsed = Number.parseFloat(value);
    if (!Number.isFinite(parsed)) return 0.32;
    return Math.min(1, Math.max(0, parsed));
  }

  function readVolumeSlider() {
    return clampVolume(dom.volumeSlider ? dom.volumeSlider.value : state.masterVolume);
  }

  function applyMasterVolume(value = readVolumeSlider()) {
    state.masterVolume = clampVolume(value);
    if (dom.ambientAudio) {
      dom.ambientAudio.volume = state.masterVolume;
    }
    if (dom.volumeSlider && Number.parseFloat(dom.volumeSlider.value) !== state.masterVolume) {
      dom.volumeSlider.value = String(state.masterVolume);
    }
  }

  function initializeAudioControls() {
    applyMasterVolume();
    if (!dom.volumeSlider) return;
    dom.volumeSlider.addEventListener('input', (event) => {
      applyMasterVolume(event.target.value);
    });
    dom.volumeSlider.addEventListener('change', (event) => {
      applyMasterVolume(event.target.value);
    });
  }

  function unlockAudioContext() {
    const ctx = getAudioContext();
    if (!ctx) return Promise.resolve(null);
    if (ctx.state !== 'suspended') return Promise.resolve(ctx);
    return ctx.resume()
      .then(() => ctx)
      .catch(err => {
        console.warn('[Norn] Audio context could not resume:', err);
        return null;
      });
  }
  
  function playRippleSound(isSelect) {
    unlockAudioContext().then(ctx => {
      if (!ctx || state.masterVolume <= 0.001) return;

      const now = ctx.currentTime;
      const gainNode = ctx.createGain();
      const primary = ctx.createOscillator();
      const shimmer = ctx.createOscillator();
      const baseFreq = isSelect ? 720 : 420;
      const peak = (isSelect ? 0.18 : 0.12) * state.masterVolume;

      primary.type = 'sine';
      shimmer.type = 'triangle';
      primary.frequency.setValueAtTime(baseFreq, now);
      primary.frequency.exponentialRampToValueAtTime(baseFreq * 0.42, now + 0.24);
      shimmer.frequency.setValueAtTime(baseFreq * 1.5, now);
      shimmer.frequency.exponentialRampToValueAtTime(baseFreq * 0.72, now + 0.34);

      gainNode.gain.setValueAtTime(0.0001, now);
      gainNode.gain.exponentialRampToValueAtTime(peak, now + 0.018);
      gainNode.gain.exponentialRampToValueAtTime(0.0001, now + 0.42);

      primary.connect(gainNode);
      shimmer.connect(gainNode);
      gainNode.connect(ctx.destination);

      primary.start(now);
      shimmer.start(now + 0.018);
      primary.stop(now + 0.45);
      shimmer.stop(now + 0.42);
    });
  }

  // ─── Welcome → Test ──────────────────────────────────────
  function startTest(consent = false) {
    if (!state.allCards.length || state.entryLocked || dom.welcomeOverlay.classList.contains('dismissed')) return;
    state.entryLocked = true;
    if (dom.xraySessionOptions) dom.xraySessionOptions.open = false;
    state.consent = Boolean(consent && COLLECT);
    state.playMode = Boolean(DECK);
    if (LAST_CARD) state.cardsPerRound = { min: 4, max: 4 };
    state.experienceStartedAt = performance.now();
    state.experience = DECK ? { schema_version: 'norn.experience.v1',
      round_feedback: state.roundFeedbackEnabled ? 'on' : 'off', exposures: [] } : null;
    syncJevChoice();
    if (state.jevEnabled && state.playMode) startJevPlay();
    if (state.xrayOpen) scheduleXrayRefresh(0, true);
    // Without consent nothing would be kept, so the pilot's new images are not shown.
    if (PILOT_PROTOCOL && state.consent) loadProbeDeck(PILOT_PROTOCOL);
    dom.welcomeOverlay.classList.add('dismissed');
    dom.testArea.classList.add('active');
    unlockAudioContext();
    startAmbientAudio();
    drawRound();
  }

  // ─── Draw a Round ────────────────────────────────────────
  function appendEvent(type, details = {}) {
    if (state.sessionSealed) return;
    const sequence = state.eventJournal.events.length + 1;
    state.eventJournal.events.push(Object.freeze({
      event_id: `evt-${String(sequence).padStart(6, '0')}`,
      sequence,
      type,
      ...details,
    }));
    if (state.playMode && !probeActive()) {
      if (type === 'round_started') precommitPlayRound(details);
      if (type === 'round_completed') settlePlayRound(details);
    }
    if (state.xraySource === 'live' && !probeActive()) {
      state.xrayPrefix = null;
      scheduleXrayRefresh();
    }
  }

  function syncJevChoice() {
    syncFeedbackChoice();
    if (!dom.jevOptIn) return;
    dom.jevOptIn.checked = state.jevEnabled;
    dom.jevOptIn.disabled = state.entryLocked || !state.jevAvailable;
    if (dom.jevEntryChoice) dom.jevEntryChoice.hidden = !LAST_CARD || !state.jevAvailable;
    if (dom.jevEntryOptIn) {
      dom.jevEntryOptIn.checked = state.jevEnabled;
      dom.jevEntryOptIn.disabled = state.entryLocked || !state.jevAvailable;
    }
    dom.jevStatus.textContent = state.entryLocked
      ? LAST_CARD ? tr(state.jevEnabled ? '本局 Jev 已开启 · 终局只封存一次，开局后不能更改。' : '本局 Jev 已关闭 · 开局后不能更改。')
        : tr(state.jevEnabled ? '本次已开启 · 开局后不能更改；最多 8 轮，不自动重试。' : '本次未开启 · 开局后不能更改。')
      : state.jevAvailable
        ? LAST_CARD ? tr('终局对照一次 · 此前选择与图片特征会发送给 Typesafe；开局前可关闭。')
          : tr('可选付费模型 · 此前选图会发给 Typesafe，最多 8 轮；研究体验到结尾才揭晓。')
        : tr('Jev 暂不可用；本地学习模型和基线照常运行。');
    if (state.jevMessage) dom.jevStatus.textContent = tr(state.jevMessage);
  }

  function syncFeedbackChoice() {
    if (!dom.roundFeedbackOptIn) return;
    dom.roundFeedbackOptIn.checked = state.roundFeedbackEnabled;
    dom.roundFeedbackOptIn.disabled = state.entryLocked || !DECK;
    dom.roundFeedbackStatus.textContent = state.entryLocked
      ? tr(state.roundFeedbackEnabled ? '本次已开启逐轮反馈；开局后不能更改。' : '逐轮反馈已关闭；结尾才揭晓猜牌回顾。')
      : tr('默认关闭 · 开启后每轮揭晓；若选择匿名织入，会记录开关与展示轮次。');
  }

  function snapshotExperience() {
    return state.experience ? JSON.parse(JSON.stringify(state.experience)) : null;
  }

  function recordRoundFeedback(last) {
    if (!last || !state.roundFeedbackEnabled || !state.experience
        || state.experience.exposures.some(row => row.after_round === last.round)
        || state.feedbackExposurePending.has(last.round)) return;
    state.feedbackExposurePending.add(last.round);
    requestAnimationFrame(() => {
      state.feedbackExposurePending.delete(last.round);
      // Record a painted, visible hint, never a reveal hidden by the final overlay.
      if (state.sessionSealed || pageHidden() || dom.playFeedback.hidden
          || (state.xrayOpen && window.innerWidth < 1100)) return;
      const results = Array.from(state.playResults.values());
      if (results[results.length - 1] !== last) return;
      const box = dom.playFeedback.getBoundingClientRect();
      if (box.width <= 0 || box.height <= 0 || box.top >= window.innerHeight || box.bottom <= 0) return;
      const previous = state.experience.exposures[state.experience.exposures.length - 1];
      if (previous && previous.after_round >= last.round) return;
      state.experience.exposures.push(Object.freeze({ after_round: last.round,
        feedback: last.target === null ? 'blank' : !last.scored ? 'unscored' : last.hit ? 'hit' : 'miss',
        elapsed_ms: Math.min(MAX_ROUND_MS, Math.max(0, Math.round(performance.now() - state.experienceStartedAt))) }));
      schedulePartialVisit();
    });
  }

  async function initializeJev() {
    let deadline;
    try {
      const timeout = new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Status timeout')), 8000); });
      const status = await Promise.race([xrayRequest('/jev/status'), timeout]);
      state.jevAvailable = status.available === true && Boolean(DECK);
      if (LAST_CARD && state.jevAvailable && !state.entryLocked && !state.jevChoiceTouched) state.jevEnabled = true;
    } catch (_) { state.jevAvailable = false; }
    finally { clearTimeout(deadline); }
    syncJevChoice();
  }

  function startJevPlay() {
    state.jevStart = (async () => {
      try {
        // Independent of archival identity; never placed in exports or localStorage.
        if (typeof crypto === 'undefined' || !crypto.getRandomValues) throw new Error('No secure randomness');
        const client = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
        const result = await xrayRequest('/jev/start', { enabled: true, mode: 'play', deck: state.deckLabel, client_session: client,
          ...(LAST_CARD ? { purpose: 'finale' } : {}) });
        if (result.status !== 'ready' || typeof result.jev_session !== 'string') throw new Error('Jev unavailable');
        return result.jev_session;
      } catch (_) {
        state.jevMessage = 'Jev 未能开启；继续本地两路试玩，不自动重试。';
        syncJevChoice();
        return null;
      }
    })();
  }

  function beforeFirstChoice(round) {
    return !state.eventJournal.events.some(event => event.round === round
      && ['card_selected', 'card_deselected', 'round_completed'].includes(event.type));
  }

  function precommitPlayRound(details) {
    // Clone synchronously, before image readiness and before any choice is possible.
    const prefix = JSON.parse(JSON.stringify(mainJournal()));
    const round = details.round;
    const ids = details.card_ids.slice();
    const forecast = { round, ids, status: 'pending', timely: false };
    state.playForecasts.set(round, forecast);
    renderPlayFeedback();
    (async () => {
      try {
        const data = await xrayRequest('/preview', { journal: prefix, compact: true,
          ...(LAST_CARD ? { product: LAST_CARD_PRODUCT } : {}) });
        if (LAST_CARD) renderPacing(data.pacing, round);
        const weights = data.preference && data.preference.effective;
        if (!Array.isArray(weights) || weights.length !== DECK_FEATURE_COUNT
            || !weights.every(Number.isFinite) || !state.displayFeatures) throw new Error('No forecast');
        const scores = ids.map(id => {
          const features = state.displayFeatures.get(id);
          if (!features || features.length !== weights.length) throw new Error('No features');
          return features.reduce((sum, value, k) => sum + value * weights[k], 0);
        });
        // A tied top rank is not a single committed guess and receives no hit credit.
        const maximum = Math.max(...scores);
        const tops = ids.filter((id, index) => Math.abs(scores[index] - maximum) < 1e-10);
        Object.assign(forecast, { status: 'ready', top: tops.length === 1 ? tops[0] : null,
          timely: beforeFirstChoice(round), layer: data.preference.status });
      } catch (_) { forecast.status = 'unavailable'; }
      renderPlayFeedback();
    })();
    if (!LAST_CARD && state.jevEnabled && state.jevStart && round <= 8) {
      (async () => {
        const token = await state.jevStart;
        if (!token) return;
        try {
          const result = await xrayRequest('/jev/predict', { jev_session: token, journal: prefix });
          state.jevPredictions.set(round, { ...result, timely: beforeFirstChoice(round) });
          state.jevRevision++;
          if (result.status !== 'ok') throw new Error('Jev unavailable');
        } catch (_) {
          state.jevMessage = '部分轮次 Jev 未返回结果；已有预测仍可回放，不自动重试。';
          syncJevChoice();
        }
        if (state.xraySource === 'live' && state.xrayTrajectory) showXrayRound(state.xrayRound);
      })();
    }
  }

  function settlePlayRound(details) {
    const rows = displayRows(mainJournal().events);
    const row = rows[rows.length - 1];
    const forecast = state.playForecasts.get(details.round);
    const target = row && row.picks.length ? row.picks[0] : null;
    state.playResults.set(details.round, { round: details.round, target,
      scored: Boolean(target !== null && forecast && forecast.status === 'ready' && forecast.timely && forecast.top !== null),
      hit: Boolean(forecast && forecast.top === target), top: forecast && forecast.top });
    renderPlayFeedback();
  }

  function renderPlayFeedback() {
    if (!dom.playFeedback) return;
    dom.playFeedback.hidden = !state.playMode || !state.roundFeedbackEnabled;
    if (dom.playFeedback.hidden) return;
    dom.playFeedback.innerHTML = '';
    const results = Array.from(state.playResults.values());
    const last = results[results.length - 1];
    const scored = results.filter(row => row.scored);
    const hits = scored.filter(row => row.hit).length;
    const message = !last ? tr('织机正在悄悄猜。选好后翻到下一轮，看看有没有猜中。')
      : last.target === null ? tr('这一轮留白。织机继续观察，不计输赢。')
        : !last.scored ? tr('这一轮没有及时完成单张预测，不补算命中。')
          : last.hit ? tr('✦ 猜中了！你首张保留的，正是织机事先押下的那张。')
            : tr('这次你给了织机一个意外。下一轮再试。');
    xrayText(dom.playFeedback, message, 'span');
    if (scored.length) xrayText(dom.playFeedback, tr`已猜中 ${hits} / ${scored.length} 轮`, 'small');
    xrayClass(dom.playFeedback, 'hit', Boolean(last && last.scored && last.hit));
    recordRoundFeedback(last);
  }

  function renderPortrait(portrait) {
    if (!dom.portrait) return;
    dom.portrait.innerHTML = '';
    dom.portrait.hidden = !portrait || portrait.schema_version !== 'norn.product-portrait.v1';
    if (dom.portrait.hidden) return;
    xrayText(dom.portrait, portrait.title, 'h3');
    const gallery = document.createElement('div');
    gallery.className = 'portrait-gallery';
    gallery.style.gridTemplateColumns = `repeat(${Math.max(1, Math.min(3, (portrait.card_ids || []).length))}, minmax(0, 1fr))`;
    (portrait.card_ids || []).forEach(id => {
      const card = state.allCards.find(item => item.id === id);
      if (!card) return;
      const image = document.createElement('img');
      image.className = 'portrait-image';
      image.src = card.url;
      image.alt = tr('本次保留的画面');
      image.loading = 'lazy';
      gallery.appendChild(image);
    });
    dom.portrait.appendChild(gallery);
    xrayText(dom.portrait, portrait.subtitle, 'p');
    if (portrait.motifs && portrait.motifs.length) xrayText(dom.portrait, portrait.motifs.join(' · '), 'small');
    const results = Array.from(state.playResults.values()).filter(row => row.scored);
    if (results.length) {
      const hits = results.filter(row => row.hit).length;
      xrayText(dom.portrait, tr`事先猜中 ${hits} / ${results.length} 轮 · 打开 X-Ray 回看`, 'p', 'portrait-score');
    }
  }

  function renderProductResult(data) {
    const persona = data && data.persona;
    if (!persona || persona.schema_version !== 'norn.persona.v1') {
      dom.sharePanel.hidden = true;
      renderPortrait(data && data.portrait);
      return;
    }
    const card = state.allCards.find(item => item.id === persona.card_id);
    if (!card) { renderPortrait(data.portrait); return; }
    dom.portrait.hidden = false;
    dom.portrait.innerHTML = '';
    dom.portrait.className = 'portrait persona-card';
    dom.portrait.setAttribute('aria-label', tr('你的命运角色'));
    xrayText(dom.portrait, tr('你的命运角色'), 'p', 'persona-eyebrow');
    xrayText(dom.portrait, persona.title, 'h3', 'persona-title');
    xrayText(dom.portrait, persona.tagline, 'p', 'persona-tagline');
    const image = document.createElement('img');
    image.src = card.url;
    image.alt = tr('本局最爱的画面');
    image.className = 'persona-image';
    dom.portrait.appendChild(image);
    const result = data.finale || state.finaleResult;
    if (result) {
      xrayText(dom.portrait, tr(result.hit ? '✦ 织机猜中了你的最爱' : '✦ 这一局，你给了织机一个意外'), 'p', 'persona-outcome');
      if (result.jev && result.jev.status === 'ok') xrayText(dom.portrait,
        tr(result.jev.hit ? 'Jev · 猜中' : 'Jev · 没猜中'), 'small', 'persona-jev');
    }
    xrayText(dom.portrait, persona.disclaimer || tr('根据本次选图创作的娱乐解读'), 'small', 'persona-disclaimer');
    dom.sharePanel.hidden = false;
    dom.shareCard.textContent = tr('保存我的命运卡');
    dom.inviteFriend.textContent = tr('邀请朋友来测');
  }

  function shareTextLines(context, text, width, maxLines) {
    const words = /\s/.test(text) ? text.trim().split(/\s+/).map(word => word + ' ') : Array.from(text);
    const lines = []; let line = '';
    const add = token => {
      if (context.measureText(line + token).width <= width) { line += token; return; }
      if (line.trim()) { lines.push(line.trim()); line = ''; }
      if (context.measureText(token).width > width && Array.from(token).length > 1) {
        for (const char of Array.from(token)) add(char);
      } else line = token;
    };
    words.forEach(add);
    if (line.trim()) lines.push(line.trim());
    if (lines.length <= maxLines) return lines;
    const kept = lines.slice(0, maxLines);
    let last = kept[maxLines - 1];
    while (last && context.measureText(last + '…').width > width) last = last.slice(0, -1);
    kept[maxLines - 1] = last.trim() + '…';
    return kept;
  }

  async function downloadShareCard() {
    const persona = state.analysisResult && state.analysisResult.persona;
    const result = state.analysisResult && state.analysisResult.finale || state.finaleResult;
    if (state.shareBusy || !persona || !result) return;
    const card = state.allCards.find(item => item.id === persona.card_id);
    if (!card) return;
    state.shareBusy = true;
    dom.shareCard.disabled = true;
    dom.shareStatus.textContent = tr('正在生成分享卡，图片只保存在你的设备……');
    try {
      const image = await new Promise((resolve, reject) => {
        const picture = new Image();
        picture.onload = () => resolve(picture);
        picture.onerror = () => reject(new Error('Image unavailable'));
        picture.src = card.url;
      });
      const english = persona.language === 'en';
      const canvas = document.createElement('canvas');
      canvas.width = 1200; canvas.height = 1640;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#0a0a14'; ctx.fillRect(0, 0, 1200, 1640);
      ctx.strokeStyle = '#756238'; ctx.lineWidth = 2; ctx.strokeRect(36, 36, 1128, 1568);
      ctx.fillStyle = '#b6a27a'; ctx.font = '22px Arial, sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(english ? 'THE NORN MACHINE / THE LAST CARD' : 'THE NORN MACHINE / 命运底牌', 600, 91);
      ctx.fillStyle = '#e4d0a4'; ctx.font = '58px Georgia, "Songti SC", serif';
      const title = shareTextLines(ctx, persona.title, 1000, 2);
      title.forEach((line, index) => ctx.fillText(line, 600, 176 + index * 68));
      const top = 225 + (title.length - 1) * 68, frameHeight = 700;
      ctx.fillStyle = '#14141f'; ctx.fillRect(72, top, 1056, frameHeight);
      const ratio = Math.min(1056 / image.naturalWidth, frameHeight / image.naturalHeight);
      const w = image.naturalWidth * ratio, h = image.naturalHeight * ratio;
      ctx.drawImage(image, 72 + (1056 - w) / 2, top + (frameHeight - h) / 2, w, h);
      ctx.fillStyle = '#ece4d4'; ctx.font = '40px Georgia, "Songti SC", serif';
      const quoteTop = top + frameHeight + 78;
      const quote = shareTextLines(ctx, persona.tagline, 990, 2);
      quote.forEach((line, index) => ctx.fillText(line, 600, quoteTop + index * 58));
      if (persona.invitation) {
        const questTop = Math.max(1150, quoteTop + quote.length * 58 + 25);
        ctx.fillStyle = '#b6a27a'; ctx.font = '19px Arial, "PingFang SC", sans-serif';
        ctx.fillText(english ? 'A LITTLE QUEST FOR THIS CHARACTER' : '给这个角色的一次小冒险', 600, questTop);
        ctx.fillStyle = '#d6cdbd'; ctx.font = '27px Georgia, "Songti SC", serif';
        shareTextLines(ctx, persona.invitation, 930, 3)
          .forEach((line, index) => ctx.fillText(line, 600, questTop + 42 + index * 34));
      }
      ctx.fillStyle = '#c9a84c'; ctx.font = '26px Arial, "PingFang SC", sans-serif';
      ctx.fillText(result.hit ? (english ? 'The loom guessed my favourite.' : '织机猜中了我的最爱。')
        : (english ? 'I surprised the loom this time.' : '这一局，我给了织机一个意外。'), 600, 1390);
      if (result.jev && result.jev.status === 'ok') {
        ctx.fillStyle = '#bfa8d6'; ctx.font = '22px Arial, "PingFang SC", sans-serif';
        ctx.fillText(english ? `Jev: ${result.jev.hit ? 'a hit' : 'a miss'}` : `Jev：${result.jev.hit ? '猜中' : '没猜中'}`, 600, 1432);
      }
      ctx.fillStyle = '#a3a0aa'; ctx.font = '19px Arial, "PingFang SC", sans-serif';
      shareTextLines(ctx, persona.disclaimer || (english ? 'An entertainment reading inspired by this visit’s choices.' : '根据本次选图创作的娱乐解读'), 980, 2)
        .forEach((line, index) => ctx.fillText(line, 600, 1512 + index * 27));
      ctx.fillStyle = '#c6b58f'; ctx.font = '20px Arial, sans-serif';
      ctx.fillText('norn-machine.dubianche123.workers.dev', 600, 1580);
      const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('PNG unavailable')), 'image/png'));
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `norn-last-card-${persona.language === 'en' ? 'en' : 'zh'}.png`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      dom.shareStatus.textContent = tr('分享卡已生成；可以把图片发给朋友。');
    } catch (_) { dom.shareStatus.textContent = tr('分享卡暂时未能生成，图片加载后可以重试。'); }
    finally { state.shareBusy = false; dom.shareCard.disabled = false; }
  }

  async function inviteFriend() {
    const link = 'https://norn-machine.dubianche123.workers.dev/';
    try {
      if (!window.navigator || !window.navigator.clipboard) throw new Error('Clipboard unavailable');
      await window.navigator.clipboard.writeText(link);
      dom.shareStatus.textContent = tr('体验链接已复制，邀请朋友也翻一张底牌。');
    } catch (_) { dom.shareStatus.textContent = link; }
  }

  function renderFinaleXray(result) {
    if (!dom.xrayLastCard) return;
    dom.xrayLastCard.hidden = !LAST_CARD || !result;
    if (dom.xrayLastCard.hidden) return;
    dom.xrayLastCard.innerHTML = '';
    xrayText(dom.xrayLastCard, tr('终局 · 封存的押注'), 'h3');
    xrayText(dom.xrayLastCard, tr('押注在你的最后一选之前封存；这里仅回放结果，不新增调用。'), 'p', 'xray-mini');
    const ids = result.candidate_ids || [];
    const valid = values => Array.isArray(values) && values.length === 4
      && values.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
      && Math.abs(values.reduce((sum, value) => sum + value, 0) - 1) < 1e-5;
    const learned = valid(result.probabilities) ? result.probabilities : null;
    const baseline = valid(result.baseline_probabilities) ? result.baseline_probabilities : null;
    const same = learned && baseline && learned.every((value, i) => Math.abs(value - baseline[i]) < 1e-10);
    const jev = result.jev && result.jev.status === 'ok' ? result.jev : null;
    const lanes = [];
    if (learned) lanes.push({ key: 'learning', name: tr(same ? '学习模型 = 参考基线' : '学习模型'),
      short: tr(same ? '学习 = 基线' : '学习'), values: learned });
    if (baseline && !same) lanes.push({ key: 'baseline', name: tr('参考基线'), short: tr('基线'), values: baseline });
    if (jev && valid(jev.probabilities)) lanes.push({ key: 'jev', name: 'Jev', short: 'Jev', values: jev.probabilities });

    const legend = document.createElement('div'); legend.className = 'xray-finale-legend';
    lanes.forEach(lane => {
      const chip = document.createElement('div'); chip.className = `xray-finale-lane ${lane.key}`;
      xrayText(chip, '', 'i');
      xrayText(chip, lane.name, 'span');
      if (lane.key === 'learning' || lane.key === 'jev')
        xrayText(chip, tr((lane.key === 'learning' ? result.hit : jev.hit) ? '猜中' : '没猜中'), 'b');
      legend.appendChild(chip);
    });
    dom.xrayLastCard.appendChild(legend);
    if (same) xrayText(dom.xrayLastCard, tr(result.layer === 'population'
      ? '本局回退到参考基线，两条分布相同，已合并展示。'
      : '学习模型与参考基线的分布相同，已合并展示。'), 'p', 'xray-finale-context');
    if (!jev || !valid(jev.probabilities)) xrayText(dom.xrayLastCard,
      tr(result.jev && result.jev.status === 'off' ? '本局 Jev 已关闭。' : 'Jev 未提供及时有效的分布，本次不计分。'), 'p', 'xray-finale-context');

    const grid = document.createElement('div'); grid.className = 'xray-finale-candidates';
    ids.forEach((id, index) => {
      const tile = document.createElement('article'); tile.className = 'xray-finale-candidate';
      tile.dataset.cardId = id;
      xrayClass(tile, 'favourite', id === result.chosen_card_id);
      const art = document.createElement('div'); art.className = 'xray-finale-art';
      const image = document.createElement('img');
      image.src = xrayCardUrl(id); image.alt = tr`终局画面 ${index + 1}`; image.loading = 'lazy';
      art.appendChild(image);
      xrayText(art, String(index + 1).padStart(2, '0'), 'span', 'xray-finale-number');
      if (id === result.chosen_card_id) xrayText(art, tr('你的最爱'), 'span', 'xray-finale-favourite');
      tile.appendChild(art);
      const guesses = document.createElement('div'); guesses.className = 'xray-finale-guesses';
      if (id === result.predicted_card_id) xrayText(guesses, tr('织机押注'), 'span', 'learning');
      if (jev && id === jev.predicted_card_id) xrayText(guesses, tr('Jev 押注'), 'span', 'jev');
      tile.appendChild(guesses);
      lanes.forEach(lane => {
        const row = document.createElement('div'); row.className = `xray-finale-probability ${lane.key}`;
        row.dataset.lane = lane.key;
        xrayText(row, lane.short, 'span', 'xray-finale-label');
        const track = document.createElement('span'); track.className = 'xray-finale-track';
        const probability = lane.values[index];
        track.setAttribute('role', 'meter'); track.setAttribute('aria-label', lane.name);
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
        track.setAttribute('aria-valuenow', String(Math.round(probability * 1000) / 10));
        const fill = document.createElement('i'); fill.style.width = `${probability * 100}%`;
        track.appendChild(fill); row.appendChild(track);
        xrayText(row, `${Math.round(probability * 100)}%`, 'b');
        tile.appendChild(row);
      });
      grid.appendChild(tile);
    });
    dom.xrayLastCard.appendChild(grid);
    xrayText(dom.xrayLastCard, tr('条形表示这四张图中的选择概率，不是性格准确率。'), 'p', 'xray-finale-context');
  }

  // ─── Pilot: new images after the reveal ──────────────────
  async function loadProbeDeck(protocol) {
    try {
      const resp = await fetch(PROBE_PROTOCOLS[protocol]);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const deck = await resp.json();
      if (deck.protocol !== protocol || !Array.isArray(deck.cards) || !deck.cards.length) throw new Error('Invalid probe deck');
      state.probe = { deck, rounds: null, journal: null, active: false, main: null };
      deck.cards.forEach(card => { const img = new Image(); img.src = card.url; });  // warm the cache
    } catch (error) {
      console.warn('[Norn] Pilot images unavailable; continuing without them.', error);
    }
  }

  function probeActive() {
    return Boolean(state.probe && state.probe.active);
  }

  function probeReady() {
    return Boolean(state.consent && state.probe && !state.probe.active && !state.probe.journal);
  }

  // The main journal, also while the probe journal is being written.
  function mainJournal() {
    return probeActive() ? state.probe.main.journal : state.eventJournal;
  }

  // Two whole scenes per round, both styles of each, in random positions.
  function probeRounds(deck) {
    const scenes = shuffle([...new Set(deck.cards.map(card => card.scene))]);
    const rounds = [];
    for (let start = 0; start < scenes.length; start += deck.scenes_per_round) {
      const chosen = new Set(scenes.slice(start, start + deck.scenes_per_round));
      rounds.push(shuffle(deck.cards.filter(card => chosen.has(card.scene))));
    }
    return rounds;
  }

  function startProbe() {
    const probe = state.probe;
    probe.main = { journal: state.eventJournal, selectedCards: state.selectedCards,
                   roundNumber: state.roundNumber, usedCardIds: state.usedCardIds };
    probe.rounds = probeRounds(probe.deck);
    probe.active = true;
    state.eventJournal = { schema_version: PROBE_SCHEMA_VERSION, protocol: probe.deck.protocol,
                           catalog_version: probe.deck.catalog_version, events: [] };
    state.selectedCards = [];
    state.roundNumber = 0;
    state.usedCardIds = new Set();
    dom.totalSelected.textContent = 0;
    if (dom.roundLabel) dom.roundLabel.textContent = tr('新画面');
    drawRound();
  }

  function endProbe() {
    const probe = state.probe;
    probe.journal = state.eventJournal;
    probe.active = false;
    state.eventJournal = probe.main.journal;
    state.selectedCards = probe.main.selectedCards;
    state.roundNumber = probe.main.roundNumber;
    state.usedCardIds = probe.main.usedCardIds;
    if (dom.roundLabel) dom.roundLabel.textContent = 'Round';
    dom.roundNum.textContent = state.roundNumber;
    dom.totalSelected.textContent = state.selectedCards.length;
  }

  function closeCurrentRound() {
    if (!state.roundOpen) return;
    clearPartialVisitTimers();
    const attention = state.roundAttention;
    const now = performance.now();
    if (attention.hiddenSince !== null) {
      attention.hiddenMs += now - attention.hiddenSince;
      attention.hiddenSince = null;
    }
    // Integer rounding must still satisfy ready + last choice <= duration.
    const offset = attention.readyOffsetMs === null ? 0 : attention.readyOffsetMs;
    const duration = Math.min(MAX_ROUND_MS, Math.max(offset + attention.lastChoiceMs,
      Math.round(now - state.roundStartTime)));
    closeHover(attention, duration - offset);
    appendEvent('round_completed', {
      round: state.roundNumber,
      duration_ms: duration,
      hidden_ms: Math.min(Math.round(attention.hiddenMs), duration - offset),
      seen_card_ids: attention.observer ? Object.freeze(attention.seen.slice()) : null,
      hover: attention.hoverable && attention.readyAt !== null
        ? Object.freeze(attention.hover.filter(segment => segment[2] <= duration - offset)) : null,
    });
    endRoundAttention(attention);
    state.roundOpen = false;
    state.roundStartTime = null;
  }

  function syncRoundControls() {
    const locked = state.roundTransitioning || state.sessionSealed;
    // Leave room for closing this round and sealing the session. A new round
    // also needs its own start/ready/close events.
    const ready = Boolean(state.roundAttention && state.roundAttention.readyAt !== null);
    const canToggle = ready && state.eventJournal.events.length < MAX_SESSION_EVENTS - 2;
    const canDraw = state.eventJournal.events.length <= MAX_SESSION_EVENTS - 5;
    const probeLast = probeActive() && state.roundNumber >= state.probe.rounds.length;
    dom.nextBtn.disabled = locked || !canDraw || state.roundNumber >= MAX_SESSION_ROUNDS || probeLast;
    dom.nextBtn.hidden = probeLast;  // the last new-image set has nothing after it, not something loading
    dom.revealBtn.disabled = locked || !state.roundOpen;
    dom.cardGrid.setAttribute('aria-busy', String(state.roundTransitioning || (state.roundOpen && !ready)));
    dom.cardGrid.querySelectorAll('.card').forEach(el => {
      el.setAttribute('aria-disabled', String(locked || !canToggle));
      el.setAttribute('tabindex', locked || !canToggle ? '-1' : '0');
    });
    dom.roundStatus.textContent = probeActive()
      ? (probeLast ? tr('这是最后一组新画面，选好后就揭示命运。')
        : tr('揭示之前，再看几组没见过的画面：同样选出触动你的，也可以直接揭示。'))
      : !canDraw || state.roundNumber >= MAX_SESSION_ROUNDS
        ? tr('这次留下的线已经足够，可以揭示命运了。') : '';
    if (LAST_CARD) {
      dom.nextBtn.textContent = tr(state.roundNumber >= LAST_CARD_MAX_ROUNDS ? '进入终局 →' : '继续探索 →');
      dom.revealBtn.textContent = tr('看看它的底牌');
      dom.roundStatus.textContent = state.roundNumber >= LAST_CARD_MAX_ROUNDS
        ? tr('已到本局 12 轮的体验上限，选好后进入终局；这不是学会偏好的门槛。') : '';
      syncGameProgress();
    }
  }

  function syncGameProgress() {
    if (!dom.gameProgress) return;
    dom.gameProgress.hidden = !LAST_CARD;
    if (!LAST_CARD) return;
    const suggestion = state.pacing && (state.pacing.latest && ['offer_finale', 'limit'].includes(state.pacing.latest.action)
      || state.pacing.suggest_finale === true
      || ['finale', 'stop', 'reveal'].includes(state.pacing.recommendation));
    dom.gameProgress.textContent = suggestion
      ? tr`探索第 ${state.roundNumber} 轮 · 可以试试终局了，由你决定`
      : tr`探索第 ${state.roundNumber} 轮 · 想揭晓时，随时看看底牌`;
    xrayClass(dom.revealBtn, 'recommended', Boolean(suggestion));
  }

  function renderPacing(pacing, round = state.roundNumber) {
    if (!LAST_CARD || state.sessionSealed || round !== state.roundNumber) return;
    state.pacing = pacing && typeof pacing === 'object' ? JSON.parse(JSON.stringify(pacing)) : null;
    syncGameProgress();
  }

  function drawRound() {
    if (state.roundTransitioning || state.sessionSealed || state.roundNumber >= MAX_SESSION_ROUNDS
        || (LAST_CARD && state.roundNumber >= LAST_CARD_MAX_ROUNDS)
        || state.eventJournal.events.length > MAX_SESSION_EVENTS - 5
        || (probeActive() && state.roundNumber >= state.probe.rounds.length)) return;
    closeCurrentRound();
    trackPageVisibility();
    state.roundTransitioning = true;
    syncRoundControls();

    state.roundNumber++;
    dom.roundNum.textContent = probeActive() ? `${state.roundNumber}/${state.probe.rounds.length}` : state.roundNumber;

    const count = LAST_CARD ? 4 : randInt(state.cardsPerRound.min, state.cardsPerRound.max);
    const available = state.allCards.filter(c => !state.usedCardIds.has(c.id));
    let display = null;

    if (probeActive()) {
      state.currentRoundCards = state.probe.rounds[state.roundNumber - 1];
    } else {
      if (available.length < count) state.usedCardIds.clear();
      const drawn = drawDisplayRound(available.length < count ? state.allCards.slice() : available, count);
      state.currentRoundCards = drawn.cards;
      display = Object.freeze({ policy: state.displayPolicy, roles: Object.freeze(drawn.roles),
                                ...(DECK ? { deck: state.deckLabel } : {}) });
    }

    state.currentRoundCards.forEach(c => state.usedCardIds.add(c.id));
    const round = state.roundNumber;
    const cards = state.currentRoundCards;
    const attention = createRoundAttention(round, cards);
    state.roundAttention = attention;
    renderCards(cards, () => {
      appendEvent('round_started', { round, card_ids: Object.freeze(cards.map(card => card.id)),
                                     ...(display ? { display } : {}) });
      state.roundStartTime = performance.now();
      state.roundOpen = true;
      state.roundTransitioning = false;
      attention.startedAt = state.roundStartTime;
      attention.hiddenSince = pageHidden() ? attention.startedAt : null;
      savePartialVisit();
      schedulePartialPulse();
      renderPlayFeedback();
      attention.readyTimer = setTimeout(() => markRoundReady(attention), ROUND_READY_TIMEOUT_MS);
      // Cached images may have settled before the round could open.
      maybeMarkRoundReady(attention);
      syncRoundControls();
    });
  }

  // ─── Display Policy ──────────────────────────────────────
  // The same rule as engine display_policy.py, which verifies logged rounds.
  // Only completed rounds reach the candidate, so the evidence gate stays valid.
  function drawDisplayRound(pool, count) {
    if (!state.displayPolicy) {
      const mixed = Math.random() < DISPLAY_MIXED_SHARE;
      state.displayPolicy = DECK ? (mixed ? DECK_MIXED : DECK_RANDOM) : (mixed ? DISPLAY_MIXED : DISPLAY_RANDOM);
    }
    const shuffled = shuffle(pool);
    let chosen = [];
    let roles = [];
    const mixed = state.displayPolicy === DISPLAY_MIXED || state.displayPolicy === DECK_MIXED;
    const scores = mixed ? (DECK ? researchScores(shuffled) : displayScores(shuffled)) : null;
    if (scores) {
      // Stable after the shuffle, so equal scores (an untrained candidate) tie at random.
      chosen = shuffled.map((card, index) => ({ card, index, score: scores.get(card.id) }))
        .sort((a, b) => b.score - a.score || a.index - b.index)
        .slice(0, Math.floor(count / 2)).map(item => item.card);
      roles = chosen.map(() => 'predicted');
      if (DECK) {
        // One card from the most popular quarter of the rest, one from the least popular (numpy.array_split).
        const taken = new Set(chosen.map(card => card.id));
        const rest = shuffled.filter(card => !taken.has(card.id))
          .sort((a, b) => state.displayFeatures.get(a.id)[0] - state.displayFeatures.get(b.id)[0]);
        const size = Math.floor(rest.length / 4);
        const bottom = rest.slice(0, size + (rest.length % 4 ? 1 : 0));
        const top = size ? rest.slice(rest.length - size) : rest.slice(-1);
        chosen.push(top[Math.floor(Math.random() * top.length)], bottom[Math.floor(Math.random() * bottom.length)]);
        roles.push('contrast_popular', 'contrast_unpopular');
      }
    }
    const taken = new Set(chosen.map(card => card.id));
    const filler = shuffled.filter(card => !taken.has(card.id)).slice(0, count - chosen.length);
    const cards = chosen.concat(filler);
    roles = roles.concat(filler.map(() => 'random'));
    const order = shuffle(cards.map((_, index) => index));
    return { cards: order.map(index => cards[index]), roles: order.map(index => roles[index]) };
  }

  // Research v6's L2 candidate: utility a*u + x8.delta, a ~ N(1, 1/q(n)), delta ~ N(0, 1/p(n)),
  // q(n) = 1.5 (1 + 1/max(1, n)), p(n) = 1.5 (1 + 8/max(1, n)), over completed rounds only.
  function researchScores(pool) {
    const features = state.displayFeatures;
    try {
      const rows = displayRows(state.eventJournal.events).filter(row => row.shown.length);
      if (!pool.every(card => features.has(card.id))
          || !rows.every(row => row.shown.every(id => features.has(id)))) return null;
      const n = Math.max(1, rows.length);
      const mean = [1].concat(new Array(DECK_FEATURE_COUNT - 1).fill(0), [0]);
      const precision = [1.5 * (1 + 1 / n)].concat(new Array(DECK_FEATURE_COUNT - 1).fill(1.5 * (1 + 8 / n)), [0.5]);
      const theta = fitSequentialLogit(choiceData(rows, features, state.slotContext), mean, precision,
                                       { damped: true, iterations: 400 });
      if (!theta) return null;
      return new Map(pool.map(card => [card.id, features.get(card.id)
        .reduce((sum, value, k) => sum + theta[k] * value, 0)]));
    } catch (_) {
      return null;
    }
  }

  function displayScores(pool) {
    const features = state.displayFeatures;
    if (!features) return null;
    try {
      const rows = displayRows(state.eventJournal.events);
      if (!pool.every(card => features.has(card.id))
          || !rows.every(row => row.shown.every(id => features.has(id)))) return null;
      const theta = fitDisplayCandidate(rows, features);
      if (!theta) return null;
      return new Map(pool.map(card => [card.id, features.get(card.id)
        .reduce((sum, value, k) => sum + theta[k] * value, 0)]));
    } catch (_) {
      return null;
    }
  }

  // Completed rounds as choice_model.observations reads them: final picks in
  // first-click order among the cards that reached the screen, then the stop.
  function displayRows(events) {
    const rows = [];
    const active = new Set();
    let shown = null;
    let order = [];
    events.forEach(event => {
      if (event.type === 'round_started') {
        shown = event.card_ids.slice();
        order = [];
      } else if (event.type === 'card_selected') {
        active.add(event.card_id);
        order.push(event.card_id);
      } else if (event.type === 'card_deselected') {
        active.delete(event.card_id);
      } else if (event.type === 'round_completed' && shown) {
        const visible = event.seen_card_ids;
        const compared = Array.isArray(visible) ? shown.filter(card => visible.includes(card)) : shown;
        const picks = [];
        order.forEach(card => {
          if (active.has(card) && !picks.includes(card) && compared.includes(card)) picks.push(card);
        });
        rows.push({ shown: compared, picks, slots: compared.map(card => shown.indexOf(card)), m: shown.length });
        shown = null;
      }
    });
    return rows;
  }

  // choice_policy._fit: Newton steps on the sequential logit with a stop option,
  // card-weight precision 1.5 * (1 + 12 / max(1, rounds with cards)), stop precision .5.
  function fitDisplayCandidate(rows, features) {
    const data = choiceData(rows.filter(row => row.shown.length), features);
    const precision = 1.5 * (1 + DISPLAY_FEATURE_COUNT / Math.max(1, data.length));
    const prior = Array.from({ length: DISPLAY_FEATURE_COUNT + 1 }, (_, k) => (k < DISPLAY_FEATURE_COUNT ? precision : 0.5));
    return fitSequentialLogit(data, new Array(DISPLAY_FEATURE_COUNT + 1).fill(0), prior, { damped: false, iterations: 100 });
  }

  // With a population context each card carries its slot offset and each step its stop offset,
  // gamma * log(cards remaining); without one every offset is 0 (research_model.card_offsets).
  function choiceData(rows, features, context = null) {
    return rows.map(row => {
      const table = context ? context.offsets[String(row.m)] : null;
      const steps = row.picks.map(id => row.shown.indexOf(id)).concat([null]);
      return { cards: row.shown.map(id => features.get(id)), steps,
               offsets: row.shown.map((_, j) => (table ? table[row.slots[j]] : 0)),
               stopOffsets: steps.map((_, i) => {
                 const remaining = row.shown.length - i;
                 return context && remaining > 0 ? context.gamma * Math.log(remaining) : 0;
               }) };
    });
  }

  // MAP of a sequential logit with a stop option under a Gaussian prior N(mean, 1/precision);
  // weights first, stop bias last. Damped steps halve until the posterior does not decrease
  // (research_model.fit); undamped steps are choice_policy._fit's.
  function fitSequentialLogit(data, mean, precision, { damped, iterations }) {
    const size = mean.length;
    const F = size - 1;
    const terms = theta => {
      let value = -0.5 * precision.reduce((sum, p, k) => sum + p * (theta[k] - mean[k]) ** 2, 0);
      const gradient = precision.map((p, k) => -p * (theta[k] - mean[k]));
      const hessian = precision.map((p, a) => precision.map((_, b) => (a === b ? p : 0)));
      data.forEach(({ cards, steps, offsets, stopOffsets }) => {
        const utilities = cards.map((x, j) => x.reduce((sum, v, k) => sum + theta[k] * v, 0) + offsets[j]);
        const top = Math.max(theta[F] + Math.max(...stopOffsets), ...utilities);
        const alive = utilities.map(v => Math.exp(v - top));
        steps.forEach((choice, i) => {
          const stop = theta[F] + stopOffsets[i];
          const stopMass = Math.exp(stop - top);
          const total = stopMass + alive.reduce((sum, v) => sum + v, 0);
          value += (choice === null ? stop : utilities[choice]) - top - Math.log(total);
          const p = alive.map(v => v / total);
          const average = new Array(size).fill(0);
          cards.forEach((x, j) => { for (let k = 0; k < F; k++) average[k] += p[j] * x[k]; });
          average[F] = stopMass / total;
          for (let a = 0; a < size; a++) {
            gradient[a] += (choice === null ? Number(a === F) : (a < F ? cards[choice][a] : 0)) - average[a];
            for (let b = 0; b < size; b++) {
              let second = a === F && b === F ? average[F] : 0;
              if (a < F && b < F) cards.forEach((x, j) => { second += p[j] * x[a] * x[b]; });
              hessian[a][b] += second - average[a] * average[b];
            }
          }
          if (choice !== null) alive[choice] = 0;
        });
      });
      return { value, gradient, hessian };
    };
    let theta = mean.slice();
    let current = terms(theta);
    for (let iteration = 0; iteration < iterations; iteration++) {
      const step = solveLinear(current.hessian, current.gradient);
      if (!step) return null;
      let scale = 1;
      let candidate;
      let next;
      for (;;) {
        candidate = theta.map((v, k) => v + scale * step[k]);
        next = terms(candidate);
        if (!damped || next.value >= current.value - 1e-12 || scale < 1e-6) break;
        scale /= 2;
      }
      theta = candidate;
      current = next;
      if (!theta.every(Number.isFinite)) return null;
      if (Math.max(...step.map(v => Math.abs(scale * v))) < 1e-9) return theta;
    }
    return null;
  }

  function solveLinear(matrix, vector) {
    const size = vector.length;
    const rows = matrix.map((row, r) => row.concat([vector[r]]));
    for (let column = 0; column < size; column++) {
      let pivot = column;
      for (let r = column + 1; r < size; r++) {
        if (Math.abs(rows[r][column]) > Math.abs(rows[pivot][column])) pivot = r;
      }
      if (rows[pivot][column] === 0) return null;
      [rows[column], rows[pivot]] = [rows[pivot], rows[column]];
      for (let r = column + 1; r < size; r++) {
        const factor = rows[r][column] / rows[column][column];
        for (let c = column; c <= size; c++) rows[r][c] -= factor * rows[column][c];
      }
    }
    const solution = new Array(size).fill(0);
    for (let r = size - 1; r >= 0; r--) {
      let total = rows[r][size];
      for (let c = r + 1; c < size; c++) total -= rows[r][c] * solution[c];
      solution[r] = total / rows[r][r];
    }
    return solution;
  }

  // ─── Render Cards ────────────────────────────────────────
  function renderCards(cards, onReady) {
    dom.cardGrid.dataset.cardCount = String(cards.length);
    const showCards = () => {
      dom.cardGrid.innerHTML = '';
      appendCards(cards);
      // Keep controls locked until the replacement has reached the render loop.
      requestAnimationFrame(onReady);
    };
    const existing = dom.cardGrid.querySelectorAll('.card');
    if (existing.length > 0) {
      existing.forEach(el => el.classList.add('exiting'));
      setTimeout(showCards, 400);
    } else {
      showCards();
    }
  }

  function appendCards(cards) {
    const renderedRound = state.roundNumber;
    const attention = state.roundAttention;
    cards.forEach((card, index) => {
      const el = document.createElement('div');
      el.className = 'card entering';
      el.dataset.id = card.id;
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');
      el.style.animationDelay = `${index * 0.12}s`;

      const isSelected = state.selectedCards.some(s => s.id === card.id);
      if (isSelected) el.classList.add('selected');
      el.setAttribute('aria-pressed', String(isSelected));

      const img = document.createElement('img');
      img.src = card.url;
      img.alt = card.id;
      img.draggable = false;
      img.onload = () => {
        el.classList.add('loaded');
        el.classList.remove('image-failed');
        symbol.textContent = '✦';
        settleCardImage(attention, card.id, true);
      };
      img.onerror = () => {
        el.classList.add('loaded', 'image-failed');
        el.style.background = 'linear-gradient(135deg, #1a1a2e, #2a1a3e)';
        symbol.textContent = tr('画面未能读取 · 点击重试');
        settleCardImage(attention, card.id, false);
      };

      const symbol = document.createElement('div');
      symbol.className = 'card-symbol';
      symbol.textContent = '✦';

      el.appendChild(img);
      el.appendChild(symbol);
      const activate = input => {
        if (state.roundTransitioning || state.sessionSealed || !state.roundOpen || renderedRound !== state.roundNumber) return;
        if (el.classList.contains('image-failed')) {
          el.classList.remove('loaded', 'image-failed');
          img.src = `${card.url}?retry=${Date.now()}`;
          return;
        }
        toggleCard(card, el, renderedRound, input);
      };
      // The device behind each choice: timings mean different things for mouse, touch and keys.
      let pressedWith = null;
      el.addEventListener('pointerdown', event => { pressedWith = event && event.pointerType; });
      el.addEventListener('click', event => {
        const kind = pressedWith || (event && event.pointerType);
        pressedWith = null;
        activate(POINTER_INPUTS.has(kind) ? kind : null);
      });
      el.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          activate('keyboard');
        }
      });
      el.addEventListener('pointerenter', event => {
        if (!attention || !event || (event.pointerType !== 'mouse' && event.pointerType !== 'pen')) return;
        attention.hovering = index;
        closeHover(attention);
        openHover(attention, index);
      });
      el.addEventListener('pointerleave', () => {
        if (!attention || attention.hovering !== index) return;
        attention.hovering = null;
        closeHover(attention);
      });

      dom.cardGrid.appendChild(el);
      if (attention && attention.observer) attention.observer.observe(el);
    });
  }

  // ─── Toggle Selection ────────────────────────────────────
  function toggleCard(card, el, renderedRound = state.roundNumber, input = null) {
    if (state.roundTransitioning || state.sessionSealed || !state.roundOpen || renderedRound !== state.roundNumber) return;
    if (state.eventJournal.events.length >= MAX_SESSION_EVENTS - 2) return;
    if (!state.currentRoundCards.some(current => current.id === card.id)) return;
    const attention = state.roundAttention;
    // Choices are timed from readiness; a round still loading is not choosable.
    if (!attention || attention.readyAt === null) return;
    const tMs = Math.min(MAX_ROUND_MS - attention.readyOffsetMs,
      Math.max(attention.lastChoiceMs, Math.round(performance.now() - attention.readyAt)));
    attention.lastChoiceMs = tMs;
    // Clicking a rendered image is direct evidence that it was on screen.
    if (attention.shown.has(card.id) && !pageHidden()) markCardSeen(attention, card.id);
    const idx = state.selectedCards.findIndex(s => s.id === card.id);
    const layout = measureLayout(attention);
    if (idx === -1) {
      appendEvent('card_selected', { round: state.roundNumber, card_id: card.id, t_ms: tMs, input, layout });
      state.selectedCards.push({ id: card.id, round: state.roundNumber });
      el.classList.add('selected');
      playRippleSound(true);
    } else {
      appendEvent('card_deselected', { round: state.roundNumber, card_id: card.id, t_ms: tMs, input, layout });
      state.selectedCards.splice(idx, 1);
      el.classList.remove('selected');
      playRippleSound(false);
    }
    el.setAttribute('aria-pressed', String(el.classList.contains('selected')));
    dom.totalSelected.textContent = state.selectedCards.length;
    syncRoundControls();
    schedulePartialVisit();
  }

  // ─── Round attention evidence ────────────────────────────
  // Readiness, on-screen visibility and tab idleness are recorded as facts in
  // the journal; Python decides how (and whether) they enter any model.
  function createRoundAttention(round, cards) {
    const attention = {
      round, cardIds: cards.map(card => card.id), startedAt: null, readyAt: null, readyOffsetMs: null,
      readyTimer: null, settled: new Set(), shown: new Set(), visible: new Set(), seen: [],
      lastChoiceMs: 0, hiddenMs: 0, hiddenSince: null, closed: false, observer: null,
      // Hover timeline [card index, start ms, end ms] from readiness; null where nothing can hover.
      hoverable: canHover(), hover: [], hoverOpen: null, hovering: null,
    };
    if (typeof IntersectionObserver === 'function') {
      attention.observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          const id = entry.target.dataset.id;
          if (entry.isIntersecting && entry.intersectionRatio >= SEEN_RATIO - 0.001) {
            attention.visible.add(id);
            if (attention.shown.has(id) && !pageHidden()) markCardSeen(attention, id);
          } else {
            attention.visible.delete(id);
          }
        });
      }, { threshold: [0, SEEN_RATIO] });
    }
    return attention;
  }

  function pageHidden() {
    return document.visibilityState === 'hidden';
  }

  function canHover() {
    return typeof window.matchMedia === 'function' && window.matchMedia('(any-hover: hover)').matches;
  }

  function openHover(attention, index) {
    if (!attention.hoverable || attention.closed || attention.readyAt === null || attention.hoverOpen
        || pageHidden()) return;
    attention.hoverOpen = { index, start: Math.max(0, Math.round(performance.now() - attention.readyAt)) };
  }

  function closeHover(attention, limit = MAX_ROUND_MS) {
    const open = attention.hoverOpen;
    if (!open) return;
    attention.hoverOpen = null;
    const previous = attention.hover[attention.hover.length - 1];
    // Rounding must not make segments overlap or outlast the round window.
    const start = Math.max(open.start, previous ? previous[2] : 0);
    const now = performance.now();
    const windowMs = Math.max(attention.lastChoiceMs,
      Math.round(now - attention.startedAt) - attention.readyOffsetMs);
    const end = Math.min(limit, windowMs, Math.round(now - attention.readyAt));
    if (end > start && attention.hover.length < HOVER_SEGMENT_LIMIT) {
      attention.hover.push(Object.freeze([open.index, start, end]));
      schedulePartialVisit();
    }
  }

  // Screen slot [row, column] of each displayed card, in card order, from layout
  // offsets (not transformed boxes, which move while cards animate in). Null when
  // the grid cannot be measured or two cards would share a slot.
  function measureLayout(attention) {
    const elements = Array.from(dom.cardGrid.querySelectorAll('.card'));
    const boxes = attention.cardIds.map(id => elements.find(el => el.dataset.id === id));
    if (boxes.some(el => !el || typeof el.offsetTop !== 'number' || !el.offsetWidth)) return null;
    const tops = [...new Set(boxes.map(el => el.offsetTop))].sort((a, b) => a - b);
    const slots = boxes.map(el => [tops.indexOf(el.offsetTop),
      boxes.filter(other => other.offsetTop === el.offsetTop && other.offsetLeft < el.offsetLeft).length]);
    if (new Set(slots.map(slot => slot.join(':'))).size !== slots.length) return null;
    return Object.freeze(slots.map(slot => Object.freeze(slot)));
  }

  function markCardSeen(attention, id) {
    if (attention.closed || attention.seen.includes(id)) return;
    attention.seen.push(id);
    schedulePartialVisit();
  }

  function settleCardImage(attention, id, loaded) {
    if (!attention || attention.closed) return;
    if (loaded) {
      attention.shown.add(id);
      if (attention.visible.has(id) && !pageHidden()) markCardSeen(attention, id);
    } else {
      attention.shown.delete(id);
    }
    attention.settled.add(id);
    maybeMarkRoundReady(attention);
  }

  function maybeMarkRoundReady(attention) {
    if (attention.cardIds.every(id => attention.settled.has(id))) markRoundReady(attention);
  }

  function markRoundReady(attention) {
    if (attention.closed || attention.readyAt !== null || attention.startedAt === null
        || attention !== state.roundAttention || !state.roundOpen || state.sessionSealed) return;
    clearTimeout(attention.readyTimer);
    attention.readyTimer = null;
    const now = performance.now();
    attention.readyAt = now;
    attention.readyOffsetMs = Math.min(MAX_ROUND_MS, Math.max(0, Math.round(now - attention.startedAt)));
    // Idle time is measured inside the deliberation window only.
    attention.hiddenMs = 0;
    attention.hiddenSince = pageHidden() ? now : null;
    appendEvent('round_ready', {
      round: attention.round,
      ready_offset_ms: attention.readyOffsetMs,
      unloaded_card_ids: Object.freeze(attention.cardIds.filter(id => !attention.shown.has(id))),
      layout: measureLayout(attention),
    });
    // A pointer already resting on a card starts its first segment at readiness.
    if (attention.hovering !== null) openHover(attention, attention.hovering);
    syncRoundControls();
    schedulePartialVisit();
  }

  function endRoundAttention(attention) {
    if (!attention) return;
    attention.closed = true;
    clearTimeout(attention.readyTimer);
    attention.readyTimer = null;
    if (attention.observer) attention.observer.disconnect();
  }

  function trackPageVisibility() {
    if (state.visibilityTracking) return;
    state.visibilityTracking = true;
    if (typeof window.addEventListener === 'function') {
      window.addEventListener('pagehide', () => {
        clearPartialVisitTimers();
        savePartialVisit({ keepalive: true });
      });
      window.addEventListener('pageshow', () => schedulePartialPulse());
    }
    document.addEventListener('visibilitychange', () => {
      trackFinaleVisibility();
      const attention = state.roundAttention;
      if (!attention || attention.closed || attention.startedAt === null) return;
      const now = performance.now();
      if (pageHidden()) {
        if (attention.hiddenSince === null) attention.hiddenSince = now;
        closeHover(attention);
        clearPartialVisitTimers();
        savePartialVisit({ keepalive: true });
        return;
      }
      if (attention.hiddenSince !== null) {
        attention.hiddenMs += now - attention.hiddenSince;
        attention.hiddenSince = null;
      }
      // A pointer can remain on a card while another tab is active, without a
      // new pointerenter on return. Confirm its current hover state before
      // resuming: the remembered index alone may be stale after a hidden move.
      if (attention.hovering !== null) {
        const card = Array.from(dom.cardGrid.querySelectorAll('.card'))
          .find(el => el.dataset.id === attention.cardIds[attention.hovering]);
        if (card && typeof card.matches === 'function' && card.matches(':hover')) {
          openHover(attention, attention.hovering);
        } else {
          attention.hovering = null;
        }
      }
      attention.visible.forEach(id => { if (attention.shown.has(id)) markCardSeen(attention, id); });
      savePartialVisit();
      schedulePartialPulse();
    });
  }

  // ─── Next Round ──────────────────────────────────────────
  function nextRound() {
    if (LAST_CARD && state.roundNumber >= LAST_CARD_MAX_ROUNDS) { revealDestiny(); return; }
    drawRound();
  }

  // Current-round checkpoints remain provisional: they neither close a round nor
  // introduce a STOP observation. Selection and attention updates run off the UI
  // path, with one request and one replaceable latest snapshot.
  function visitSessionKey() {
    if (!state.sessionKey) {
      state.sessionKey = Array.from(crypto.getRandomValues(new Uint8Array(16)),
                                    byte => byte.toString(16).padStart(2, '0')).join('');
    }
    return state.sessionKey;
  }

  function canSavePartialVisit() {
    const terminalCheckpoint = LAST_CARD && state.finaleBasePayload && !state.analysisBusy && !state.analysisResult;
    return Boolean(DECK && state.consent && COLLECT && (!state.sessionSealed || terminalCheckpoint) && !probeActive()
      && hasBackendConfig() && state.eventJournal.events.length);
  }

  function clearPartialVisitTimers() {
    clearTimeout(state.partialSaveTimer);
    clearTimeout(state.partialPulseTimer);
    state.partialSaveTimer = state.partialPulseTimer = null;
  }

  function cancelPartialVisit() {
    clearPartialVisitTimers();
    state.partialPending = null;
    if (state.partialInFlight && state.partialInFlight.controller) state.partialInFlight.controller.abort();
    state.partialInFlight = null;
  }

  function schedulePartialVisit() {
    if (!canSavePartialVisit()) return;
    clearTimeout(state.partialSaveTimer);
    state.partialSaveTimer = setTimeout(() => {
      state.partialSaveTimer = null;
      savePartialVisit();
    }, PARTIAL_SAVE_DELAY_MS);
  }

  function schedulePartialPulse() {
    clearTimeout(state.partialPulseTimer);
    state.partialPulseTimer = null;
    if (!canSavePartialVisit() || pageHidden()) return;
    state.partialPulseTimer = setTimeout(() => {
      state.partialPulseTimer = null;
      if (!canSavePartialVisit() || pageHidden()) return;
      savePartialVisit();
      schedulePartialPulse();
    }, PARTIAL_PULSE_MS);
  }

  function openRoundCheckpoint() {
    const attention = state.roundAttention;
    if (!state.roundOpen || !attention || attention.closed || attention.startedAt === null) return null;
    const now = performance.now();
    const offset = attention.readyOffsetMs === null ? 0 : attention.readyOffsetMs;
    const elapsed = Math.min(MAX_ROUND_MS, Math.max(offset + attention.lastChoiceMs,
      Math.round(now - attention.startedAt)));
    const windowMs = Math.max(0, elapsed - offset);
    const hidden = attention.readyAt === null ? 0 : Math.min(windowMs, Math.max(0,
      Math.round(attention.hiddenMs + (attention.hiddenSince === null ? 0 : now - attention.hiddenSince))));
    let hover = null;
    if (attention.hoverable && attention.readyAt !== null) {
      hover = attention.hover.map(segment => [segment[0], segment[1], Math.min(segment[2], windowMs)])
        .filter(segment => segment[2] > segment[1]);
      const open = attention.hoverOpen;
      if (open && hover.length < HOVER_SEGMENT_LIMIT) {
        const previous = hover[hover.length - 1];
        const start = Math.max(open.start, previous ? previous[2] : 0);
        const end = Math.min(windowMs, Math.round(now - attention.readyAt));
        if (end > start) hover.push([open.index, start, end]);
      }
    }
    return { round: attention.round, elapsed_ms: elapsed, hidden_ms: hidden,
             ready_offset_ms: attention.readyOffsetMs,
             seen_card_ids: attention.observer ? attention.seen.slice() : null, hover };
  }

  function savePartialVisit({ keepalive = false } = {}) {
    if (!canSavePartialVisit()) return;
    clearTimeout(state.partialSaveTimer);
    state.partialSaveTimer = null;
    const visitor = visitorKey(true), epoch = state.collectionEpoch;
    const openRound = openRoundCheckpoint();
    const body = JSON.stringify({ consent: CONSENT_VERSION, visitor, session_key: visitSessionKey(),
                                  journal: state.eventJournal,
                                  ...(LAST_CARD ? { product: LAST_CARD_PRODUCT } : {}),
                                  ...(state.experience ? { experience: snapshotExperience() } : {}),
                                  ...(openRound ? { open_round: openRound } : {}) });
    state.partialPending = { body, visitor, epoch,
      keepalive: keepalive && new Blob([body]).size <= PARTIAL_KEEPALIVE_BYTES };
    if (keepalive && state.partialInFlight) {
      // Exit takes priority over an older browser request. Aborting the browser
      // does not guarantee server cancellation; archive prefix guards still apply.
      if (state.partialInFlight.controller) state.partialInFlight.controller.abort();
      state.partialInFlight = null;
    }
    flushPartialVisit();
  }

  function flushPartialVisit() {
    if (state.partialInFlight || !state.partialPending) return;
    const checkpoint = state.partialPending;
    state.partialPending = null;
    const request = { controller: null };
    state.partialInFlight = request;
    Promise.resolve().then(() => ensureBackendConfig()).then(() => {
      const sealedLastCard = LAST_CARD && state.sessionSealed && state.consent && COLLECT && state.finaleBasePayload;
      if (state.partialInFlight !== request || (!canSavePartialVisit() && !sealedLastCard)
          || state.collectionEpoch !== checkpoint.epoch || visitorKey() !== checkpoint.visitor) return;
      request.controller = typeof AbortController === 'function' ? new AbortController() : null;
      return fetch(`${API_ENDPOINT}/record/partial`, { method: 'POST', headers: buildApiHeaders(),
        body: checkpoint.body, ...(request.controller ? { signal: request.controller.signal } : {}),
        ...(checkpoint.keepalive ? { keepalive: true } : {}) });
    }).catch(() => {}).finally(() => {
      if (state.partialInFlight !== request) return;
      state.partialInFlight = null;
      if (!canSavePartialVisit()) state.partialPending = null;
      else flushPartialVisit();
    });
  }

  // ─── Reveal Destiny ──────────────────────────────────────
  async function revealDestiny() {
    if (state.analysisBusy || state.roundTransitioning || !state.roundOpen || state.sessionSealed || dom.revealOverlay.classList.contains('active')) return;
    if (LAST_CARD) savePartialVisit({ keepalive: true });
    closeCurrentRound();
    appendEvent('session_completed');
    Object.freeze(state.eventJournal.events);
    Object.freeze(state.eventJournal);
    if (probeActive()) {
      endProbe();
    } else if (probeReady()) {
      // The main journal is sealed, so the reading's model is fixed before any new image is seen.
      startProbe();
      return;
    }
    state.sessionSealed = true;
    if (state.xrayOpen) scheduleXrayRefresh(0, true);
    syncRoundControls();

    // Build API payload
    const payload = Object.freeze({
      journal: state.eventJournal, request_id: createOperationId('reading'),
      language: currentLanguage(),
      ...(state.consent && state.experience ? { experience: snapshotExperience() } : {}),
      ...(state.consent ? { consent: CONSENT_VERSION, visitor: visitorKey(true) } : {}),
      ...(state.consent && DECK ? { session_key: visitSessionKey() } : {}),
      ...(state.consent && state.probe && state.probe.journal ? { probe: state.probe.journal } : {}),
      ...(LAST_CARD ? { product: LAST_CARD_PRODUCT } : {}),
    });

    if (LAST_CARD) {
      state.finaleBasePayload = payload;
      // The terminal vote is separate, but leaving while it loads must retain
      // the completed final exploration round, including its attention window.
      savePartialVisit({ keepalive: true });
      await beginFinale();
      return;
    }
    await showReadingForPayload(payload);
  }

  async function showReadingForPayload(payload) {

    // Update summary
    dom.summaryRounds.textContent = state.roundNumber;
    dom.summaryCards.textContent = state.selectedCards.length;

    // Show reveal overlay + particles
    dom.revealOverlay.classList.add('active');
    stopParticles();
    state.particleController = initParticles();
    resetReading();
    if (LAST_CARD && dom.revealHeading) dom.revealHeading.focus();
    resetDialoguePanel();
    state.pendingAnalysisPayload = payload;

    const configured = await ensureBackendConfig();
    if (!configured) {
      renderMissingBackendReading(payload);
      return;
    }

    // Call API (if endpoint configured)
    await callAnalyzeAPI(payload);
  }

  function renderFinaleText() {
    if (!LAST_CARD || !dom.finaleOverlay || dom.finaleOverlay.hidden) return;
    const result = state.finaleResult;
    dom.finaleTitle.textContent = tr(result ? result.hit ? '猜中了！' : '你给了织机一个意外。' : '命运底牌');
    dom.finaleDescription.textContent = result
      ? tr(result.hit ? '你最心动的，正是它提前押下的那张。' : '这一回它没猜中。属于你的故事，仍由你选中的画面开始。')
      : state.finaleChallenge
        ? tr('四张从未出现的画面，织机已经押好一张。哪张最让你心动？')
        : tr('你的探索已经封存。织机正在为最后一选押下底牌……');
    dom.finaleFlip.textContent = tr(state.finaleBusy ? '正在翻牌……' : '选好了，翻开底牌');
    dom.finaleContinue.textContent = tr('揭晓我的命运角色 →');
    dom.finaleRetry.textContent = tr('重试');
    if (result && !state.finaleError) {
      const jev = result.jev;
      dom.finaleStatus.textContent = jev && jev.status === 'ok'
        ? tr(jev.predicted_card_id === result.chosen_card_id ? 'Jev 也猜中了你的最爱。' : 'Jev 这一回没猜中。')
        : state.jevEnabled ? tr('Jev 本次结果不可用；保留织机的真实竞猜结果。') : tr('这一局，只由织机与你对弈。');
    }
  }

  async function beginFinale() {
    if (state.finaleBusy || state.finaleResult) return;
    state.finaleBusy = true;
    state.finaleError = null;
    dom.finaleOverlay.hidden = false;
    dom.testArea.inert = true;
    if (state.finaleStartedAt === null) state.finaleStartedAt = performance.now();
    dom.finaleOverlay.setAttribute('aria-busy', 'true');
    dom.finaleGrid.innerHTML = '';
    dom.finaleSeal.hidden = false;
    dom.finaleRetry.hidden = true;
    dom.finaleFlip.hidden = false;
    dom.finaleFlip.disabled = true;
    dom.finaleContinue.hidden = true;
    dom.finaleStatus.textContent = tr('只用此前的选择，不偷看你的最后一选。');
    renderFinaleText();
    dom.finaleTitle.focus();
    try {
      if (!(await ensureBackendConfig())) throw new Error(tr('请确认本地服务正在运行，再点击重试。你的选择已保留。'));
      if (!state.finaleRequest) {
        const ticket = state.jevEnabled && state.jevStart ? await state.jevStart : null;
        state.finaleRequest = Object.freeze({ journal: state.eventJournal,
          challenge_id: createOperationId('finale'), product: LAST_CARD_PRODUCT, language: currentLanguage(),
          ...(ticket ? { jev_session: ticket } : {}),
          ...(state.consent && state.experience ? { experience: snapshotExperience() } : {}),
          ...(state.consent ? { consent: CONSENT_VERSION, visitor: visitorKey(true), session_key: visitSessionKey() } : {}),
        });
      }
      const challenge = await xrayRequest('/finale/start', state.finaleRequest);
      if (!challenge || challenge.schema_version !== 'norn.finale.challenge.v1'
          || challenge.challenge_id !== state.finaleRequest.challenge_id
          || !Array.isArray(challenge.candidate_ids) || challenge.candidate_ids.length !== 4
          || new Set(challenge.candidate_ids).size !== 4
          || challenge.candidate_ids.some(id => state.usedCardIds.has(id) || !state.allCards.some(card => card.id === id))
          || typeof challenge.commitment !== 'string' || !challenge.commitment) throw new Error(tr('终局凭证不完整，请重试。'));
      state.finaleChallenge = Object.freeze(challenge);
      state.finaleJevSettled = challenge.jev_status !== 'pending';
      dom.finaleSeal.hidden = true;
      dom.finaleProof.hidden = false;
      dom.finaleProofText.textContent = `${challenge.commitment}\n${challenge.events_hash || ''}`;
      renderFinaleCards();
      if (!state.finaleJevSettled && !state.finaleJevAttempted) await prepareFinaleJev();
    } catch (error) {
      state.finaleError = 'start';
      dom.finaleStatus.textContent = tr('底牌暂时没准备好。你的探索已保留，请重试。');
      dom.finaleRetry.hidden = false;
    } finally {
      state.finaleBusy = false;
      dom.finaleOverlay.setAttribute('aria-busy', 'false');
      renderFinaleText();
      syncFinaleCardReadiness();
    }
  }

  function renderFinaleCards() {
    const challenge = state.finaleChallenge;
    if (!challenge) return;
    const epoch = ++state.finaleRenderEpoch;
    state.finaleImagesReady = false;
    const buttons = [];
    let settled = 0, loaded = 0;
    dom.finaleGrid.innerHTML = '';
    dom.finaleStatus.textContent = tr('正在展开最后四张画面……');
    challenge.candidate_ids.forEach((id, index) => {
      const card = state.allCards.find(item => item.id === id);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'finale-card';
      button.disabled = true;
      button.dataset.cardId = id;
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', 'false');
      button.setAttribute('aria-label', tr`终局画面 ${index + 1}`);
      const image = document.createElement('img');
      image.alt = '';
      let done = false;
      const settle = ok => {
        if (done || epoch !== state.finaleRenderEpoch) return;
        done = true; settled++; if (ok) loaded++;
        if (settled !== 4) return;
        state.finaleImagesReady = loaded === 4;
        if (state.finaleImagesReady) {
          state.finaleError = null;
          dom.finaleRetry.hidden = true;
          syncFinaleCardReadiness();
        } else {
          state.finaleError = 'images';
          dom.finaleStatus.textContent = tr('有画面未能加载，请重试后再选。');
          dom.finaleRetry.hidden = false;
        }
      };
      image.onload = () => settle(true);
      image.onerror = () => settle(false);
      image.src = card.url;
      button.appendChild(image);
      xrayText(button, String(index + 1).padStart(2, '0'), 'span', 'finale-card-number');
      button.addEventListener('pointerdown', event => {
        button.dataset.choiceInput = ['mouse', 'touch'].includes(event.pointerType) ? event.pointerType : 'unknown';
      });
      button.addEventListener('click', event => selectFinaleCard(id,
        event && event.detail === 0 ? 'keyboard' : button.dataset.choiceInput || 'unknown'));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) || !state.finaleImagesReady || state.finaleChoiceRequest) return;
        event.preventDefault();
        const step = ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1;
        const next = (index + step + 4) % 4;
        selectFinaleCard(challenge.candidate_ids[next], 'keyboard');
        buttons[next].focus();
      });
      buttons.push(button);
      dom.finaleGrid.appendChild(button);
      if (image.complete) settle(image.naturalWidth > 0);
    });
    syncFinaleCardReadiness();
  }

  function syncFinaleCardReadiness() {
    if (!state.finaleChallenge || state.finaleResult || state.finaleError) return;
    const ready = state.finaleImagesReady && state.finaleJevSettled && !state.finaleBusy && !state.finaleChoiceRequest;
    if (ready && state.finaleReadyAt === null) {
      state.finaleReadyAt = performance.now();
      if (pageHidden()) state.finaleHiddenSince = state.finaleReadyAt;
    }
    Array.from(dom.finaleGrid.children).forEach(button => { button.disabled = !ready; });
    if (state.finaleImagesReady) dom.finaleStatus.textContent = tr(state.finaleJevSettled
      ? '选一张最心动的，再翻开底牌。' : 'Jev 正在封存自己的押注，完成后即可选图……');
  }

  async function prepareFinaleJev() {
    state.finaleJevAttempted = true;
    dom.finaleStatus.textContent = tr('Jev 正在封存自己的押注，完成后即可选图……');
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const deadline = setTimeout(() => { if (controller) controller.abort(); }, 17000);
    try {
      const response = await fetch(`${API_ENDPOINT}/jev/finale`, { method: 'POST', headers: buildApiHeaders(),
        body: JSON.stringify({ challenge_id: state.finaleChallenge.challenge_id }),
        ...(controller ? { signal: controller.signal } : {}) });
      const result = await response.json();
      if (!response.ok || result.status !== 'ready') state.jevMessage = 'Jev 本次结果不可用；保留织机的真实竞猜结果。';
    } catch (_) { state.jevMessage = 'Jev 本次结果不可用；保留织机的真实竞猜结果。'; }
    finally { clearTimeout(deadline); state.finaleJevSettled = true; }
  }

  function trackFinaleVisibility() {
    if (state.finaleReadyAt === null || state.finaleChoiceRequest || state.finaleResult) return;
    const now = performance.now();
    if (pageHidden()) {
      if (state.finaleHiddenSince === null) state.finaleHiddenSince = now;
    } else if (state.finaleHiddenSince !== null) {
      state.finaleHiddenMs += Math.max(0, now - state.finaleHiddenSince);
      state.finaleHiddenSince = null;
    }
  }

  function measureFinaleLayout() {
    const boxes = Array.from(dom.finaleGrid.children);
    if (boxes.length !== 4 || boxes.some(el => typeof el.offsetTop !== 'number' || !el.offsetWidth)) return null;
    const tops = [...new Set(boxes.map(el => el.offsetTop))].sort((a, b) => a - b);
    const slots = boxes.map(el => [tops.indexOf(el.offsetTop),
      boxes.filter(other => other.offsetTop === el.offsetTop && other.offsetLeft < el.offsetLeft).length]);
    if (new Set(slots.map(slot => slot.join(':'))).size !== 4) return null;
    return Object.freeze(slots.map(slot => Object.freeze(slot)));
  }

  function selectFinaleCard(id, input = 'unknown') {
    if (!state.finaleImagesReady || !state.finaleJevSettled || state.finaleChoiceRequest || state.finaleBusy || state.finaleResult) return;
    if (!state.finaleChallenge.candidate_ids.includes(id)) return;
    state.finaleSelection = id;
    state.finaleInput = ['mouse', 'touch', 'keyboard'].includes(input) ? input : 'unknown';
    state.finaleLayout = measureFinaleLayout();
    dom.finaleGrid.children && Array.from(dom.finaleGrid.children).forEach(button => {
      const chosen = button.dataset.cardId === id;
      button.setAttribute('aria-checked', String(chosen));
      xrayClass(button, 'selected', chosen);
    });
    dom.finaleFlip.disabled = false;
  }

  async function flipFinale() {
    if (state.finaleBusy || state.finaleResult || !state.finaleSelection || !state.finaleImagesReady) return;
    state.finaleBusy = true;
    state.finaleError = null;
    dom.finaleRetry.hidden = true;
    dom.finaleFlip.disabled = true;
    if (!state.finaleChoiceRequest) {
      const now = performance.now();
      const elapsed = Math.min(MAX_ROUND_MS, Math.max(0, Math.round(now - state.finaleStartedAt)));
      const readyOffset = Math.min(elapsed, Math.max(0, Math.round(state.finaleReadyAt - state.finaleStartedAt)));
      const hidden = state.finaleHiddenMs + (state.finaleHiddenSince === null ? 0 : Math.max(0, now - state.finaleHiddenSince));
      state.finaleChoiceRequest = Object.freeze({
        challenge_id: state.finaleChallenge.challenge_id, card_id: state.finaleSelection, elapsed_ms: elapsed,
        observation: Object.freeze({ ready_offset_ms: readyOffset,
          hidden_ms: Math.min(elapsed - readyOffset, Math.max(0, Math.round(hidden))),
          layout: state.finaleLayout, input: state.finaleInput }),
      });
    }
    Array.from(dom.finaleGrid.children).forEach(button => { button.disabled = true; });
    renderFinaleText();
    try {
      const result = await xrayRequest('/finale/choose', state.finaleChoiceRequest);
      if (!result || result.schema_version !== 'norn.finale.result.v1'
          || result.challenge_id !== state.finaleChallenge.challenge_id
          || result.commitment !== state.finaleChallenge.commitment
          || result.chosen_card_id !== state.finaleChoiceRequest.card_id
          || !state.finaleChallenge.candidate_ids.includes(result.predicted_card_id)
          || typeof result.hit !== 'boolean' || result.hit !== (result.predicted_card_id === result.chosen_card_id)) throw new Error('Invalid finale result');
      state.finaleResult = Object.freeze(result);
      renderFinaleXray(result);
      Array.from(dom.finaleGrid.children).forEach(button => {
        const chosen = button.dataset.cardId === result.chosen_card_id;
        const predicted = button.dataset.cardId === result.predicted_card_id;
        xrayClass(button, 'predicted', predicted);
        if (chosen) xrayText(button, tr('你的最爱'), 'span', 'finale-card-badge chosen');
        if (predicted) xrayText(button, tr('织机的押注'), 'span', 'finale-card-badge predicted');
        if (result.jev && result.jev.status === 'ok' && result.jev.predicted_card_id === button.dataset.cardId)
          xrayText(button, tr('Jev 的押注'), 'span', 'finale-card-badge jev');
      });
      dom.finaleProofText.textContent += `\n${result.salt || ''}`;
      dom.finaleFlip.hidden = true;
      dom.finaleContinue.hidden = false;
      dom.finaleTitle.focus();
    } catch (_) {
      state.finaleError = 'choose';
      dom.finaleStatus.textContent = tr('翻牌结果还没确认。重试只会核对同一个选择，不会重新下注。');
      dom.finaleRetry.hidden = false;
    } finally {
      state.finaleBusy = false;
      renderFinaleText();
    }
  }

  function retryFinale() {
    if (state.finaleBusy) return;
    if (state.finaleError === 'images') { renderFinaleCards(); return; }
    if (state.finaleChoiceRequest) { flipFinale(); return; }
    beginFinale();
  }

  async function continueFinaleReading() {
    if (!state.finaleResult || state.analysisBusy || dom.revealOverlay.classList.contains('active')) return;
    dom.finaleOverlay.hidden = true;
    const payload = Object.freeze({ ...state.finaleBasePayload, language: currentLanguage(),
      finale_id: state.finaleResult.challenge_id });
    await showReadingForPayload(payload);
  }

  // ─── API Call ────────────────────────────────────────────
  async function callAnalyzeAPI(payload) {
    if (!hasBackendConfig()) {
      console.warn('[Norn] Backend config missing; skipping analysis request.');
      return;
    }
    if (state.analysisBusy) return;
    state.analysisBusy = true;
    dom.analysisError.hidden = true;
    dom.retryAnalysis.disabled = true;
    try {
      const resp = await fetch(`${API_ENDPOINT}/analyze`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: JSON.stringify(payload),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const error = new Error(data.fallback || data.error || `HTTP ${resp.status}`);
        error.serverResponse = true;
        error.confirmedTerminal = resp.status !== 429 && typeof data.status === 'string';
        throw error;
      }
      if (!data.reading) throw new Error(tr('解读内容为空，请重试。'));
      state.analysisResult = data;
      dom.languageHint.hidden = true;
      state.pendingAnalysisPayload = null;
      state.record = data.record && data.record.record_id ? data.record : null;
      // An unconfirmed write may or may not be stored: keep withdrawal, count no thread.
      if (state.record && !state.record.unconfirmed) setThreadCount(threadCount() + 1);
      updateRecordNote();
      if (state.xraySource === 'live') scheduleXrayRefresh();
      updateRecordExport(data);
      renderProductResult(data);
      updateRuntimeNotice(data);
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
      if (err.confirmedTerminal) {
        // A terminal response confirms the server finished this operation. An
        // explicit retry may dispatch anew; a lost response must retain its ID.
        state.pendingAnalysisPayload = Object.freeze({ ...payload, request_id: createOperationId('reading') });
      }
      setRevealTitle(tr('织机暂时未能回应'), 'Your choices are still here');
      dom.analysisError.hidden = false;
      document.getElementById('analysis-error-text').textContent = err.serverResponse
        ? tr`${err.message} 你的选择已保留，可以重试。`
        : tr('请确认本地服务正在运行，再点击重试。你的选择已保留。');
      releaseParticles(0);
      showRestartBlock();
    } finally {
      state.analysisBusy = false;
      dom.retryAnalysis.disabled = false;
    }
  }

  async function retryAnalysis() {
    if (!state.pendingAnalysisPayload || state.analysisBusy) return;
    if (!(await ensureBackendConfig())) {
      renderMissingBackendReading(state.pendingAnalysisPayload);
      return;
    }
    resetReading();
    resetDialoguePanel();
    await callAnalyzeAPI(state.pendingAnalysisPayload);
  }

  function renderMissingBackendReading(payload) {
    state.analysisResult = {
      mbti_type: 'XXXX',
      confidence: {},
      reading: missingBackendReading(),
      mode: 'unconfigured',
      total_rounds: state.roundNumber,
      total_selections: state.selectedCards.length,
      config_missing: true,
      max_dialogue_turns: getDialogueLimit(state.roundNumber),
    };
    state.dialogueLimit = getDialogueLimit(state.roundNumber);
    updateDialogueMeta();
    setDialogueStatus(tr('后端入口尚未配置。'));
    dom.analysisError.hidden = false;
    document.getElementById('analysis-error-text').textContent = tr('请使用本地服务打开页面，或检查 runtime-config.js 中的入口。');
    renderReading(missingBackendReading());
  }

  function shortProfileHash(value) {
    return normalizeConfigValue(value).replace(/^sha256:/, '').slice(0, 12);
  }

  function createOperationId(kind) {
    const id = window.crypto && typeof window.crypto.randomUUID === 'function'
      ? window.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
    return `${kind}-${id}`;
  }

  function updateRecordExport(result) {
    dom.recordExport.hidden = !result.session_id;
    dom.exportRecord.disabled = !result.session_id;
    const version = normalizeConfigValue(result.profile_version);
    const hash = shortProfileHash(result.profile_hash);
    dom.profileVersion.textContent = [version && tr`画像 ${version}`, hash].filter(Boolean).join(' · ');
    dom.exportStatus.textContent = '';
  }

  function updateRecordNote(text = null) {
    if (!dom.recordNote) return;
    const stored = Boolean(state.record);
    dom.recordNote.hidden = !COLLECT;
    const threads = threadCount();
    dom.recordNoteText.textContent = text || (stored
      ? (state.record.unconfirmed
        ? tr('这次的记录没能确认是否保存；如果不想留下，可以撤回。')
        : threads > 1 ? tr`✦ 你的第 ${threads} 根线已匿名织入织锦` : tr('✦ 你的线已匿名织入织锦'))
      : state.consent ? tr('这次没能保存记录，体验不受影响。') : tr('这次没有留下学习记录。'));
    dom.withdrawRecord.hidden = !stored;
    dom.withdrawRecord.disabled = state.withdrawBusy;
  }

  async function withdrawRecord() {
    if (!state.record || state.withdrawBusy || !hasBackendConfig()) return;
    state.withdrawBusy = true;
    updateRecordNote(tr('正在撤回……'));
    try {
      const resp = await fetch(`${API_ENDPOINT}/record/withdraw`, {
        method: 'POST', headers: buildApiHeaders(),
        body: JSON.stringify({ visitor: visitorKey(), record_id: state.record.record_id }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.withdrawn !== true) throw new Error(data.error || `HTTP ${resp.status}`);
      const counted = !state.record.unconfirmed;
      state.record = null;
      state.withdrawBusy = false;
      if (counted) setThreadCount(threadCount() - 1);
      updateRecordNote(tr('已撤回，这次的记录已经删除。'));
    } catch (error) {
      state.withdrawBusy = false;
      updateRecordNote(tr`撤回没有完成：${error.message || tr('服务暂时不可用')}，可以再试一次。`);
      dom.withdrawRecord.hidden = false;
    }
  }

  async function exportSessionRecord() {
    const result = state.analysisResult;
    if (state.exportBusy || !result || !result.session_id || !hasBackendConfig()) return;
    state.exportBusy = true;
    dom.exportRecord.disabled = true;
    dom.exportStatus.textContent = tr('正在准备本次记录……');
    try {
      const resp = await fetch(`${API_ENDPOINT}/sessions/${encodeURIComponent(result.session_id)}/export`, {
        headers: buildApiHeaders(),
      });
      const recordText = await resp.text();
      if (!resp.ok) {
        if (resp.status === 404 || resp.status === 410) {
          throw new Error(tr('本次会话已过期或服务已重启，请重新编织后再导出。'));
        }
        throw new Error(tr('本次记录暂时无法导出，请稍后重试。'));
      }
      if (!recordText.trim()) throw new Error(tr('本次记录暂时无法读取，请稍后重试。'));
      const recordDocument = JSON.parse(recordText);
      const hasContributions = Boolean(recordDocument.contribution_ledger || recordDocument.ledger);
      // Preserve Python's numeric representation: reserializing in JavaScript
      // would change 8.0/-0.0 and invalidate the canonical profile hash.
      const blob = new Blob([recordText], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `norn-session-${shortProfileHash(result.profile_hash) || result.session_id.slice(0, 12)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      dom.exportStatus.textContent = hasContributions
        ? tr('已导出事件、版本化画像和贡献账本。记录包含本次选择，请自行保管。')
        : tr('已导出事件和版本化画像。记录包含本次选择，请自行保管。');
    } catch (err) {
      dom.exportStatus.textContent = err && err.message
        ? err.message : tr('暂时无法连接本地服务，请恢复后重试导出。');
    } finally {
      state.exportBusy = false;
      dom.exportRecord.disabled = false;
    }
  }

  // ─── Optional post-reading rating ────────────────────────
  // The first outcome label. It is stored beside, never inside, the sealed
  // journal and travels with the export; skipping sends nothing at all.
  function finalSelectionIds(journal) {
    const active = [];
    journal.events.forEach(event => {
      if (event.type === 'card_selected') active.push(event.card_id);
      if (event.type === 'card_deselected') active.splice(active.indexOf(event.card_id), 1);
    });
    return active;
  }

  function resetFeedbackPanel() {
    state.feedback = { score: null, closest: null, busy: false };
    if (!dom.feedbackPanel) return;
    dom.feedbackPanel.hidden = true;
    dom.feedbackRating.innerHTML = '';
    dom.feedbackClosest.innerHTML = '';
    dom.feedbackClosestBlock.hidden = true;
    dom.feedbackSave.disabled = true;
    dom.feedbackStatus.textContent = '';
  }

  function feedbackChoice(parent, kind, value, label, card = null) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = kind === 'score' ? 'feedback-score' : 'feedback-card';
    button.dataset.value = String(value);
    button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', 'false');
    button.setAttribute('aria-label', label);
    if (card) {
      const img = document.createElement('img');
      img.src = card.url;
      img.alt = '';
      img.draggable = false;
      button.appendChild(img);
    } else {
      button.textContent = String(value);
    }
    button.addEventListener('click', () => chooseFeedback(kind, value));
    parent.appendChild(button);
  }

  function showFeedbackPanel() {
    const result = state.analysisResult;
    if (!dom.feedbackPanel || !result || !result.session_id || result.config_missing
        || !dom.feedbackPanel.hidden || !state.sessionSealed) return;
    resetFeedbackPanel();
    for (let score = 1; score <= 5; score++) {
      feedbackChoice(dom.feedbackRating, 'score', score, tr`${score} 分（满分 5）`);
    }
    // Only the sealed journal's final choices are admissible; one card is no choice.
    const ids = finalSelectionIds(state.eventJournal);
    if (ids.length >= 2) {
      ids.forEach(id => {
        const card = state.allCards.find(item => item.id === id);
        if (card) feedbackChoice(dom.feedbackClosest, 'closest', id, tr`最像我的一张：${id}`, card);
      });
      dom.feedbackClosestBlock.hidden = false;
    }
    dom.feedbackPanel.hidden = false;
  }

  function chooseFeedback(kind, value) {
    if (state.feedback.busy) return;
    state.feedback[kind] = state.feedback[kind] === value ? null : value;
    const parent = kind === 'score' ? dom.feedbackRating : dom.feedbackClosest;
    Array.from(parent.children).forEach(button => button.setAttribute('aria-checked',
      String(button.dataset.value === String(state.feedback[kind]))));
    dom.feedbackSave.disabled = state.feedback.score === null && state.feedback.closest === null;
    dom.feedbackStatus.textContent = '';
  }

  async function submitFeedback() {
    const result = state.analysisResult;
    const feedback = state.feedback;
    if (feedback.busy || !result || !result.session_id || !hasBackendConfig()
        || (feedback.score === null && feedback.closest === null)) return;
    feedback.busy = true;
    dom.feedbackSave.disabled = true;
    dom.feedbackStatus.textContent = tr('正在记下……');
    try {
      const resp = await fetch(`${API_ENDPOINT}/feedback`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: JSON.stringify({ session_id: result.session_id, reading_rating: feedback.score,
          closest_card_id: feedback.closest }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(resp.status === 404 || resp.status === 410
          ? tr('本次会话已过期，评分未能保存。') : (data.error || tr('评分暂时无法保存。')));
      }
      dom.feedbackStatus.textContent = state.record && !state.record.unconfirmed
        ? tr('已记下，也织进了你留下的那根线；可以改选后再次记下。')
        : tr('已记下，会随“导出本次记录”一起保存；可以改选后再次记下。');
    } catch (err) {
      dom.feedbackStatus.textContent = tr`${err && err.message ? err.message : tr('暂时无法连接服务。')} 这不影响判词、追问或导出。`;
    } finally {
      feedback.busy = false;
      dom.feedbackSave.disabled = feedback.score === null && feedback.closest === null;
    }
  }

  function skipFeedback() {
    if (state.feedback.busy || !dom.feedbackPanel) return;
    dom.feedbackPanel.hidden = true;
  }

  // ─── Optional engineering inspector ──────────────────────
  // Python computes every round's posterior in one request. The slider and the
  // play button only switch between those precomputed states locally. Opening
  // or scrubbing this panel never seals a journal, creates a session, changes
  // selection state or calls a model.
  const XRAY_SQUASH = 1.5;          // display only: tanh(weight / 1.5) maps a weight to [-1, 1]
  const XRAY_PLAY_MS = 650;
  const XRAY_AXES = [
    { neg: tr('内向 I'), pos: tr('外向 E'), n: 'I', p: 'E' },
    { neg: tr('实感 S'), pos: tr('直觉 N'), n: 'S', p: 'N' },
    { neg: tr('情感 F'), pos: tr('思考 T'), n: 'F', p: 'T' },
    { neg: tr('感知 P'), pos: tr('判断 J'), n: 'P', p: 'J' },
  ];

  function initializeXray() {
    // The HTML contains the classic inspector's empty state. A deck-specific
    // view is revealed only after its matching trajectory has been received.
    if (DECK) dom.xrayContent.hidden = true;
    dom.xrayModelView.open = !DECK;
    dom.xrayModelSummary.hidden = !DECK;
    dom.xrayComparisonImportControl.hidden = !DECK;
    dom.xrayToggle.addEventListener('click', () => setXrayOpen(!state.xrayOpen));
    dom.xrayClose.addEventListener('click', () => setXrayOpen(false));
    dom.xrayRefresh.addEventListener('click', () => scheduleXrayRefresh(0, true));
    dom.xrayImport.addEventListener('change', importXrayRecord);
    dom.xrayComparisonImport.addEventListener('change', importXrayComparison);
    dom.xrayComparisonOnline.addEventListener('click', () => {
      state.xrayOfflineComparison = null;
      const round = state.xrayRound;
      state.xrayRound = -1;
      showXrayRound(round);
      dom.xrayStatus.textContent = tr('已返回逐轮对照 · 仅在本地切换。');
    });
    dom.xrayLive.addEventListener('click', () => {
      state.xraySource = 'live';
      state.xrayImported = null;
      state.xrayRecordText = null;
      state.xrayOfflineComparison = null;
      state.xrayFollow = true;
      scheduleXrayRefresh(0, true);
    });
    dom.xrayRound.addEventListener('input', () => {
      stopXrayPlayback();
      showXrayRound(Number(dom.xrayRound.value));
    });
    dom.xrayPlay.addEventListener('click', toggleXrayPlayback);
    dom.xrayEngineering.addEventListener('toggle', renderXrayDetails);
    document.addEventListener('keydown', (event) => {
      if (LAST_CARD && !dom.finaleOverlay.hidden && event.key === 'Tab') {
        const targets = Array.from(dom.finaleOverlay.querySelectorAll('button, summary, [tabindex="0"]'))
          .filter(element => !element.disabled && element.getClientRects().length > 0);
        if (targets.length) {
          const first = targets[0], last = targets[targets.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === dom.finaleTitle)) {
            event.preventDefault(); last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
          }
        }
        return;
      }
      if (event.key === 'Escape' && state.xrayOpen) {
        event.preventDefault();
        setXrayOpen(false);
      }
    });
  }

  function setXrayOpen(open) {
    state.xrayOpen = open;
    dom.xrayPanel.hidden = !open;
    dom.xrayToggle.setAttribute('aria-expanded', String(open));
    invalidateXrayRequest();
    stopXrayPlayback();
    if (open) {
      dom.xrayTitle.focus();
      scheduleXrayRefresh(0, true);
    } else {
      dom.xrayContent.setAttribute('aria-busy', 'false');
      dom.xrayTip.hidden = true;
      dom.xrayToggle.focus();
      renderPlayFeedback();
    }
  }

  function invalidateXrayRequest() {
    state.xrayEpoch++;
    if (state.xrayTimer) clearTimeout(state.xrayTimer);
    state.xrayTimer = null;
    return state.xrayEpoch;
  }

  function xrayLiveKey() {
    // Clicks inside an open round do not change the trajectory; a closed round,
    // a frozen reading or a follow-up (calls and cache) does.
    let closed = 0;
    mainJournal().events.forEach(event => { if (event.type === 'round_completed') closed++; });
    const session = state.analysisResult && state.analysisResult.session_id || '';
    return `${closed}|${session}|${state.dialogueTurns}`;
  }

  function scheduleXrayRefresh(delay = 180, force = false) {
    if (!state.xrayOpen) return;
    if (researchXrayLocked()) {
      invalidateXrayRequest();
      stopXrayPlayback();
      dom.xrayContent.hidden = true;
      dom.xrayPlay.disabled = true;
      dom.xrayRound.disabled = true;
      dom.xrayStatus.textContent = tr('完成选图后解锁对照，避免预测提示影响你的选择。');
      return;
    }
    dom.xrayPlay.disabled = false;
    dom.xrayRound.disabled = false;
    if (!force) {
      if (state.xraySource !== 'live') return;
      const key = xrayLiveKey();
      if (key === state.xrayRequestedKey) return;
    }
    const epoch = invalidateXrayRequest();
    state.xrayRequestedKey = state.xraySource === 'live' ? xrayLiveKey() : null;
    state.xrayTimer = setTimeout(() => {
      state.xrayTimer = null;
      return refreshXray(epoch);
    }, delay);
  }

  async function xrayRequest(path, body, raw = false) {
    await ensureBackendConfig();
    const options = { headers: buildApiHeaders() };
    if (body !== undefined) {
      options.method = 'POST';
      options.body = raw ? body : JSON.stringify(body);
    }
    const resp = await fetch(`${API_ENDPOINT}${path}`, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const error = new Error(data.error || `HTTP ${resp.status}`);
      error.status = resp.status;
      throw error;
    }
    return data;
  }

  async function refreshXray(epoch = invalidateXrayRequest()) {
    if (!state.xrayOpen) return;
    if (researchXrayLocked()) {
      scheduleXrayRefresh(0, true);
      return;
    }
    if (state.xraySource === 'import' && state.xrayImported) {
      showXrayTrajectory(state.xrayImported);
      return;
    }
    dom.xrayContent.setAttribute('aria-busy', 'true');
    dom.xrayStatus.textContent = tr('正在读取 Python 计算……');
    try {
      if (!hasBackendConfig()) throw new Error(tr('请通过 norn serve 打开本页。'));
      const session = state.analysisResult && state.analysisResult.session_id;
      let data = null;
      if (session) {
        try {
          data = await xrayRequest(`/sessions/${encodeURIComponent(session)}/trajectory`);
        } catch (error) {
          // An expired conversation still has its facts on this page.
          if (error.status !== 404 && error.status !== 410) throw error;
        }
      }
      if (!data) {
        const journal = mainJournal();
        data = await xrayRequest('/trajectory', {
          journal: { schema_version: journal.schema_version, events: journal.events.slice() },
        });
      }
      if (!state.xrayOpen || epoch !== state.xrayEpoch) return;
      showXrayTrajectory(data);
      const rounds = data.states.length - 1;
      dom.xrayStatus.textContent = data.source === 'session'
        ? tr`本次冻结记录 · ${rounds} 轮，全部状态已在本页，拖动不再请求。`
        : data.open_round
          ? tr`实时 · 已封口 ${rounds} 轮；当前一轮完成后加入轨迹。`
          : tr`实时 · 已封口 ${rounds} 轮。`;
    } catch (error) {
      if (epoch !== state.xrayEpoch || !state.xrayOpen) return;
      dom.xrayStatus.textContent = tr`透视暂时不可用：${error.message || tr('请确认本地服务正在运行。')} 选图与追问仍可继续；可点“刷新”重试。`;
    } finally {
      if (epoch === state.xrayEpoch) dom.xrayContent.setAttribute('aria-busy', 'false');
    }
  }

  async function importXrayRecord() {
    if (!state.xrayOpen) return;
    if (researchXrayLocked()) return;
    const file = dom.xrayImport.files && dom.xrayImport.files[0];
    if (!file) return;
    const epoch = invalidateXrayRequest();
    stopXrayPlayback();
    dom.xrayContent.setAttribute('aria-busy', 'true');
    dom.xrayStatus.textContent = tr('正在核验导入记录并计算每一轮……');
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error(tr('记录超过 8 MiB 上限。'));
      // Send the uploaded bytes unchanged: an export's hash covers its exact numbers.
      const originalText = await file.text();
      const data = await xrayRequest('/trajectory', originalText, true);
      if (!state.xrayOpen || epoch !== state.xrayEpoch) return;
      if (!Array.isArray(data.states) || !data.states.length) throw new Error(tr('服务未返回轨迹。'));
      state.xraySource = 'import';
      state.xrayImported = data;
      state.xrayRecordText = originalText;
      state.xrayOfflineComparison = null;
      state.xrayFollow = true;
      showXrayTrajectory(data);
      dom.xrayStatus.textContent = data.verified
        ? tr('导入记录已验证 · 仅回放，不恢复判词或追问会话。')
        : tr('导入的事件记录 · 未附画像，仅回放轨迹，不恢复判词或追问会话。');
    } catch (error) {
      if (epoch !== state.xrayEpoch || !state.xrayOpen) return;
      dom.xrayStatus.textContent = tr`导入未通过：${error.message || tr('无法读取记录。')} 原体验保持不变。`;
    } finally {
      if (epoch === state.xrayEpoch) dom.xrayContent.setAttribute('aria-busy', 'false');
      dom.xrayImport.value = '';
    }
  }

  function xrayText(parent, text, tag = 'p', className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    parent.appendChild(element);
    return element;
  }

  function xrayDisclosure(parent, title) {
    const details = document.createElement('details');
    details.className = 'xray-disclosure';
    xrayText(details, title, 'summary');
    parent.appendChild(details);
    return details;
  }

  function xrayJSON(parent, title, read) {
    // Raw JSON is serialized only when someone opens it.
    const details = xrayDisclosure(parent, title);
    const pre = xrayText(details, tr('展开后显示。'), 'pre');
    details.addEventListener('toggle', () => {
      if (details.open) pre.textContent = JSON.stringify(read(), null, 2);
    });
    return { details, pre };
  }

  function xraySVG(tag, attributes = {}, parent = null, text = null) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
    if (text !== null) element.textContent = text;
    if (parent) parent.appendChild(element);
    return element;
  }

  function xrayClass(element, name, on) {
    if (on) element.classList.add(name); else element.classList.remove(name);
  }

  function formatXrayBytes(value) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return tr('未知');
    return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KiB`;
  }

  function xrayCardUrl(id) {
    if (!state.xrayCardUrls || state.xrayCardUrls.size !== state.allCards.length) {
      state.xrayCardUrls = new Map(state.allCards.map(card => [card.id, card.url]));
    }
    return state.xrayCardUrls.get(id) || `./assets/cards/${id}.webp`;
  }

  function xraySquash(value) {
    return Math.tanh(value / XRAY_SQUASH);
  }

  function researchXray(data) {
    return data && data.schema_version === 'norn.research-trajectory.v1';
  }

  function researchXrayLocked() {
    return Boolean(DECK && state.consent && !state.sessionSealed);
  }

  async function importXrayComparison() {
    if (!state.xrayOpen || researchXrayLocked()) return;
    const file = dom.xrayComparisonImport.files && dom.xrayComparisonImport.files[0];
    if (!file) return;
    const epoch = invalidateXrayRequest();
    stopXrayPlayback();
    dom.xrayStatus.textContent = tr('正在核验离线对照，读取不会调用模型……');
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error(tr('记录超过 8 MiB 上限。'));
      const raw = await file.text();
      const parsed = JSON.parse(raw);
      if (!parsed || parsed.schema_version !== 'norn.pamela-comparison.v1' || parsed.mode !== 'frozen_prefix') {
        throw new Error(tr('请选择冻结前缀的 PAMELA 离线对照报告。'));
      }
      const record = state.xraySource === 'import' && state.xrayRecordText
        ? JSON.parse(state.xrayRecordText) : mainJournal();
      const journal = record.journal || record;
      // Preserve Python's exact numeric JSON (including 1.0) inside the raw
      // envelope. The server verifies the report hash and every history/option
      // binding; JSON.parse above only checks the file shape, not its identity.
      const body = '{"journal":' + JSON.stringify(journal) + ',"comparison_report":' + raw + '}';
      const data = await xrayRequest('/trajectory', body, true);
      if (!state.xrayOpen || epoch !== state.xrayEpoch) return;
      const report = data.offline_comparison;
      if (!report || report.mode !== 'frozen_prefix' || !state.xrayTrajectory
          || report.events_hash !== state.xrayTrajectory.events_hash || report.deck !== state.xrayTrajectory.deck) {
        throw new Error(tr('离线对照不属于当前记录。请先导入对应的选图记录。'));
      }
      state.xrayOfflineComparison = report;
      const first = report.rounds && report.rounds[0];
      state.xrayRound = -1;
      showXrayRound(first ? first.round : 0);
      dom.xrayStatus.textContent = tr('离线对照与记录匹配 · 三路一起使用同一冻结前缀。');
    } catch (error) {
      if (epoch === state.xrayEpoch && state.xrayOpen) {
        dom.xrayStatus.textContent = tr`导入未通过：${error.message || tr('无法读取记录。')} 原体验保持不变。`;
      }
    } finally {
      dom.xrayComparisonImport.value = '';
    }
  }

  const COMPARISON_LANES = [
    { id: 'baseline', letter: 'B', name: '参考基线' },
    { id: 'learning', letter: 'L', name: '学习模型' },
    { id: 'jev', letter: 'J', name: 'Jev' },
  ];

  function comparisonProbability(value) {
    return value > 0 && value < 0.005 ? '<1%' : `${Math.round(value * 100)}%`;
  }

  function renderPredictiveComparison(data, round) {
    let report = state.xrayOfflineComparison || data.comparison;
    if (!state.xrayOfflineComparison && state.xraySource === 'live' && state.playMode
        && state.jevEnabled && window.NornPlayComparison && report) {
      if (!state.playComparisonCache || state.playComparisonCache.source !== report
          || state.playComparisonCache.revision !== state.jevRevision) {
        state.playComparisonCache = { source: report, revision: state.jevRevision,
          value: window.NornPlayComparison.merge(report, state.jevPredictions) };
      }
      report = state.playComparisonCache.value;
    }
    dom.xrayComparison.hidden = false;
    dom.xrayComparisonHeading.textContent = tr('它猜中了什么？');
    dom.xrayComparisonScope.textContent = tr('如果选一张：用此前选择预测这一轮，再与你实际保留的图片对照。');
    dom.xrayLossHeading.textContent = tr('随着轮次，谁更接近你的选择？');
    dom.xrayComparisonOnline.textContent = tr('返回逐轮对照');
    dom.xrayComparisonOnline.hidden = !state.xrayOfflineComparison;
    dom.xrayComparisonLegend.innerHTML = '';
    const frozen = report && report.mode === 'frozen_prefix';
    dom.xrayModelView.hidden = Boolean(frozen);
    dom.xraySource.textContent = frozen ? tr('离线留出对照')
      : state.xraySource === 'import'
        ? (data.verified ? tr('导入的记录 · 已核验') : tr('导入的事件记录'))
        : (data.source === 'session' ? tr('本次体验 · 已冻结') : tr('本次体验 · 实时'));
    dom.xrayComparisonMode.textContent = frozen
      ? tr`冻结前 ${report.training.cutoff} 轮` : tr('逐轮预测');
    const rows = report && Array.isArray(report.rounds) ? report.rounds : [];
    const current = rows.find(item => item.round === round);
    COMPARISON_LANES.forEach(lane => {
      const status = report && report.lanes && report.lanes[lane.id] && report.lanes[lane.id].status;
      const label = document.createElement('span');
      label.className = `xray-lane-label ${lane.id}`;
      xrayText(label, lane.letter, 'b', 'xray-lane-letter');
      xrayText(label, LAST_CARD && lane.id === 'learning' && current && current.same_predictions
        ? tr('学习模型 · 本轮沿用基线') : lane.id === 'jev' && status !== 'ok'
        ? tr(status === 'failed' ? 'Jev · 结果不可用' : 'Jev · 本次未运行') : tr(lane.name), 'span');
      dom.xrayComparisonLegend.appendChild(label);
    });
    dom.xrayComparisonCards.innerHTML = '';
    dom.xrayComparisonLoss.innerHTML = '';
    dom.xrayLossValue.textContent = '';
    if (!current) {
      dom.xrayComparisonContext.textContent = '';
      dom.xrayComparisonResult.textContent = frozen
        ? tr`前 ${report.training.cutoff} 轮只用于拟合。把时间轴拖到之后的轮次，查看留出对照。`
        : tr('完成一轮后，这里会把当时的预测与你的选择放在一起。');
      dom.xrayComparisonNote.textContent = LAST_CARD ? tr('Jev 仅在终局封存一次；这里回放探索阶段，不产生新调用。') : state.jevEnabled && state.playMode
        ? tr('Jev 已在开局前开启。打开、拖动和播放只读取已有预测，不新增调用。')
        : tr('Jev 默认关闭；可以在开局前手动开启，或事后导入结果。');
      return;
    }
    const display = current.display || {};
    const count = display.planned_count ?? current.candidate_ids.length;
    const seen = display.ever_seen_count;
    const columns = display.ready_columns;
    const context = [tr`第 ${round} 轮 · 共 ${count} 张`];
    if (Number.isInteger(seen)) context.push(tr`曾见 ${seen} 张`);
    if (Number.isInteger(columns)) context.push(tr`就绪时 ${columns} 列`);
    dom.xrayComparisonContext.textContent = context.join(' · ');
    dom.xrayComparisonCards.dataset.count = String(current.candidate_ids.length);
    dom.xrayComparisonCards.style.setProperty
      ? dom.xrayComparisonCards.style.setProperty('--options', current.candidate_ids.length || 1)
      : dom.xrayComparisonCards.style['--options'] = current.candidate_ids.length || 1;
    const picked = new Set(current.picks || []);
    current.candidate_ids.forEach((id, index) => {
      const tile = document.createElement('div');
      tile.className = 'xray-prediction-card';
      xrayClass(tile, 'actual', picked.has(id));
      xrayClass(tile, 'target', current.target === id);
      const image = xrayThumb(tile, id, 'xray-thumb xray-prediction-image');
      if (picked.has(id)) xrayText(image, current.target === id ? tr('首张保留') : '✓', 'span', 'xray-actual-label');
      COMPARISON_LANES.forEach(lane => {
        const prediction = current.lanes && current.lanes[lane.id];
        if (!prediction || prediction.status !== 'ok' || !Array.isArray(prediction.probabilities)) return;
        const probability = prediction.probabilities[index];
        const bar = document.createElement('div');
        bar.className = `xray-prediction-bar ${lane.id}`;
        xrayText(bar, lane.letter, 'span');
        const track = document.createElement('div');
        track.className = 'xray-prediction-track';
        const fill = document.createElement('i');
        fill.style.width = `${Math.max(0, Math.min(100, probability * 100))}%`;
        track.appendChild(fill);
        bar.appendChild(track);
        xrayText(bar, comparisonProbability(probability), 'b');
        bar.setAttribute('aria-label', tr`${tr(lane.name)}：${comparisonProbability(probability)}，并列第 ${prediction.ranks[index]} 名`);
        tile.appendChild(bar);
      });
      dom.xrayComparisonCards.appendChild(tile);
    });
    if (!current.scored) {
      dom.xrayComparisonResult.textContent = current.target === null
        ? tr('这一轮没有保留图片，不计入“如果选一张”的对照。')
        : tr('可比较图片不足两张，这轮不计入预测损失。');
    } else if (current.same_predictions) {
      dom.xrayComparisonResult.textContent = current.layer_before === 'population'
        ? tr('证据尚未支持个人差异，本轮学习模型与参考基线相同。')
        : tr('本轮两路预测相同。');
    } else {
      const target = current.candidate_ids.indexOf(current.target);
      dom.xrayComparisonResult.textContent = tr`首张保留的画面：基线第 ${current.lanes.baseline.ranks[target]} 名，学习模型第 ${current.lanes.learning.ranks[target]} 名。`;
    }
    dom.xrayComparisonNote.textContent = frozen
      ? tr`所有留出轮次都只用前 ${report.training.cutoff} 轮拟合。图片条件概率不包含“都不选”，也不表示人格准确率。`
      : tr`本轮模型只学到第 ${current.cutoff_rounds} 轮；在本轮曾见的相同图片中比较，不包含“都不选”。`;
    if (report.live_jev) dom.xrayComparisonNote.textContent += ' ' + tr('三路曲线只比较都有预测的轮次；未及时返回的 Jev 结果仅供回放，不计现场猜中。');
    renderComparisonLoss(report, round);
  }

  function renderComparisonLoss(report, round) {
    const rows = report.rounds || [];
    const before = rows.filter(item => item.round <= round);
    const last = before[before.length - 1];
    const n = last && last.cumulative ? last.cumulative.n : 0;
    dom.xrayLossValue.textContent = tr`${n} 个可评分轮次 · 越低越好`;
    if (!n) {
      xrayText(dom.xrayComparisonLoss, tr('还没有可评分的图片选择。'), 'p', 'xray-mini');
      return;
    }
    const keys = { baseline: 'baseline_nll', learning: 'learning_nll', jev: 'jev_nll' };
    const available = COMPARISON_LANES.filter(lane => rows.some(item => item.cumulative
      && Number.isFinite(item.cumulative[keys[lane.id]])));
    const allValues = rows.flatMap(item => available.map(lane => item.cumulative && item.cumulative[keys[lane.id]])).filter(Number.isFinite);
    const ceiling = Math.max(1, Math.ceil(Math.max(...allValues) * 10) / 10);
    const firstRound = rows[0].round, lastRound = rows[rows.length - 1].round;
    const W = Math.max(260, Math.min(620, dom.xrayComparisonLoss.getBoundingClientRect().width || 520));
    const H = 132, L = 35, R = 13, T = 14, B = 26;
    const x = value => L + (value - firstRound) / Math.max(1, lastRound - firstRound) * (W - L - R);
    const y = value => T + (1 - value / ceiling) * (H - T - B);
    const svg = xraySVG('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': tr('图片条件平均预测损失，越低越好；曲线只画到当前回放轮次。') }, dom.xrayComparisonLoss);
    [0, ceiling].forEach(value => {
      xraySVG('line', { x1: L, x2: W - R, y1: y(value), y2: y(value), class: 'xray-loss-grid' }, svg);
      xraySVG('text', { x: L - 7, y: y(value) + 4, 'text-anchor': 'end', class: 'xray-loss-axis' }, svg, value.toFixed(1));
    });
    xraySVG('text', { x: L, y: H - 5, class: 'xray-loss-axis' }, svg, String(firstRound));
    if (lastRound !== firstRound) xraySVG('text', { x: W - R, y: H - 5, 'text-anchor': 'end', class: 'xray-loss-axis' }, svg, String(lastRound));
    available.forEach(lane => {
      const points = before.filter(item => item.cumulative && Number.isFinite(item.cumulative[keys[lane.id]]));
      if (!points.length) return;
      xraySVG('polyline', { points: points.map(item => `${x(item.round)},${y(item.cumulative[keys[lane.id]])}`).join(' '),
        class: `xray-loss-line ${lane.id}`, fill: 'none' }, svg);
      const endpoint = points[points.length - 1];
      xraySVG('circle', { cx: x(endpoint.round), cy: y(endpoint.cumulative[keys[lane.id]]), r: 3.5,
        class: `xray-loss-point ${lane.id}` }, svg);
    });
    xraySVG('line', { x1: x(round), x2: x(round), y1: T, y2: H - B, class: 'xray-loss-cursor' }, svg);
    const readouts = document.createElement('div');
    readouts.className = 'xray-loss-readouts';
    available.forEach(lane => {
      const value = last.cumulative[keys[lane.id]];
      if (!Number.isFinite(value)) return;
      const item = document.createElement('span');
      item.className = lane.id;
      xrayText(item, lane.letter, 'b');
      xrayText(item, `${tr(lane.name)} ${value.toFixed(2)}`, 'span');
      readouts.appendChild(item);
    });
    dom.xrayComparisonLoss.appendChild(readouts);
  }

  function researchLayerName(layer) {
    return tr(layer === 'personal' ? '个人偏好' : layer === 'sensitivity' ? '口味敏感度' : '参考口味');
  }

  function prepareResearchXray(data) {
    dom.xrayPanel.classList.add('xray-research');
    [dom.xrayAxisTop, dom.xrayAxisBottom, dom.xrayAxisLeft, dom.xrayAxisRight].forEach(axis => { axis.hidden = true; });
    dom.xrayWeaveHeading.textContent = tr('画面星图');
    dom.xrayLegend.textContent = tr('位置按参考评分排序，保持不动；越亮，当前预测越靠前。白框是本轮展示，金框是本轮选中，金点是此前选过。');
    dom.xrayBarsHeading.textContent = tr('织机了解到了哪一层？');
    dom.xrayBarsNote.textContent = tr('只有选择积累的证据达到门槛，才启用更个人化的一层；未通过不代表你没有偏好。');
    dom.xrayModelSummary.textContent = tr('模型现在怎么看？');
    dom.xrayComparison.hidden = false;
    dom.xrayGuessHeading.textContent = tr('尚未展示的画面');
    dom.xrayGuessNote.textContent = tr('这是当前模型对未展示画面的排序，不是已经验证的下一轮预测。');
    const signature = `research:${data.cards.map(card => card.id).join('|')}`;
    if (state.xrayWeaveSignature !== signature) {
      state.xrayWeaveSignature = signature;
      dom.xrayWeave.innerHTML = '';
      state.xrayCells = new Map();
      const byReference = data.cards.map((card, index) => ({ card, index }))
        .sort((left, right) => right.card.u - left.card.u || left.index - right.index);
      byReference.forEach(({ card, index }, position) => {
        const cell = document.createElement('div');
        cell.className = 'xray-cell';
        cell.dataset.cardId = card.id;
        cell.style.gridColumn = String(position % 16 + 1);
        cell.style.gridRow = String(Math.floor(position / 16) + 1);
        cell.style.backgroundImage = `url("${xrayCardUrl(card.id)}")`;
        cell.addEventListener('pointerenter', event => showXrayTip(card, index, event));
        cell.addEventListener('pointermove', moveXrayTip);
        cell.addEventListener('pointerleave', () => { dom.xrayTip.hidden = true; });
        // A tap also opens the same small preview on touch screens.
        cell.addEventListener('click', event => showXrayTip(card, index, event));
        dom.xrayWeave.appendChild(cell);
        state.xrayCells.set(card.id, cell);
      });
    }
  }

  function researchEvidence(current, key) {
    const value = current.log_evalues && current.log_evalues[key];
    const threshold = Number.isFinite(current.log_threshold) ? current.log_threshold : Math.log(20);
    return Number.isFinite(value) ? Math.exp(Math.max(-40, Math.min(20, value - threshold))) : 0;
  }

  function renderResearchXray(data, current, shown, picked, earlier) {
    renderPredictiveComparison(data, current.round);
    // The population reference remains a real ranking at layer zero. It must
    // never be flattened or presented as an established personal preference.
    const scores = current.scores;
    const mean = scores.reduce((sum, value) => sum + value, 0) / scores.length;
    const spread = Math.sqrt(scores.reduce((sum, value) => sum + (value - mean) ** 2, 0) / scores.length);
    data.cards.forEach((card, index) => {
      const cell = state.xrayCells.get(card.id);
      const lit = spread > 1e-9 ? 1 / (1 + Math.exp(-1.6 * (scores[index] - mean) / spread)) : 0.5;
      cell.style.opacity = (0.16 + 0.84 * lit).toFixed(3);
      cell.style.filter = `saturate(${(0.25 + 0.9 * lit).toFixed(3)}) brightness(${(0.45 + 0.7 * lit).toFixed(3)})`;
      xrayClass(cell, 'shown', shown.has(card.id) && !picked.has(card.id));
      xrayClass(cell, 'picked', picked.has(card.id));
      xrayClass(cell, 'past', earlier.has(card.id) && !picked.has(card.id));
    });
    dom.xrayWeave.setAttribute('aria-label', tr`第 ${current.round} 轮 · ${researchLayerName(current.layer)}排序，越亮的画面预测越靠前。`);

    dom.xrayBars.innerHTML = '';
    const stages = [
      { layer: 'population', label: tr('参考口味'), note: tr('从参考评分者开始') },
      { layer: 'sensitivity', label: tr('口味敏感度'), note: tr('你多大程度跟随参考口味') },
      { layer: 'personal', label: tr('个人偏好'), note: tr('哪些画面元素更吸引你') },
    ];
    const flow = document.createElement('div');
    flow.className = 'xray-stages';
    stages.forEach((stage, index) => {
      const item = document.createElement('div');
      item.className = 'xray-stage';
      xrayClass(item, 'active', stage.layer === current.layer);
      xrayText(item, `0${index + 1}`, 'span', 'xray-stage-number');
      xrayText(item, stage.label, 'strong');
      xrayText(item, stage.note, 'span', 'xray-stage-note');
      xrayText(item, stage.layer === current.layer ? tr('当前使用') : '·', 'span', 'xray-stage-status');
      flow.appendChild(item);
    });
    dom.xrayBars.appendChild(flow);

    dom.xrayTypes.innerHTML = '';
    [['E1', tr('敏感度证据')], ['E2', tr('个人差异证据')]].forEach(([key, label]) => {
      const ratio = researchEvidence(current, key);
      const row = document.createElement('div');
      row.className = 'xray-evidence';
      xrayText(row, label, 'span');
      const track = document.createElement('div');
      track.className = 'xray-evidence-track';
      const fill = document.createElement('div');
      fill.className = ratio >= 1 ? 'xray-evidence-fill passed' : 'xray-evidence-fill';
      // A capped log display keeps tiny values legible without pretending the
      // evidence ratio is a probability or a calibrated confidence level.
      fill.style.width = `${Math.min(100, Math.log1p(ratio) / Math.log(2) * 100).toFixed(1)}%`;
      track.appendChild(fill);
      row.appendChild(track);
      xrayText(row, ratio >= 100 ? '≥100×' : `${ratio.toFixed(2)}×`, 'b');
      row.setAttribute('aria-label', tr`${label}：门槛的 ${ratio >= 100 ? '≥100' : ratio.toFixed(2)} 倍`);
      dom.xrayTypes.appendChild(row);
    });
    xrayText(dom.xrayTypes, tr('1× 达到证据门槛；这不是准确率或概率。'), 'p', 'xray-mini');

    dom.xrayStrip.innerHTML = '';
    dom.xrayRoundNote.innerHTML = '';
    dom.xrayRoundHeading.textContent = current.round ? tr`第 ${current.round} 轮` : tr('还没开始');
    (current.shown || []).forEach(id => xrayThumb(dom.xrayStrip, id, picked.has(id) ? 'xray-thumb on' : 'xray-thumb'));
    (current.unseen || []).forEach(id => xrayThumb(dom.xrayStrip, id, 'xray-thumb unseen'));
    if (!current.round) {
      xrayText(dom.xrayRoundNote, tr('还没有个人证据，从参考评分者的画面排序开始。'));
    } else {
      xrayText(dom.xrayRoundNote, tr`选了 ${current.picks.length} / ${(current.shown || []).length} 张`);
      const previous = data.states[Math.max(0, state.xrayRound - 1)];
      xrayText(dom.xrayRoundNote, previous.layer !== current.layer
        ? tr`这一轮启用了${researchLayerName(current.layer)}。`
        : tr`当前使用${researchLayerName(current.layer)}。`);
    }
    dom.xrayGuesses.innerHTML = '';
    (current.unseen_top || current.top.filter(id => !shown.has(id) && !picked.has(id) && !earlier.has(id))).forEach(id => {
      const hit = picked.has(id) || earlier.has(id);
      const thumb = xrayThumb(dom.xrayGuesses, id, hit ? 'xray-thumb guess hit' : 'xray-thumb guess');
      if (hit) thumb.title += tr(' · 已选');
    });
  }

  function renderResearchDetails(data) {
    if (state.xrayOfflineComparison) {
      const report = state.xrayOfflineComparison;
      if (state.xrayDetailsFor !== report) {
        state.xrayDetailsFor = report;
        dom.xrayDetails.innerHTML = '';
        xrayText(dom.xrayDetails, tr`三路都固定使用前 ${report.training.cutoff} 轮，后续选择不更新任何一条模型。`);
        xrayText(dom.xrayDetails, tr('已校验记录、牌库、预测任务和报告完整性；这不认证报告中的远程模型调用。'));
        const round = xrayJSON(dom.xrayDetails, tr('查看本轮对照 JSON'), () => report.rounds.find(item => item.round === state.xrayRound) || null);
        xrayJSON(dom.xrayDetails, tr('查看预测对照 JSON'), () => report);
        state.xrayDetailNodes = { round };
      }
      const round = state.xrayDetailNodes.round;
      if (round.details.open) round.pre.textContent = JSON.stringify(report.rounds.find(item => item.round === state.xrayRound) || null, null, 2);
      return;
    }
    const current = data.states[Math.max(0, state.xrayRound)];
    if (state.xrayDetailsFor !== data) {
      state.xrayDetailsFor = data;
      const root = dom.xrayDetails;
      root.innerHTML = '';
      xrayText(root, tr('每张画面的分数 = 参考口味贡献 + 8 项内容贡献。这里展示生效参数，未通过门槛的候选不进入分数。'));
      xrayText(root, tr('拖动与播放只切换本地状态，不请求后端，也不调用模型。'));
      xrayText(root, tr('首张保留指最终保留图片中最早被点选的一张，撤回的图片不算。'));
      xrayText(root, `${data.algorithm_version || data.model_version || ''} · ${data.deck || DECK} · ${shortProfileHash(data.events_hash)}`, 'p', 'xray-mono');
      const parameters = xrayText(root, '', 'p', 'xray-mono');
      const breakdown = xrayDisclosure(root, tr('画面分数是怎样组成的？'));
      const chart = document.createElement('div');
      chart.className = 'xray-contribution-cards';
      breakdown.appendChild(chart);
      breakdown.addEventListener('toggle', () => renderResearchDetails(state.xrayTrajectory));
      const round = xrayJSON(root, tr('查看本轮状态 JSON'), () => state.xrayTrajectory.states[Math.max(0, state.xrayRound)]);
      xrayJSON(root, tr('查看版本与来源 JSON'), () => ({ schema_version: data.schema_version, deck: data.deck,
        algorithm_version: data.algorithm_version, events_hash: data.events_hash, profile: data.profile || null }));
      xrayJSON(root, tr('查看预测对照 JSON'), () => state.xrayOfflineComparison || data.comparison || null);
      state.xrayDetailNodes = { parameters, breakdown, chart, round };
    }
    const nodes = state.xrayDetailNodes;
    nodes.parameters.textContent = tr`当前使用${researchLayerName(current.layer)}。`;
    if (nodes.round.details.open) nodes.round.pre.textContent = JSON.stringify(current, null, 2);
    if (!nodes.breakdown.open) return;
    nodes.chart.innerHTML = '';
    (current.contributions || []).forEach(card => {
      const row = document.createElement('div');
      row.className = 'xray-contribution-card';
      xrayThumb(row, card.card_id, 'xray-thumb guess');
      const body = document.createElement('div');
      xrayText(body, tr`合计 ${card.score.toFixed(3)}`, 'b');
      const scale = Math.max(0.001, ...card.terms.map(Math.abs));
      const terms = document.createElement('div');
      terms.className = 'xray-term-bars';
      card.terms.forEach((term, index) => {
        const bar = document.createElement('div');
        bar.className = 'xray-term';
        const label = index === 0 ? tr('参考口味') : tr`内容 ${index}`;
        xrayText(bar, index ? String(index) : 'R', 'span');
        const track = document.createElement('div');
        track.className = 'xray-term-track';
        const fill = document.createElement('i');
        fill.className = term < 0 ? 'negative' : 'positive';
        fill.style.width = `${Math.abs(term) / scale * 50}%`;
        fill.style.left = term < 0 ? `${50 - Math.abs(term) / scale * 50}%` : '50%';
        track.appendChild(fill);
        bar.appendChild(track);
        xrayText(bar, term.toFixed(3), 'span');
        bar.title = `${label} · ${term.toFixed(6)}`;
        terms.appendChild(bar);
      });
      body.appendChild(terms);
      row.appendChild(body);
      nodes.chart.appendChild(row);
    });
  }

  function xrayOnBaseline(current) {
    return Boolean(current.guard && current.guard.status === 'baseline');
  }

  function buildXrayWeave(cards) {
    const signature = cards.map(card => `${card.id}:${card.cell.join(',')}`).join('|');
    if (state.xrayWeaveSignature === signature) return;
    state.xrayWeaveSignature = signature;
    dom.xrayWeave.innerHTML = '';
    state.xrayCells = new Map();
    ['v', 'h'].forEach(direction => {
      const line = document.createElement('div');
      line.className = `xray-quad ${direction}`;
      dom.xrayWeave.appendChild(line);
    });
    cards.forEach((card, index) => {
      const cell = document.createElement('div');
      cell.className = 'xray-cell';
      cell.dataset.cardId = card.id;
      cell.style.gridColumn = String(card.cell[0] + 1);
      cell.style.gridRow = String(card.cell[1] + 1);
      cell.style.backgroundImage = `url("${xrayCardUrl(card.id)}")`;
      cell.addEventListener('pointerenter', event => showXrayTip(card, index, event));
      cell.addEventListener('pointermove', moveXrayTip);
      cell.addEventListener('pointerleave', () => { dom.xrayTip.hidden = true; });
      dom.xrayWeave.appendChild(cell);
      state.xrayCells.set(card.id, cell);
    });
    // One square viewBox: the marker needs no pixel measurement or resize handler.
    const svg = xraySVG('svg', { class: 'xray-overlay', viewBox: '0 0 100 100', 'aria-hidden': 'true' }, dom.xrayWeave);
    const marker = xraySVG('g', { class: 'xray-marker' }, svg);
    state.xrayMarker = {
      group: marker,
      halo: xraySVG('ellipse', { class: 'xray-marker-halo', rx: 4, ry: 4 }, marker),
      ring: xraySVG('ellipse', { class: 'xray-marker-ring', rx: 4, ry: 4 }, marker),
      dot: xraySVG('circle', { class: 'xray-marker-dot', r: 1.1 }, marker),
      label: xraySVG('text', { class: 'xray-marker-label', x: 2, y: -2 }, marker, tr('你')),
    };
  }

  function buildXrayBars() {
    if (state.xrayBars) return;
    state.xrayBars = XRAY_AXES.map(axis => {
      const row = document.createElement('div');
      row.className = 'xray-bar';
      const negative = xrayText(row, tr(axis.neg), 'span');
      const track = document.createElement('div');
      track.className = 'xray-bar-track';
      const band = document.createElement('div');
      band.className = 'xray-band';
      const dot = document.createElement('div');
      dot.className = 'xray-dot';
      track.appendChild(band);
      track.appendChild(dot);
      row.appendChild(track);
      const positive = xrayText(row, tr(axis.pos), 'span');
      dom.xrayBars.appendChild(row);
      return { row, band, dot, negative, positive };
    });
  }

  function renderXrayTicks(states) {
    dom.xrayTicks.innerHTML = '';
    const rounds = states.length - 1;
    states.slice(1).forEach((round, index) => {
      const tick = document.createElement('i');
      tick.className = round.picks.length ? 'xray-tick' : 'xray-tick empty';
      tick.style.left = `${(index + 1) / rounds * 100}%`;
      dom.xrayTicks.appendChild(tick);
    });
  }

  function showXrayTrajectory(data) {
    if (DECK && !researchXray(data)) throw new Error(tr('牌库与轨迹版本不匹配，请刷新后重试。'));
    // Both engines return one complete, provider-free trajectory. Normalize the
    // selection field at this display boundary without changing exported bytes.
    if (data.schema_version === 'norn.research-trajectory.v1') {
      data = { ...data, states: data.states.map(current => ({ ...current, picks: current.picked || current.picks || [] })) };
    }
    const previous = state.xrayTrajectory;
    if (state.xrayOfflineComparison && (state.xrayOfflineComparison.events_hash !== data.events_hash
        || state.xrayOfflineComparison.deck !== data.deck)) state.xrayOfflineComparison = null;
    if (data.offline_comparison) state.xrayOfflineComparison = data.offline_comparison;
    const last = data.states.length - 1;
    const follow = state.xrayFollow || !previous || state.xrayRound >= previous.states.length - 1;
    state.xrayFollow = false;
    state.xrayTrajectory = data;
    if (researchXray(data)) {
      prepareResearchXray(data);
      dom.xrayContent.hidden = false;
    } else {
      dom.xrayComparison.hidden = true;
      buildXrayWeave(data.cards);
      buildXrayBars();
    }
    if (previous !== data) {
      renderXrayTicks(data.states);
      state.xrayTrajectorySize = JSON.stringify(data).length;  // ASCII-dominated, so about bytes
    }
    dom.xrayRound.max = String(last);
    dom.xrayLive.hidden = state.xraySource === 'live';
    dom.xraySource.textContent = state.xraySource === 'import'
      ? (data.verified ? tr('导入的记录 · 已核验') : tr('导入的事件记录'))
      : (data.source === 'session' ? tr('本次体验 · 已冻结') : tr('本次体验 · 实时'));
    state.xrayRound = -1;
    showXrayRound(follow ? last : Math.min(Number(dom.xrayRound.value) || 0, last));
  }

  function showXrayRound(value) {
    const data = state.xrayTrajectory;
    if (!data) return;
    const states = data.states;
    const last = states.length - 1;
    const round = Math.max(0, Math.min(last, Math.round(Number(value) || 0)));
    if (round === state.xrayRound) return;
    state.xrayRound = round;
    const current = states[round];
    dom.xrayRound.value = String(round);
    if (dom.xrayRound.style.setProperty) dom.xrayRound.style.setProperty('--fill', `${last ? round / last * 100 : 0}%`);
    dom.xrayRoundLabel.textContent = tr`${round} / ${last} 轮`;
    dom.xrayPlay.setAttribute('aria-label', state.xrayPlayTimer ? tr('暂停') : tr`从第 ${round >= last ? 0 : round} 轮播放`);

    const earlier = new Set();
    states.slice(1, round).forEach(item => item.picks.forEach(id => earlier.add(id)));
    const shown = new Set(current.shown || []);
    const picked = new Set(current.picks || []);
    if (researchXray(data)) {
      renderResearchXray(data, current, shown, picked, earlier);
      if (dom.xrayEngineering.open) renderXrayDetails();
      return;
    }
    renderXrayWeave(data, current, shown, picked, earlier);
    renderXrayMarker(current);
    renderXrayBars(current);
    renderXrayRoundStrip(current, round, picked);
    renderXrayGuesses(current, picked, earlier);
    if (dom.xrayEngineering.open) renderXrayDetails();
  }

  function renderXrayWeave(data, current, shown, picked, earlier) {
    const baseline = xrayOnBaseline(current);
    const scores = current.scores;
    const mean = scores.reduce((sum, value) => sum + value, 0) / scores.length;
    const spread = Math.sqrt(scores.reduce((sum, value) => sum + (value - mean) ** 2, 0) / scores.length);
    data.cards.forEach((card, index) => {
      const cell = state.xrayCells.get(card.id);
      const lit = !baseline && spread > 1e-9 ? 1 / (1 + Math.exp(-1.6 * (scores[index] - mean) / spread)) : 0.5;
      cell.style.opacity = (0.16 + 0.84 * lit).toFixed(3);
      cell.style.filter = `saturate(${(0.25 + 0.9 * lit).toFixed(3)}) brightness(${(0.45 + 0.7 * lit).toFixed(3)})`;
      xrayClass(cell, 'shown', shown.has(card.id) && !picked.has(card.id));
      xrayClass(cell, 'picked', picked.has(card.id));
      xrayClass(cell, 'past', earlier.has(card.id) && !picked.has(card.id));
    });
    if (baseline) {
      dom.xrayWeave.setAttribute('aria-label', tr('证据不足，暂不区分图片'));
      return;
    }
    const best = current.types[0];
    dom.xrayWeave.setAttribute('aria-label', tr`第 ${current.round} 轮后的偏好织锦：最亮的画面最可能吸引你。`
      + (best ? tr`当前最可能的画面原型 ${best.type}，${Math.round(best.p * 100)}%。` : ''));
  }

  function renderXrayMarker(current) {
    // Position on the S/N x T/F plane; the ellipse is 1.5 sigma mapped through
    // the same tanh squash (local Jacobian), in square viewBox units.
    const marker = state.xrayMarker;
    // The candidate's covariance is not a posterior around the gated zero
    // scoring point. A baseline state has no personal center or ellipse.
    marker.group.style.display = xrayOnBaseline(current) ? 'none' : '';
    if (xrayOnBaseline(current)) return;
    const reach = 50 * 0.94;
    const [mx, my] = [current.mean[1], current.mean[2]];
    const x = 50 + xraySquash(mx) * reach;
    const y = 50 - xraySquash(my) * reach;
    marker.group.style.transform = `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px)`;
    marker.group.setAttribute('transform', `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
    const jx = reach / XRAY_SQUASH * (1 - xraySquash(mx) ** 2);
    const jy = reach / XRAY_SQUASH * (1 - xraySquash(my) ** 2);
    const c = current.cov_sn_tf;
    const a = jx * jx * c[0][0], d = jy * jy * c[1][1], b = -jx * jy * c[0][1];
    const half = (a + d) / 2, root = Math.sqrt(Math.max(((a - d) / 2) ** 2 + b * b, 0));
    const major = half + root, minor = Math.max(half - root, 0);
    const angle = b ? Math.atan2(major - a, b) * 180 / Math.PI : 0;
    [marker.halo, marker.ring].forEach(ellipse => {
      ellipse.setAttribute('rx', Math.max(1.4, 1.5 * Math.sqrt(major)).toFixed(2));
      ellipse.setAttribute('ry', Math.max(1.4, 1.5 * Math.sqrt(minor)).toFixed(2));
      ellipse.setAttribute('transform', `rotate(${angle.toFixed(1)})`);
    });
  }

  function renderXrayBars(current) {
    const baseline = xrayOnBaseline(current);
    const percent = value => 50 + xraySquash(value) * 50;
    state.xrayBars.forEach((bar, index) => {
      bar.dot.style.display = baseline ? 'none' : '';
      bar.band.style.display = baseline ? 'none' : '';
      const mean = current.mean[index], spread = current.sd[index];
      bar.dot.style.left = `${percent(mean).toFixed(2)}%`;
      bar.band.style.left = `${percent(mean - spread).toFixed(2)}%`;
      bar.band.style.width = `${(percent(mean + spread) - percent(mean - spread)).toFixed(2)}%`;
      const clear = !baseline && Math.abs(mean) > spread;  // a side lights up only beyond one sd
      xrayClass(bar.negative, 'lean', clear && mean < 0);
      xrayClass(bar.positive, 'lean', clear && mean > 0);
      bar.row.setAttribute('aria-label', baseline ? tr('证据不足，暂不区分图片')
        : tr`${XRAY_AXES[index].neg} 与 ${XRAY_AXES[index].pos}：估计 ${mean.toFixed(2)}，标准差 ${spread.toFixed(2)}`);
    });
  }

  function xrayThumb(parent, id, className) {
    const thumb = document.createElement('div');
    thumb.className = className;
    thumb.style.backgroundImage = `url("${xrayCardUrl(id)}")`;
    const cards = state.xrayTrajectory && Array.isArray(state.xrayTrajectory.cards)
      ? state.xrayTrajectory.cards : state.allCards;
    const card = cards.find(item => item.id === id);
    thumb.title = card ? `${id} · ${card.type}` : id;
    parent.appendChild(thumb);
    return thumb;
  }

  function renderXrayRoundStrip(current, round, picked) {
    dom.xrayStrip.innerHTML = '';
    dom.xrayRoundNote.innerHTML = '';
    if (!round) {
      dom.xrayRoundHeading.textContent = tr('还没开始');
      dom.xrayRoundNote.textContent = xrayOnBaseline(current) ? tr('证据不足，暂不区分图片')
        : tr('没有任何选择时，四个方向同样可能；织锦整体半亮。');
      return;
    }
    dom.xrayRoundHeading.textContent = tr`第 ${round} 轮`;
    current.shown.forEach(id => xrayThumb(dom.xrayStrip, id, picked.has(id) ? 'xray-thumb on' : 'xray-thumb'));
    (current.unseen || []).forEach(id => {
      xrayThumb(dom.xrayStrip, id, 'xray-thumb unseen').title += tr(' · 未进入视野，不计入证据');
    });
    const count = current.picks.length;
    const seconds = typeof current.duration_ms === 'number' ? tr` · 用时 ${(current.duration_ms / 1000).toFixed(1)}s` : '';
    xrayText(dom.xrayRoundNote, (count ? tr`选了 ${count} / ${current.shown.length} 张`
      : tr`${current.shown.length} 张都没选（同样是证据）`) + seconds + '　→　', 'span');
    if (xrayOnBaseline(current)) {
      xrayText(dom.xrayRoundNote, tr('证据不足，暂不区分图片'), 'span');
      return;
    }
    const moves = current.delta.map((value, index) => ({ value, index }))
      .filter(move => Math.abs(move.value) >= 0.05)
      .sort((left, right) => Math.abs(right.value) - Math.abs(left.value)).slice(0, 2);
    if (!moves.length) {
      xrayText(dom.xrayRoundNote, tr('重心几乎不动'), 'span');
      return;
    }
    xrayText(dom.xrayRoundNote, tr('重心移向 '), 'span');
    moves.forEach((move, position) => {
      const axis = XRAY_AXES[move.index];
      xrayText(dom.xrayRoundNote, tr(move.value > 0 ? axis.pos : axis.neg), 'b');
      xrayText(dom.xrayRoundNote, ` ${Math.abs(move.value).toFixed(2)}${position < moves.length - 1 ? (currentLanguage() === 'en' ? ', ' : '，') : ''}`, 'span');
    });
  }

  function renderXrayGuesses(current, picked, earlier) {
    dom.xrayTypes.innerHTML = '';
    dom.xrayGuesses.innerHTML = '';
    if (xrayOnBaseline(current)) {
      xrayText(dom.xrayTypes, tr('证据不足，暂不区分图片'), 'p', 'xray-mini');
      return;
    }
    current.types.slice(0, 3).forEach((row, index) => {
      const item = document.createElement('div');
      item.className = index ? 'xray-type' : 'xray-type best';
      xrayText(item, row.type, 'span');
      const track = document.createElement('div');
      track.className = 'xray-type-track';
      const fill = document.createElement('div');
      fill.className = 'xray-type-fill';
      fill.style.width = `${(row.p * 100).toFixed(1)}%`;
      track.appendChild(fill);
      item.appendChild(track);
      xrayText(item, row.p > 0 && row.p < 0.005 ? '<1%' : `${Math.round(row.p * 100)}%`, 'b');
      dom.xrayTypes.appendChild(item);
    });
    current.top.forEach(id => {
      const hit = picked.has(id) || earlier.has(id);
      const thumb = xrayThumb(dom.xrayGuesses, id, hit ? 'xray-thumb guess hit' : 'xray-thumb guess');
      if (hit) thumb.title += tr(' · 已选');
    });
  }

  function showXrayTip(card, index, event) {
    const data = state.xrayTrajectory;
    if (!data) return;
    const current = data.states[state.xrayRound];
    const scores = current.scores;
    const rank = scores.filter(value => value > scores[index]).length + 1;
    dom.xrayTip.innerHTML = '';
    const image = document.createElement('img');
    image.src = xrayCardUrl(card.id);
    image.alt = '';
    dom.xrayTip.appendChild(image);
    xrayText(dom.xrayTip, `${card.id} · ${card.type}`, 'div');
    xrayText(dom.xrayTip, researchXray(data) ? tr`当前排名 ${rank} / ${scores.length}` : xrayOnBaseline(current) ? tr('证据不足，暂不区分图片')
      : state.xrayRound ? tr`当前排名 ${rank} / ${scores.length}` : tr('尚无选择，排名未定'), 'div', 'xray-mini');
    dom.xrayTip.hidden = false;
    moveXrayTip(event);
  }

  function moveXrayTip(event) {
    const x = Math.min(event.clientX + 14, window.innerWidth - 164);
    const y = Math.min(event.clientY + 14, window.innerHeight - 240);
    dom.xrayTip.style.left = `${x}px`;
    dom.xrayTip.style.top = `${y}px`;
  }

  function toggleXrayPlayback() {
    if (state.xrayPlayTimer) {
      stopXrayPlayback();
      return;
    }
    const data = state.xrayTrajectory;
    if (!data || data.states.length < 2) return;
    if (state.xrayRound >= data.states.length - 1) showXrayRound(0);
    dom.xrayPlay.textContent = '❚❚';
    const advance = () => {
      const last = state.xrayTrajectory.states.length - 1;
      if (!state.xrayOpen || state.xrayRound >= last) {
        stopXrayPlayback();
        return;
      }
      showXrayRound(state.xrayRound + 1);
      state.xrayPlayTimer = setTimeout(advance, XRAY_PLAY_MS);
    };
    state.xrayPlayTimer = setTimeout(advance, XRAY_PLAY_MS);
    dom.xrayPlay.setAttribute('aria-label', tr('暂停'));
  }

  function stopXrayPlayback() {
    if (state.xrayPlayTimer) clearTimeout(state.xrayPlayTimer);
    state.xrayPlayTimer = null;
    if (!dom.xrayPlay) return;
    dom.xrayPlay.textContent = '▶';
    const last = state.xrayTrajectory ? state.xrayTrajectory.states.length - 1 : 0;
    dom.xrayPlay.setAttribute('aria-label', tr`从第 ${state.xrayRound >= last ? 0 : Math.max(0, state.xrayRound)} 轮播放`);
  }

  function cacheSummary(calls) {
    if (!calls.length) return tr('缓存状态未知');
    const actual = calls.filter(call => call.simulated !== true && call.provider !== 'mock');
    if (!actual.length) return tr('模拟运行，无模型用量');
    let observed = 0;
    let read = 0;
    actual.forEach(call => {
      const usage = call.usage || {};
      const key = usage.cache_read_input_tokens !== undefined ? 'cache_read_input_tokens' : 'cached_tokens';
      if (usage[`${key}_observed`] === true && typeof usage[key] === 'number') {
        observed++;
        read += usage[key];
      }
    });
    if (!observed) return tr('缓存读取未知');
    return tr`缓存读取 ${read} Token（实测${observed < actual.length ? tr('，部分请求未知') : ''}）`;
  }

  function renderXrayDetails() {
    // Every JSON, version, cache and byte disclosure lives here, built only
    // while open; scrubbing then rewrites just the round-dependent lines.
    const data = state.xrayTrajectory;
    if (!dom.xrayEngineering.open || !data) return;
    if (researchXray(data)) {
      renderResearchDetails(data);
      return;
    }
    if (state.xrayDetailsFor !== data) buildXrayDetails(data);
    const current = data.states[Math.max(0, state.xrayRound)];
    const nodes = state.xrayDetailNodes;
    const precision = current.guard ? current.guard.weight_precision ?? data.prior?.weight_precision_formula ?? tr('未知')
      : data.prior?.weight_precision ?? tr('未知');
    const stopPrecision = current.guard?.stop_precision ?? data.prior?.stop_precision ?? tr('未知');
    const precisionLabel = current.guard || data.prior ? `${precision} / ${stopPrecision}` : tr('未知');
    nodes.parameters.textContent = tr`参数 12 个偏好权重 + 1 个停止偏置（第 ${current.round} 轮后 ${current.stop_bias}）· 先验精度 ${precisionLabel}`;
    nodes.posterior.textContent = current.guard
      ? tr`生效参数 ${XRAY_AXES.map((axis, index) => `${axis.p}/${axis.n} ${current.mean[index].toFixed(2)}`).join('  ')}；候选标准差 ${current.sd.map(value => value.toFixed(2)).join(' / ')}。`
      : tr`后验 ${XRAY_AXES.map((axis, index) =>
        `${axis.p}/${axis.n} ${current.mean[index].toFixed(2)}±${current.sd[index].toFixed(2)}`).join('  ')}`;
    if (nodes.round.details.open) nodes.round.pre.textContent = JSON.stringify(current, null, 2);
    renderXrayContributions();
  }

  function renderXrayContributions() {
    // Python's per-feature terms for this round's top guesses and picks; the page only lays them out.
    const data = state.xrayTrajectory;
    const nodes = state.xrayDetailNodes;
    if (!data || !nodes || !nodes.breakdown.open) return;
    const current = data.states[Math.max(0, state.xrayRound)];
    const block = current.contributions;
    nodes.table.innerHTML = '';
    if (!block) {
      xrayText(nodes.table, tr('这份轨迹没有逐维贡献数据。'), 'p', 'xray-mini');
      return;
    }
    const signed = value => `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(2)}`;
    const table = document.createElement('table');
    const head = document.createElement('tr');
    [tr('画面'), ...XRAY_AXES.map(axis => `${axis.p}/${axis.n}`), tr('像素'), tr('合计')].forEach(label =>
      xrayText(head, label, 'th').setAttribute('scope', 'col'));
    table.appendChild(head);
    const top = new Set(xrayOnBaseline(current) ? [] : current.top);
    const picks = new Set(current.picks || []);
    block.cards.forEach(card => {
      const row = document.createElement('tr');
      const role = [top.has(card.card_id) && tr('猜测'), picks.has(card.card_id) && tr('本轮选中')].filter(Boolean).join('·');
      xrayText(row, role ? `${card.card_id} · ${role}` : card.card_id, 'td');
      card.axes.forEach((term, index) => {
        xrayText(row, signed(term), 'td').title = current.guard
          ? tr`生效权重 ${block.weights[index]}；候选标准差 ${block.weight_sd[index]}`
          : tr`权重 ${block.weights[index]} ± ${block.weight_sd[index]}`;
      });
      xrayText(row, signed(card.pixel), 'td').title = card.pixel_terms
        .map((term, index) => `pc${index + 1} ${signed(term)}`).join(' · ');
      const total = xrayText(row, current.guard ? card.score.toFixed(2) : `${card.score.toFixed(2)} ± ${card.sd.toFixed(2)}`, 'td');
      if (current.guard) total.title = tr`候选标准差 ${card.sd.toFixed(2)}`;
      table.appendChild(row);
    });
    nodes.table.appendChild(table);
  }

  function buildXrayDetails(data) {
    state.xrayDetailsFor = data;
    const root = dom.xrayDetails;
    root.innerHTML = '';
    const guarded = data.states.some(current => current.guard);
    xrayText(root, guarded
      ? tr`模型 ${data.model_version || tr('未知')} · 收缩序贯选择；证据不足时使用等概率图片基线。`
      : tr`模型 ${data.model_version || tr('未知')} · 贝叶斯序贯选择：4 个画面原型轴 + 8 个像素主成分 + “停止选择”，拉普拉斯后验。`);
    const parameters = xrayText(root, '');
    const posterior = xrayText(root, '', 'p', 'xray-mono');
    if (guarded) xrayText(root, tr('生效贡献用于打分；不确定性来自未门控候选，回退不代表确定没有偏好。'), 'p', 'xray-mini');
    xrayText(root, tr`计算 Python 一次算完全部 ${data.states.length} 个状态；拖动与播放只在本地切换，不请求后端，也不调用模型。数值约定 ${data.numerical_contract || tr('未知')}（轨迹是显示投影，不入哈希）。`);
    const profile = data.profile;
    xrayText(root, profile
      ? tr`版本 画像 ${profile.profile_version} · 评分 ${profile.algorithm_version} · 偏好模型 ${profile.preference_version || tr('未知')} · 画像哈希 ${shortProfileHash(profile.profile_hash)}`
      : tr`版本 尚未冻结画像 · 轨迹 ${data.schema_version || tr('未知')} · 事件哈希 ${shortProfileHash(data.events_hash)}`, 'p', 'xray-mono');
    const metrics = data.metrics || {};
    xrayText(root, tr`体积 轨迹 JSON 约 ${formatXrayBytes(state.xrayTrajectorySize)} · 事件 ${data.event_count ?? tr('未知')} 条`
      + (metrics.raw_bytes !== undefined ? tr` · 原始记录 ${formatXrayBytes(metrics.raw_bytes)} · 模型上下文 ${formatXrayBytes(metrics.prompt_bytes)} · 输入 Token 估算 ${metrics.estimated_input_tokens}（估算不等于计费）` : ''));
    const calls = Array.isArray(data.calls) ? data.calls : [];
    const modelCalls = calls.reduce((sum, call) => sum + (call.provider_calls || 0), 0);
    xrayText(root, calls.length ? tr`模型调用 ${modelCalls} 次 · ${cacheSummary(calls)}`
      : tr('模型调用 0 次 · 尚无文案请求；轨迹计算不调用模型。'), 'p', 'xray-call-total');
    const breakdown = xrayDisclosure(root, tr('逐维贡献：为什么这些画面排在前面'));
    xrayText(breakdown, tr('效用 u = Σ 权重 × 特征值，12 项为 4 个画面原型轴与 8 个像素主成分（像素列为 8 项之和，悬停看明细）。')
      + (guarded ? tr('每行展示本轮生效贡献；候选标准差见悬停，不能当作回退分数的后验范围。')
        : tr('每行是本轮前 6 个猜测或本轮选中的画面；± 为后验标准差。数值由 Python 计算，页面只展示。')), 'p', 'xray-mini');
    const table = document.createElement('div');
    table.className = 'xray-contrib';
    breakdown.appendChild(table);
    breakdown.addEventListener('toggle', renderXrayContributions);
    const components = xrayDisclosure(root, tr('8 个像素主成分是什么'));
    (data.components || []).forEach(component => xrayText(components,
      tr`${component.name} · 解释 ${(Number(component.explained_variance_ratio) * 100).toFixed(1)}% · ${component.label}`, 'p', 'xray-mini'));
    const round = xrayJSON(root, tr('查看本轮状态 JSON'), () => data.states[Math.max(0, state.xrayRound)]);
    if (calls.length) xrayJSON(root, tr('查看模型调用与用量 JSON'), () => calls);
    xrayJSON(root, tr('查看版本与来源 JSON'), () => ({
      schema_version: data.schema_version, model_version: data.model_version, feature_version: data.feature_version,
      numerical_contract: data.numerical_contract, source: data.source, verified: data.verified,
      complete: data.complete, events_hash: data.events_hash, profile: data.profile || null }));
    state.xrayDetailNodes = { parameters, posterior, round, breakdown, table };
  }

  function cleanMarkdown(text) {
    if (!text) return '';
    return text.replace(/\*\*(.*?)\*\*/g, '$1').replace(/\*(.*?)\*/g, '$1').replace(/#/g, '');
  }

  function renderReading(reading) {
    if (!dom.readingText || !reading) return;
    if (LAST_CARD && state.analysisResult && state.analysisResult.persona) setRevealTitle(tr('你的命运角色'), tr('从你的选择，读一个属于你的故事'));
    else if (DECK) setRevealTitle(tr('你的选图回顾'), tr('回看吸引你的画面'));
    else setRevealTitle(tr('命运已经织成'), 'The threads have taken shape');

    const cleanText = cleanMarkdown(reading);
    // Build paragraph structure but start empty
    const paragraphs = cleanText.split('\n\n').filter(p => p.trim());
    dom.readingText.innerHTML = paragraphs.map(() => '<p></p>').join('');
    const paraEls = dom.readingText.querySelectorAll('p');
    dom.readingText.classList.add('visible');

    // Flatten all characters with paragraph index
    const chars = [];
    paragraphs.forEach((para, pIdx) => {
      [...para].forEach(ch => chars.push({ pIdx, ch }));
      if (pIdx < paragraphs.length - 1) chars.push({ pIdx, ch: null }); // paragraph end
    });

    // Adaptive speed: target ~3s for short texts, ~7s for long texts
    const totalChars = reading.replace(/\n+/g, '').length;
    const durationMs = Math.min(7000, Math.max(3000, totalChars * 12));
    const msPerChar = durationMs / Math.max(chars.length, 1);

    let charIdx = 0;
    let lastTime = null;
    let accumulated = 0;
    let done = false;

    function tick(now) {
      if (done) return;
      if (lastTime === null) lastTime = now;
      accumulated += now - lastTime;
      lastTime = now;

      while (accumulated >= msPerChar && charIdx < chars.length) {
        accumulated -= msPerChar;
        const { pIdx, ch } = chars[charIdx];
        if (ch !== null && paraEls[pIdx]) {
          paraEls[pIdx].textContent += ch;
        }
        charIdx++;
      }

      if (charIdx < chars.length) {
        requestAnimationFrame(tick);
      } else {
        done = true;
        scheduleDialoguePanel();
        scheduleRestartBlock();
        showFeedbackPanel();
      }
    }

    // Particles dissipate as text begins appearing
    releaseParticles(350);
    requestAnimationFrame(tick);
  }

  function resetReading() {
    if (!dom.readingText) return;
    if (LAST_CARD) setRevealTitle(tr('织机正在读你的底牌……'), tr('你的选择，会成为怎样的角色？'));
    else if (DECK) setRevealTitle(tr('正在整理你的选择……'), tr('图片、解读与猜牌回顾'));
    else setRevealTitle(tr('命运正在编织……'), 'The threads of fate are converging');
    dom.readingText.classList.remove('visible');
    dom.readingText.innerHTML = '';
    dom.analysisError.hidden = true;
    clearRestartTimer();
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

  function clearRestartTimer() {
    if (!state.restartRevealTimer) return;
    clearTimeout(state.restartRevealTimer);
    state.restartRevealTimer = null;
  }

  function scheduleRestartBlock() {
    if (!dom.restartBlock || state.restartRevealTimer) return;
    clearRestartTimer();
    const hasDialogue = !(state.analysisResult && state.analysisResult.config_missing);
    const delay = hasDialogue ? RESTART_REVEAL_DELAY_MS : FALLBACK_RESTART_REVEAL_DELAY_MS;
    state.restartRevealTimer = setTimeout(() => {
      state.restartRevealTimer = null;
      showRestartBlock();
    }, delay);
  }

  function resetDialoguePanel() {
    if (state.dialogueRevealTimer) {
      clearTimeout(state.dialogueRevealTimer);
      state.dialogueRevealTimer = null;
    }

    state.dialogueTurns = 0;
    state.dialogueLimit = getDialogueLimit(state.roundNumber);
    state.dialogueHistory = [];
    state.pendingDialoguePayload = null;
    state.dialogueLocked = false;
    state.dialogueBusy = false;
    state.analysisResult = null;
    dom.recordExport.hidden = true;
    dom.exportRecord.disabled = true;
    dom.profileVersion.textContent = '';
    dom.exportStatus.textContent = '';
    resetFeedbackPanel();

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
      appendDialogueBubble('system', tr`织机愿意回应${formatTurnLabel(state.dialogueLimit)}追问。`);
    }, DIALOGUE_REVEAL_DELAY_MS);
  }

  async function handleDialogueSubmit(event) {
    event.preventDefault();
    if (state.dialogueLocked || state.dialogueBusy) return;
    const configured = await ensureBackendConfig();
    if (!configured) {
      setDialogueStatus(tr('后端入口尚未配置。'));
      return;
    }

    const message = dom.dialogueInput.value.trim();
    if (!message) return;
    if (message.length > questionLimit()) {
      setDialogueStatus(tr`输入不能超过 ${questionLimit()} 字。`);
      return;
    }

    const turnCount = state.dialogueTurns + 1;
    const userBubble = appendDialogueBubble('user', message);
    dom.dialogueInput.value = '';
    syncDialogueCounter();
    setDialogueBusy(true);
    setDialogueStatus(tr('命运正在回声中……'));

    const sessionId = state.analysisResult && state.analysisResult.session_id;
    const pending = state.pendingDialoguePayload;
    const payload = pending && pending.session_id === sessionId && pending.user_message === message
      ? pending : Object.freeze({ user_message: message, session_id: sessionId,
        turn_id: createOperationId('turn') });
    // A response may be lost after the server commits. Retain this operation's
    // ID for its retry; a confirmed success clears it, including repeated text.
    state.pendingDialoguePayload = payload;

    try {
      const resp = await fetch(`${API_ENDPOINT}/dialogue`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: JSON.stringify(payload),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const error = new Error(data.error || `HTTP ${resp.status}`);
        error.confirmedTerminal = resp.status !== 429 && typeof data.status === 'string';
        throw error;
      }

      const reply = data.reply || data.text || tr('命运暂时没有更多话要说。');
      state.pendingDialoguePayload = null;
      appendDialogueBubble('assistant', reply, true);
      state.dialogueHistory.push({ role: 'user', content: message });
      state.dialogueHistory.push({ role: 'assistant', content: reply });
      state.dialogueTurns = Number.isInteger(data.turn_count) ? data.turn_count : turnCount;
      updateRuntimeNotice(data);
      if (data.max_turns) {
        state.dialogueLimit = resolveDialogueLimit(data.max_turns);
      }
      updateDialogueMeta();

      if (data.ended || state.dialogueTurns >= state.dialogueLimit) {
        lockDialogue(data.blocked ? (data.reply || data.text || '') : tr`${formatTurnLabel(state.dialogueLimit)}已满，织机不再继续回声。`);
      } else {
        setDialogueStatus(tr`还剩 ${state.dialogueLimit - state.dialogueTurns} 轮。`);
      }
    } catch (err) {
      console.error('[Norn] Dialogue error:', err);
      if (err.confirmedTerminal) state.pendingDialoguePayload = null;
      if (userBubble) userBubble.remove();
      dom.dialogueInput.value = message;
      syncDialogueCounter();
      setDialogueStatus(formatDialogueError(err));
    } finally {
      setDialogueBusy(false);
      if (state.xraySource === 'live') scheduleXrayRefresh();
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
    state.dialogueBusy = isBusy;
    dom.dialogueInput.disabled = isBusy || state.dialogueLocked;
    dom.dialogueSend.disabled = isBusy || state.dialogueLocked;
  }

  function appendDialogueBubble(role, text, typewrite = false) {
    if (!dom.dialogueLog) return;
    const bubble = document.createElement('div');
    bubble.className = `dialogue-bubble ${role}`;
    
    text = cleanMarkdown(text);

    if (role === 'assistant' && typewrite && text) {
      // Typewriter effect for assistant replies
      bubble.textContent = '';
      dom.dialogueLog.appendChild(bubble);
      dom.dialogueLog.scrollTop = dom.dialogueLog.scrollHeight;

      const chars = [...text];
      const msPerChar = Math.min(40, Math.max(16, 3000 / chars.length));
      let idx = 0;
      let last = null;
      let acc = 0;

      function tick(now) {
        if (last === null) last = now;
        acc += now - last;
        last = now;
        while (acc >= msPerChar && idx < chars.length) {
          acc -= msPerChar;
          bubble.textContent += chars[idx];
          idx++;
        }
        if (idx < chars.length) {
          requestAnimationFrame(tick);
        } else {
          dom.dialogueLog.scrollTop = dom.dialogueLog.scrollHeight;
        }
      }
      requestAnimationFrame(tick);
    } else {
      bubble.textContent = text;
      dom.dialogueLog.appendChild(bubble);
      dom.dialogueLog.scrollTop = dom.dialogueLog.scrollHeight;
    }
    return bubble;
  }

  function syncDialogueCounter() {
    const length = dom.dialogueInput.value.length;
    const limit = questionLimit();
    dom.dialogueInput.maxLength = limit;
    dom.dialogueCount.textContent = `${length}/${limit}`;
  }

  function questionLimit() {
    const reading = state.analysisResult;
    if (reading && Number.isInteger(reading.max_question_chars)) return reading.max_question_chars;
    return (reading ? reading.language : currentLanguage()) === 'en' ? 400 : 100;
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
      return tr('追问入口或会话不可用。请确认服务配置，必要时重新编织。');
    }
    if (message.includes('fetch') || message.includes('Network') || message.includes('Load failed')) {
      return tr('暂时无法连接本地服务。问题已保留，恢复服务后可再次追问。');
    }
    return message || tr('命运一时失语。');
  }

  function getDialogueLimit(totalRounds) {
    if (LAST_CARD) return 3;
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
    const labels = { 1: tr('一轮'), 2: tr('两轮'), 3: tr('三轮') };
    return labels[turns] || tr`${turns}轮`;
  }

  function getDialogueReadyText() {
    const variants = {
      1: tr('与织机进行对话，窥探命运之绳。'),
      2: tr('与织机低声交谈，听命运回响两次。'),
      3: tr('与织机继续对话，直到命运开始松动。'),
    };
    return variants[state.dialogueLimit] || tr('与织机进行对话，窥探命运之绳。');
  }

  function startAmbientAudio() {
    if (state.audioStarted || !dom.ambientAudio) return;
    applyMasterVolume();
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
  // Display reads card IDs and the shipped features only; legacy personality
  // labels never shape which cards appear.
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
