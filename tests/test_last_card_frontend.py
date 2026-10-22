"""Run the shipped Last Card lifecycle against deterministic browser boundaries.

These cases exercise real request bodies, readiness, replay-safe retries and
consent paths. Browser rendering checks complement them; no model is called.
"""

import shutil
import subprocess
import unittest

from test_frontend_events import APP
from test_frontend_i18n import LOCALE_FIXTURE


LAST_CARD_FIXTURE = LOCALE_FIXTURE[:LOCALE_FIXTURE.index("async function localeSelection")]
LAST_CARD_FIXTURE = LAST_CARD_FIXTURE.replace(
    "state, dom, startTest, drawRound,",
    "state, dom, init, initializeJev, syncEntryChoice, syncJevChoice, startJevPlay, "
    "beginFinale, renderFinaleCards, selectFinaleCard, flipFinale, retryFinale, "
    "continueFinaleReading, renderPacing, renderProductResult, renderFinaleXray, shareTextLines, "
    "downloadShareCard, inviteFriend, startTest, drawRound,",
).replace(
    "return { ...api, i18n:", "return { ...api, context, i18n:",
).replace("  if (pilot) {", "  if (true) {")

LAST_CARD_FIXTURE += r"""
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const product = { schema_version: 'norn.last-card.v1', pacing_policy: 'norn.pacing.v1' };
const preview = { preference: { effective: [1, 0, 0, 0, 0, 0, 0, 0, 0], status: 'population' },
  pacing: { schema_version: 'norn.pacing.v1', latest: { round: 0, action: 'continue' } } };

function game({ language = 'en', count = 80 } = {}) {
  const app = setup({ languages: [language === 'en' ? 'en-US' : 'zh-CN'],
    config: { deck: 'pamela-v1', lastCard: true, collect: true, consentVersion: 'norn.consent.v4' } });
  app.context.window.crypto = { randomUUID: () => 'fixture' };
  app.state.allCards = Array.from({ length: count }, (_, index) => ({
    id: `PAMELA_${String(index + 1).padStart(4, '0')}`, url: `/assets/${index + 1}.webp`, type: 'INFP', coords: {} }));
  app.state.deckLabel = 'pamela-v1@sha256:fixture';
  app.state.displayFeatures = new Map(app.state.allCards.map((card, i) => [card.id,
    [(count - i) / count, 0, 0, 0, 0, 0, 0, 0, 0]]));
  app.dom.ambientAudio.play = () => Promise.resolve();
  app.dom.finaleOverlay.hidden = true;
  app.dom.sharePanel.hidden = true;
  return app;
}

async function explore(app) {
  app.responses.push({ status: 200, data: preview });
  if (!app.state.entryLocked) app.startTest(false); else app.nextRound();
  app.paintRound(); await flush();
  assert.equal(app.dom.cardGrid.children.length, 4);
  app.dom.cardGrid.children[0].click();
}

function challenge(app, extra = {}) {
  return { schema_version: 'norn.finale.challenge.v1', challenge_id: 'finale-fixture',
    candidate_ids: app.state.allCards.filter(card => !app.state.usedCardIds.has(card.id)).slice(0, 4).map(card => card.id),
    commitment: 'sha256:sealed', events_hash: 'sha256:events', product_version: 'norn.last-card.v1', jev_status: 'off', ...extra };
}

function result(app, { hit = false, jev = { status: 'off' } } = {}) {
  const ids = app.state.finaleChallenge.candidate_ids;
  return { schema_version: 'norn.finale.result.v1', challenge_id: 'finale-fixture', candidate_ids: ids,
    chosen_card_id: app.state.finaleSelection, predicted_card_id: hit ? app.state.finaleSelection : ids.find(id => id !== app.state.finaleSelection),
    hit, commitment: 'sha256:sealed', salt: 'revealed-salt', events_hash: 'sha256:events',
    profile_hash: 'sha256:profile', layer: 'population', probabilities: ids.map(() => 0.25),
    baseline_probabilities: ids.map(() => 0.25), jev };
}

function settleFinale(app, failed = -1) {
  app.dom.finaleGrid.children.forEach((button, index) => {
    const img = button.children.find(child => child.tag === 'img');
    (index === failed ? img.onerror : img.onload)();
  });
}

async function completeProduct() {
  for (const language of ['en', 'zh']) {
    const app = game({ language });
    await explore(app);
    const terminal = challenge(app);
    app.setTime(1500);
    app.responses.push({ status: 200, data: terminal });
    await app.revealDestiny();
    assert.equal(app.state.sessionSealed, true);
    assert.equal(app.dom.finaleOverlay.hidden, false);
    assert.equal(app.requests.filter(row => row.url === '/api/analyze').length, 0,
      'Reading waits for a genuine terminal vote and an explicit continuation');
    const start = JSON.parse(app.requests.find(row => row.url === '/api/finale/start').body);
    assert.deepEqual(start.product, product);
    assert.equal(start.journal.events.at(-1).type, 'session_completed');
    assert.equal(start.consent, undefined, 'Private entry gets the same challenge without archival identity');
    assert(!start.journal.events.some(event => (event.card_ids || []).some(id => terminal.candidate_ids.includes(id))));
    app.selectFinaleCard(terminal.candidate_ids[0], 'touch');
    assert.equal(app.state.finaleSelection, null, 'Unloaded images cannot receive a vote');
    app.setTime(1800); settleFinale(app);
    app.dom.finaleGrid.children.forEach((el, i) => {
      el.offsetWidth = 150; el.offsetTop = i < 2 ? 0 : 230; el.offsetLeft = i % 2 * 160;
    });
    app.setTime(1900); app.setHidden(true);
    app.setTime(2250); app.setHidden(false);
    app.setTime(2500); app.selectFinaleCard(terminal.candidate_ids[0], 'touch');
    const receipt = result(app);
    app.responses.push({ status: 200, data: receipt });
    app.setTime(2800); await app.flipFinale();
    const choice = JSON.parse(app.requests.find(row => row.url === '/api/finale/choose').body);
    assert.equal(choice.elapsed_ms, 1300, 'Total terminal time begins when its overlay opens');
    assert.deepEqual(choice.observation, { ready_offset_ms: 300, hidden_ms: 350,
      layout: [[0, 0], [0, 1], [1, 0], [1, 1]], input: 'touch' });
    assert.equal(app.state.finaleResult.hit, false, 'A real miss remains a miss');
    assert.equal(app.dom.finaleContinue.hidden, false);
    assert(textOf(app.dom.xrayLastCard).includes(language === 'en' ? 'reference baseline' : '参考基线'));
    const persona = { schema_version: 'norn.persona.v1', title: language === 'en' ? 'The Quiet Navigator' : '静水领航人',
      tagline: language === 'en' ? 'You follow the small light beyond the crowded shore.' : '你循着微光，走向人群之外的岸。',
      card_id: choice.card_id, language, disclaimer: language === 'en' ? 'An entertainment reading.' : '根据本次选图创作的娱乐解读。' };
    app.responses.push({ status: 200, data: { ...successfulAnalysis, max_dialogue_turns: 3, reading: language === 'en' ? 'You chose a quiet shore.' : '你选了一处安静的岸。',
      persona, finale: receipt, language } });
    await app.continueFinaleReading();
    const analysis = JSON.parse(app.requests.find(row => row.url === '/api/analyze').body);
    assert.deepEqual(analysis.product, product);
    assert.equal(analysis.finale_id, 'finale-fixture');
    assert.deepEqual(analysis.journal, start.journal, 'The terminal choice stays out of the exploration journal');
    assert.equal(app.dom.sharePanel.hidden, false);
    assert(textOf(app.dom.portrait).includes(persona.title));
    assert(textOf(app.dom.portrait).includes(persona.disclaimer));
    assert.equal(app.dom.portrait.children.find(child => child.tag === 'img').src,
      app.state.allCards.find(card => card.id === choice.card_id).url);
    assert.equal(app.dom.dialogueTurns.textContent, '0/3', 'A one-round game keeps all three follow-up turns');
    for (let turn = 1; turn <= 3; turn++) {
      app.dom.dialogueInput.value = language === 'en' ? `Tell me more ${turn}` : `再说一点 ${turn}`;
      app.responses.push({ status: 200, data: { reply: 'A grounded reply.', turn_count: turn, max_turns: 3, ended: turn === 3 } });
      await app.handleDialogueSubmit({ preventDefault() {} });
      assert.equal(app.dom.dialogueTurns.textContent, `${turn}/3`);
      assert.equal(app.state.dialogueLocked, turn === 3);
    }
    const countBeforeLocked = app.requests.length;
    app.dom.dialogueInput.value = 'A fourth question';
    await app.handleDialogueSubmit({ preventDefault() {} });
    assert.equal(app.requests.length, countBeforeLocked, 'The fourth turn is visibly locked without a request');
    assert.equal(app.requests.filter(row => row.url.includes('/record/partial')).length, 0,
      'Private exploration and terminal stages never send a contribution');
  }
}

async function retryWithoutChangingTheBet() {
  const app = game(); await explore(app);
  app.responses.push({ status: 503, data: { error: 'temporary' } });
  await app.revealDestiny();
  assert.equal(app.state.finaleError, 'start');
  app.setTime(1600);
  app.responses.push({ status: 200, data: challenge(app) });
  await app.beginFinale();
  const starts = app.requests.filter(row => row.url === '/api/finale/start');
  assert.equal(starts.length, 2); assert.equal(starts[0].body, starts[1].body);
  settleFinale(app, 2);
  app.selectFinaleCard(app.state.finaleChallenge.candidate_ids[0]);
  assert.equal(app.state.finaleSelection, null);
  const count = app.requests.length;
  app.retryFinale(); settleFinale(app);
  assert.equal(app.requests.length, count, 'Image recovery cannot make a new commitment');
  const ids = app.state.finaleChallenge.candidate_ids;
  app.selectFinaleCard(ids[0], 'keyboard');
  app.responses.push({ status: 503, data: { error: 'lost response' } });
  await app.flipFinale();
  assert.equal(app.state.finaleError, 'choose');
  app.setTime(9000); app.selectFinaleCard(ids[1], 'mouse');
  assert.equal(app.state.finaleSelection, ids[0], 'A dispatched choice cannot be revised while its receipt is unknown');
  app.responses.push({ status: 200, data: result(app, { hit: true }) });
  await app.flipFinale();
  const choices = app.requests.filter(row => row.url === '/api/finale/choose');
  assert.equal(choices[0].body, choices[1].body, 'Elapsed, observation and card all remain identical on retry');
  assert.equal(JSON.parse(choices[1].body).observation.input, 'keyboard');
  assert.equal(app.state.finaleResult.hit, true);
}

async function jevOnlyAtTheFinale() {
  const app = game();
  app.responses.push({ status: 200, data: { available: true, provider_calls: 0 } });
  await app.initializeJev();
  assert.equal(app.state.jevEnabled, true);
  assert.equal(app.dom.jevEntryOptIn.checked, true);
  app.responses.push({ status: 200, data: { status: 'ready', jev_session: 'ticket' } });
  await explore(app);
  await flush();
  assert.equal(JSON.parse(app.requests.find(row => row.url === '/api/jev/start').body).purpose, 'finale');
  assert.equal(app.dom.jevEntryOptIn.disabled, true);
  await explore(app);
  assert.equal(app.requests.filter(row => row.url === '/api/jev/predict').length, 0);
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  app.responses.push({ status: 200, data: challenge(app, { jev_status: 'pending' }) },
    { status: 200, data: { status: 'ready', provider_calls: 1 }, wait });
  app.setTime(2000);
  const opening = app.revealDestiny(); await flush();
  app.setTime(2300); settleFinale(app);
  const id = app.state.finaleChallenge.candidate_ids[0];
  app.selectFinaleCard(id);
  assert.equal(app.state.finaleSelection, null, 'Loaded cards wait for the sealed Jev attempt');
  assert.equal(app.state.finaleReadyAt, null);
  app.setTime(6000); release(); await opening;
  assert.equal(app.state.finaleReadyAt, 6000);
  app.selectFinaleCard(id, 'mouse');
  const receipt = result(app, { jev: { status: 'ok', model: 'jev', predicted_card_id: id, hit: true, provider_calls: 1 } });
  app.responses.push({ status: 200, data: receipt });
  app.setTime(6500); await app.flipFinale();
  assert.equal(app.state.finaleResult.hit, false); assert.equal(app.state.finaleResult.jev.hit, true);
  assert.equal(app.requests.filter(row => row.url === '/api/jev/finale').length, 1);
  assert.equal(JSON.parse(app.requests.find(row => row.url === '/api/finale/choose').body).observation.ready_offset_ms, 4000);
  assert(textOf(app.dom.finaleStatus).includes('Jev also guessed'));
}

async function pacingAndTwelveRoundBound() {
  const app = game();
  for (let round = 1; round <= 12; round++) {
    await explore(app);
    assert.equal(app.state.roundNumber, round);
    assert.equal(app.state.sessionSealed, false, 'Neither round five nor a pacing hint automatically ends the game');
    app.renderPacing({ latest: { round, action: 'offer_finale' } });
    assert(app.dom.gameProgress.textContent.includes('your choice'));
  }
  app.drawRound(); assert.equal(app.state.roundNumber, 12, 'The UX cap cannot produce a thirteenth exploration round');
  app.responses.push({ status: 200, data: challenge(app) });
  app.nextRound(); await flush();
  assert.equal(app.state.sessionSealed, true);
  assert.equal(app.requests.filter(row => row.url === '/api/finale/start').length, 1);
  assert(app.dom.roundStatus.textContent.includes('not a learning threshold'));
}

async function voluntaryEntryAndSealedCheckpoint() {
  for (const consent of [false, true]) {
    const app = game();
    const deck = { schema_version: 'norn.research-deck.browser.v1', deck: 'pamela-v1',
      deck_version: 'sha256:fixture', credit: 'Fixture', cards: app.state.allCards.map(card => ({
        ...card, x2: app.state.displayFeatures.get(card.id) })) };
    app.responses.push({ status: 200, data: deck }, { status: 200, data: { available: false } });
    await app.init(); await flush();
    assert.equal(app.dom.collectOptIn.checked === true, false);
    assert.equal(app.dom.startPrivate.hidden, true);
    app.dom.collectOptIn.checked = consent;
    // Private and consented use the same game entrance. Partial responses are
    // queued before preview because its snapshot enters the microtask first.
    if (consent) app.responses.push({ status: 200, data: {} });
    app.responses.push({ status: 200, data: preview });
    app.dom.startBtn.click(); app.paintRound({ images: 'none' });
    app.runFrames(() => app.state.roundOpen);
    app.dom.cardGrid.children.forEach((_, i) => app.settleImage(i)); await flush();
    assert.equal(app.state.consent, consent);
    app.dom.cardGrid.children[0].click();
    if (consent) app.responses.push({ status: 200, data: {} }, { status: 200, data: {} });
    app.responses.push({ status: 200, data: challenge(app) });
    await app.revealDestiny(); await flush();
    const checkpoints = app.requests.filter(row => row.url === '/api/record/partial').map(row => JSON.parse(row.body));
    assert.equal(Boolean(checkpoints.length), consent);
    if (consent) {
      const last = checkpoints.at(-1);
      assert.deepEqual(last.product, product);
      assert.equal(last.journal.events.at(-1).type, 'session_completed');
      assert(last.journal.events.some(event => event.type === 'round_completed'));
      assert.equal(last.open_round, undefined, 'A sealed checkpoint closes the actual last exploration round');
      assert.equal(last.session_key, JSON.parse(app.requests.find(row => row.url === '/api/finale/start').body).session_key);
    }
  }
}

async function localShareCardAndBoundedCopy() {
  const app = game({ language: 'zh' });
  const lines = app.shareTextLines({ measureText: value => ({ width: value.length * 10 }) }, 'Supercalifragilisticexpialidocious '.repeat(8), 80, 3);
  assert.equal(lines.length, 3); assert(lines.at(-1).endsWith('…'));
  assert.doesNotThrow(() => app.shareTextLines({ measureText: () => ({ width: 10 }) }, '字', 1, 1));
  const drawn = [], imageDraws = [];
  const create = app.domDocument.createElement.bind(app.domDocument);
  app.domDocument.createElement = tag => {
    const el = create(tag);
    if (tag === 'canvas') {
      const ctx = new Proxy({ measureText: value => ({ width: Array.from(value).length * 20 }),
        fillText: value => drawn.push(value), drawImage: (...args) => imageDraws.push(args) },
        { get: (target, key) => key in target ? target[key] : () => {} });
      el.getContext = () => ctx;
      el.toBlob = (callback, type) => callback(new Blob(['png-fixture'], { type }));
    }
    return el;
  };
  app.context.Image = class {
    constructor() { this.naturalWidth = 800; this.naturalHeight = 1200; }
    set src(value) { this.source = value; Promise.resolve().then(() => this.onload()); }
  };
  app.state.analysisResult = { persona: { title: 'The Quiet Navigator', tagline: 'A long English sentence '.repeat(30),
    card_id: app.state.allCards[3].id, language: 'en', disclaimer: 'An entertainment reading.' },
    finale: { hit: false, jev: { status: 'ok', hit: true } } };
  await app.downloadShareCard();
  assert.equal(app.downloads.length, 1);
  assert.equal(app.downloads[0].filename, 'norn-last-card-en.png', 'Share language follows frozen persona, not current UI');
  assert.equal(app.blobs[0].type, 'image/png');
  assert(drawn.includes('I surprised the loom this time.')); assert(drawn.includes('Jev: a hit'));
  assert(!drawn.some(line => /visitor|profile_hash|session_key/.test(line)));
  assert.equal(imageDraws[0][0].source, app.state.allCards[3].url);
  assert.equal(app.requests.length, 0, 'Sharing renders locally without an upload');
  let clipboard;
  app.context.window.navigator.clipboard = { writeText: async value => { clipboard = value; } };
  await app.inviteFriend();
  assert.equal(clipboard, 'https://norn-machine.dubianche123.workers.dev/');
}

async function finaleProbabilityComparison() {
  for (const language of ['zh', 'en']) {
    const app = game({ language });
    const ids = app.state.allCards.slice(0, 4).map(card => card.id);
    const receipt = { candidate_ids: ids, chosen_card_id: ids[1], predicted_card_id: ids[0], hit: false,
      layer: 'population', probabilities: [.4, .3, .2, .1], baseline_probabilities: [.4, .3, .2, .1],
      jev: { status: 'ok', predicted_card_id: ids[1], hit: true, probabilities: [.1, .6, .2, .1] } };
    app.renderFinaleXray(receipt);
    const nodes = () => descendants(app.dom.xrayLastCard);
    const rows = () => nodes().filter(el => el.classList.contains('xray-finale-probability'));
    const tiles = nodes().filter(el => el.classList.contains('xray-finale-candidate'));
    assert.equal(tiles.length, 4, 'The finale compares exactly the same four unseen candidates');
    assert.equal(tiles.filter(el => el.classList.contains('favourite')).length, 1);
    assert.equal(tiles[1].dataset.cardId, ids[1]);
    assert.equal(rows().length, 8, 'Identical learning/baseline predictions share four bars, plus four independent Jev bars');
    assert.equal(rows().filter(el => el.dataset.lane === 'baseline').length, 0);
    assert(textOf(app.dom.xrayLastCard).includes(language === 'en' ? 'Learning model = reference baseline' : '学习模型 = 参考基线'));
    assert(textOf(app.dom.xrayLastCard).includes(language === 'en' ? 'fell back' : '回退'));
    const learning = rows().filter(el => el.dataset.lane === 'learning');
    const jev = rows().filter(el => el.dataset.lane === 'jev');
    assert.deepEqual(learning.map(el => el.children.at(-1).textContent), ['40%', '30%', '20%', '10%']);
    assert.deepEqual(jev.map(el => el.children.at(-1).textContent), ['10%', '60%', '20%', '10%']);
    assert.equal(jev[1].children[1].children[0].style.width, '60%');
    assert.equal(jev[1].children[1].attributes['aria-valuenow'], '60');
    assert(textOf(tiles[1]).includes(language === 'en' ? 'Jev’s guess' : 'Jev 押注'));
    assert(textOf(tiles[0]).includes(language === 'en' ? 'Loom’s guess' : '织机押注'));
    assert.equal(app.requests.length, 0, 'Replaying distributions does not invoke a provider or rebuild a forecast');
    if (language === 'en') assert(!/[\u4e00-\u9fff]/.test(textOf(app.dom.xrayLastCard)));

    app.renderFinaleXray({ ...receipt, layer: 'personal', baseline_probabilities: [.1, .2, .3, .4] });
    assert.equal(rows().length, 12, 'Three different distributions get three honest lanes');
    assert.equal(rows().filter(el => el.dataset.lane === 'baseline').length, 4);
    assert(!textOf(app.dom.xrayLastCard).includes(language === 'en' ? 'fell back' : '回退'));
    for (const status of ['late', 'pending', 'failed', 'off']) {
      app.renderFinaleXray({ ...receipt, jev: { ...receipt.jev, status } });
      assert.equal(rows().length, 4, 'Unavailable Jev has no invented probability bars');
      assert.equal(rows().filter(el => el.dataset.lane === 'jev').length, 0);
      const legend = nodes().find(el => el.classList.contains('xray-finale-legend'));
      assert(!textOf(legend).includes('Jev'), 'A late or absent answer earns neither a hit nor a miss');
      if (status !== 'off') assert(textOf(app.dom.xrayLastCard).includes(language === 'en' ? 'unscored, not a miss' : '本次不计分'));
    }
  }
}

const cases = { completeProduct, retryWithoutChangingTheBet, jevOnlyAtTheFinale,
  pacingAndTwelveRoundBound, voluntaryEntryAndSealedCheckpoint, localShareCardAndBoundedCopy, finaleProbabilityComparison };
(async () => { await cases[process.argv[2]](); })().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required for shipped JavaScript checks")
class LastCardFrontendTests(unittest.TestCase):
    def test_product_lifecycle(self):
        for scenario in ["completeProduct", "retryWithoutChangingTheBet", "jevOnlyAtTheFinale",
                         "pacingAndTwelveRoundBound", "voluntaryEntryAndSealedCheckpoint", "localShareCardAndBoundedCopy",
                         "finaleProbabilityComparison"]:
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", LAST_CARD_FIXTURE, str(APP), scenario],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
