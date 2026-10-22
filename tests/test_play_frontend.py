"""Exercise real opt-in, precommit and product-result browser boundaries."""

import shutil
import subprocess
import unittest

from test_frontend_events import APP, NODE_FIXTURE


PLAY_FIXTURE = NODE_FIXTURE[:NODE_FIXTURE.index("const cases = {")]
PLAY_FIXTURE = PLAY_FIXTURE.replace(
    "state, dom, startTest, drawRound,",
    "state, dom, init, initializeJev, syncJevChoice, startJevPlay, precommitPlayRound, "
    "settlePlayRound, renderPlayFeedback, renderPortrait, appendEvent, callAnalyzeAPI, "
    "syncFeedbackChoice, snapshotExperience, recordRoundFeedback, closeCurrentRound, startTest, drawRound,",
).replace("  if (pilot) {", "  if (true) {").replace(
    "runFrames, observers, documentListeners, windowListeners, timers,",
    "runFrames, observers, documentListeners, windowListeners, timers, domDocument: document, "
    "paintFeedback: () => { const frame = frames.shift(); if (frame) frame(now); }, "
    "frameCount: () => frames.length, setWidth: width => { context.window.innerWidth = width; },",
)

PLAY_FIXTURE += r"""
const textOf = element => [element.textContent, ...element.children.map(textOf)].join(' ');
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const deckResponse = app => ({ schema_version: 'norn.research-deck.browser.v1', deck: 'pamela-v1',
  deck_version: 'sha256:test', credit: 'Fixture', cards: app.state.allCards.map((card, i) => ({
    id: card.id, url: card.url, x2: [4 - i, 0, 0, 0, 0, 0, 0, 0, 0] })) });
const preview = weights => ({ preference: { effective: weights || [1, 0, 0, 0, 0, 0, 0, 0, 0], status: 'population' } });
function playSetup({ consent = false, roundFeedback = false } = {}) {
  const app = setup({ config: { deck: 'pamela-v1', collect: true, consentVersion: 'norn.consent.v4' } });
  app.state.deckLabel = 'pamela-v1@sha256:test';
  app.state.playMode = true;
  app.state.consent = consent;
  app.state.roundFeedbackEnabled = roundFeedback;
  app.state.experienceStartedAt = app.time();
  app.state.experience = { schema_version: 'norn.experience.v1',
    round_feedback: roundFeedback ? 'on' : 'off', exposures: [] };
  app.state.displayFeatures = new Map(deckResponse(app).cards.map(card => [card.id, card.x2]));
  return app;
}
const start = app => app.appendEvent('round_started', {
  round: 1, card_ids: Object.freeze(app.state.allCards.map(card => card.id)) });
const choose = (app, index) => app.appendEvent('card_selected', {
  round: 1, card_id: app.state.allCards[index].id, t_ms: 100 });
const withdraw = (app, index) => app.appendEvent('card_deselected', {
  round: 1, card_id: app.state.allCards[index].id, t_ms: 150 });
const complete = app => app.appendEvent('round_completed', {
  round: 1, seen_card_ids: app.state.allCards.map(card => card.id), duration_ms: 200 });

async function bothEntrancesFreezeOptIn() {
  for (const entrance of ['startBtn', 'startPrivate']) {
    for (const [enabled, feedbackEnabled] of [[false, false], [false, true], [true, false], [true, true]]) {
      const app = setup({ config: { deck: 'pamela-v1', collect: true, consentVersion: 'norn.consent.v4' } });
      // init's asset fetch starts synchronously; its read-only status fetch
      // resumes after ensureBackendConfig's resolved promise.
      app.responses.push({ status: 200, data: deckResponse(app) },
        { status: 200, data: { available: true, provider_calls: 0 } });
      await app.init(); await flush();
      app.dom.ambientAudio.play = () => Promise.resolve();
      // The DOM fixture does not parse initial HTML attributes; the static
      // contract independently checks this native <details open> state.
      app.dom.xraySessionOptions.open = true;
      assert.equal(app.state.jevEnabled, false, 'Default entry never selects a paid model');
      assert.equal(app.state.roundFeedbackEnabled, false, 'Feedback is off for both entrances by default');
      assert.equal(app.dom.roundFeedbackOptIn.checked, false);
      assert.equal(app.dom.roundFeedbackOptIn.disabled, false);
      assert.equal(app.dom.jevOptIn.disabled, false);
      assert.equal(app.requests.filter(item => item.url.includes('/jev/')).length, 1);
      assert.equal(app.requests.find(item => item.url === '/api/jev/status').method, undefined);
      app.dom.jevOptIn.checked = enabled;
      app.dom.jevOptIn.listeners.change();
      assert.equal(app.state.jevEnabled, enabled);
      app.dom.roundFeedbackOptIn.checked = feedbackEnabled;
      app.dom.roundFeedbackOptIn.listeners.change();
      if (enabled) app.responses.push({ status: 200, data: { status: 'ready', jev_session: 'fixture-token' } });
      app.dom[entrance].click();
      app.dom[entrance].click();
      app.dom[entrance === 'startBtn' ? 'startPrivate' : 'startBtn'].click();
      await flush();
      assert.equal(app.state.consent, entrance === 'startBtn', 'Jev opt-in preserves the chosen collection entrance');
      assert.equal(app.state.entryLocked, true);
      assert.equal(app.dom.xraySessionOptions.open, false, 'Either entrance collapses its frozen pre-entry options');
      assert.equal(app.dom.jevOptIn.disabled, true);
      assert.equal(app.dom.roundFeedbackOptIn.disabled, true);
      assert.equal(app.state.roundFeedbackEnabled, feedbackEnabled);
      assert.deepEqual(normalize(app.snapshotExperience()), { schema_version: 'norn.experience.v1',
        round_feedback: feedbackEnabled ? 'on' : 'off', exposures: [] });
      assert.equal(app.requests.filter(item => item.url === '/api/jev/start').length, Number(enabled),
        'Double clicks and switching entrance cannot restart the sidecar');
      app.dom.jevOptIn.checked = !enabled;
      app.dom.jevOptIn.listeners.change();
      assert.equal(app.state.jevEnabled, enabled);
      assert.equal(app.dom.jevOptIn.checked, enabled, 'Both entrances freeze the checkbox at first start');
      app.dom.roundFeedbackOptIn.checked = !feedbackEnabled;
      app.dom.roundFeedbackOptIn.listeners.change();
      assert.equal(app.state.roundFeedbackEnabled, feedbackEnabled);
      assert.equal(app.dom.roundFeedbackOptIn.checked, feedbackEnabled, 'Both entrances freeze feedback at first start');
      assert.equal(app.requests.filter(item => item.url === '/api/jev/predict').length, 0,
        'No deal has reached its render callback yet');
    }
  }
}

async function unavailableAndDefaultMakeNoProviderCalls() {
  const disabled = playSetup();
  disabled.responses.push({ status: 200, data: { available: false, provider_calls: 0 } });
  await disabled.initializeJev();
  assert.equal(disabled.dom.jevOptIn.disabled, true);
  const app = playSetup();
  app.responses.push({ status: 200, data: { available: true, provider_calls: 0 } });
  await app.initializeJev();
  app.responses.push({ status: 200, data: preview() });
  start(app); await flush();
  choose(app, 0); complete(app);
  assert.equal(app.requests.filter(item => /\/jev\/(start|predict)$/.test(item.url)).length, 0);
  assert.equal(app.state.playResults.get(1).scored, true);
  assert.equal(app.state.playResults.get(1).hit, true);
  assert.equal(app.dom.playFeedback.hidden, true, 'Private play also defaults to feedback off');
  assert.equal(app.snapshotExperience().exposures.length, 0);
}

async function precommitClonesBeforeAnyChoice() {
  const app = playSetup();
  app.state.jevEnabled = true;
  app.state.jevStart = Promise.resolve('fixture-token');
  let finishPreview;
  app.responses.push({ status: 200, data: preview(), wait: new Promise(resolve => { finishPreview = resolve; }) },
    { status: 200, data: { status: 'ok', round: 1, probabilities: [0.7, 0.1, 0.1, 0.1] } });
  start(app);
  choose(app, 0);
  complete(app);
  await flush();
  assert(app.requests.some(item => item.url === '/api/preview'));
  assert(app.requests.some(item => item.url === '/api/jev/predict'));
  for (const request of app.requests) {
    const body = JSON.parse(request.body);
    assert.deepEqual(body.journal.events.map(event => event.type), ['round_started'], request.url);
    assert.equal(body.journal.events.at(-1).round, 1);
    assert.equal(body.visitor, undefined); assert.equal(body.consent, undefined);
  }
  assert.equal(app.state.jevPredictions.get(1).timely, false);
  assert.equal(app.state.playResults.get(1).scored, false);
  finishPreview(); await flush();
  assert.equal(app.state.playForecasts.get(1).timely, false);
  assert.equal(app.state.playResults.get(1).scored, false, 'Late forecasts cannot retroactively gain credit');
}

async function tiesLateAndFinalRetainedTarget() {
  const tied = playSetup();
  tied.responses.push({ status: 200, data: preview(Array(9).fill(0)) });
  start(tied); await flush(); choose(tied, 0); complete(tied);
  assert.equal(tied.state.playForecasts.get(1).top, null);
  assert.equal(tied.state.playResults.get(1).scored, false, 'Tied ranks are not a committed single-card guess');
  const undone = playSetup();
  undone.responses.push({ status: 200, data: preview() });
  start(undone); await flush();
  choose(undone, 0); choose(undone, 1); withdraw(undone, 0); complete(undone);
  const result = undone.state.playResults.get(1);
  assert.equal(result.target, undone.state.allCards[1].id, 'The withdrawn first click is not the target');
  assert.equal(result.scored, true); assert.equal(result.hit, false);
  assert(!textOf(undone.dom.playFeedback).includes('✦'));
  const empty = playSetup();
  empty.responses.push({ status: 200, data: preview() });
  start(empty); await flush(); choose(empty, 0); withdraw(empty, 0); complete(empty);
  assert.equal(empty.state.playResults.get(1).target, null);
  assert.equal(empty.state.playResults.get(1).scored, false);
  assert(!textOf(empty.dom.playFeedback).includes('已猜中'));
}

async function collectedResultsStayHiddenUntilPortrait() {
  const app = playSetup({ consent: true });
  app.responses.push({ status: 200, data: preview() });
  start(app); await flush(); choose(app, 0); complete(app);
  assert.equal(app.state.playResults.get(1).hit, true);
  assert.equal(app.dom.playFeedback.hidden, true, 'Research collection receives no intervening correctness feedback');
  app.state.sessionSealed = true;
  app.state.xrayTrajectory = null;
  const portrait = { schema_version: 'norn.product-portrait.v1', title: 'Your visual thread',
    subtitle: 'A thread to explore next', card_ids: [app.state.allCards[0].id], motifs: ['Quiet detail'] };
  app.responses.push({ status: 200, data: { ...successfulAnalysis, portrait } });
  await app.callAnalyzeAPI({ journal: app.state.eventJournal, language: 'en' });
  assert.equal(app.state.xrayTrajectory, null);
  assert.equal(app.dom.portrait.hidden, false);
  const content = textOf(app.dom.portrait);
  assert(content.includes(portrait.title) && content.includes(portrait.subtitle));
  assert(content.includes('1 / 1'), 'A real analysis response presents the stored precommit score without opening X-Ray');
  const gallery = app.dom.portrait.children.find(child => child.classList.contains('portrait-gallery'));
  assert.equal(gallery.children[0].src, app.state.allCards[0].url);
  assert.equal(app.requests.filter(item => item.url === '/api/trajectory').length, 0);
}

function paintFeedbackFrames(app) {
  // Direct helper cases have no animation loop; exhaust only their queued paints.
  for (let n = 0; n < 10 && app.frameCount(); n++) app.paintFeedback();
}

async function feedbackExposureRequiresVisiblePaint() {
  const app = playSetup({ consent: true, roundFeedback: true });
  app.responses.push({ status: 200, data: preview() });
  start(app); await flush(); choose(app, 0); complete(app);
  assert.equal(app.state.consent, true, 'Turning feedback on does not opt out of collection');
  assert.equal(app.dom.playFeedback.hidden, false);
  assert.equal(app.snapshotExperience().exposures.length, 0, 'Updating the DOM is not yet a painted exposure');
  app.domDocument.visibilityState = 'hidden';
  app.setTime(1375); paintFeedbackFrames(app);
  assert.equal(app.snapshotExperience().exposures.length, 0, 'A background page has no measured exposure');
  app.domDocument.visibilityState = 'visible';
  app.renderPlayFeedback(); app.renderPlayFeedback();
  app.setTime(1600); paintFeedbackFrames(app);
  assert.deepEqual(normalize(app.snapshotExperience()), { schema_version: 'norn.experience.v1',
    round_feedback: 'on', exposures: [{ after_round: 1, feedback: 'hit', elapsed_ms: 600 }] });
  assert.equal(app.timers.filter(timer => timer.delay === 500).length, 1,
    'A new exposure schedules the normal coalesced checkpoint');
  app.renderPlayFeedback(); paintFeedbackFrames(app);
  assert.equal(app.snapshotExperience().exposures.length, 1, 'Repaint and locale changes do not double-count a round');
  const detached = app.snapshotExperience();
  detached.round_feedback = 'off'; detached.exposures[0].feedback = 'miss'; detached.exposures.push({});
  assert.equal(app.snapshotExperience().round_feedback, 'on');
  assert.equal(app.snapshotExperience().exposures[0].feedback, 'hit');
  assert.equal(app.snapshotExperience().exposures.length, 1, 'Snapshots cannot rewrite live experience history');

  for (const boundary of ['sealed', 'covered', 'outside']) {
    const hidden = playSetup({ consent: true, roundFeedback: true });
    hidden.responses.push({ status: 200, data: preview() });
    start(hidden); await flush(); choose(hidden, 0); complete(hidden);
    if (boundary === 'sealed') hidden.state.sessionSealed = true;
    if (boundary === 'covered') {
      hidden.state.xrayOpen = true;
      hidden.setWidth(800);
    }
    if (boundary === 'outside') hidden.dom.playFeedback.getBoundingClientRect = () => ({
      width: 100, height: 20, top: 900, bottom: 920 });
    paintFeedbackFrames(hidden);
    assert.equal(hidden.snapshotExperience().exposures.length, 0, boundary);
  }
  const off = playSetup({ consent: true });
  off.responses.push({ status: 200, data: preview() });
  start(off); await flush(); choose(off, 0); complete(off); paintFeedbackFrames(off);
  assert.equal(off.dom.playFeedback.hidden, true);
  assert.deepEqual(normalize(off.snapshotExperience()), { schema_version: 'norn.experience.v1',
    round_feedback: 'off', exposures: [] });
}

async function exposureKindsAndMonotoneClock() {
  const app = playSetup({ roundFeedback: true });
  for (const [round, target, scored, hit, feedback] of [
    [1, 'a', true, false, 'miss'], [2, 'a', false, false, 'unscored'], [3, null, false, false, 'blank'],
  ]) {
    app.state.playResults.set(round, { round, target, scored, hit });
    app.setTime(round === 3 ? 90000000 : 1000 + round * 100);
    app.renderPlayFeedback(); paintFeedbackFrames(app);
    assert.equal(app.snapshotExperience().exposures.at(-1).feedback, feedback);
  }
  const exposures = app.snapshotExperience().exposures;
  assert.deepEqual(exposures.map(row => row.after_round).join(','), '1,2,3');
  assert.equal(exposures.at(-1).elapsed_ms, 86400000, 'Elapsed time is bounded at one day');
  app.recordRoundFeedback({ round: 1, target: 'a', scored: true, hit: true });
  paintFeedbackFrames(app);
  assert.equal(app.snapshotExperience().exposures.length, 3, 'An old round cannot be appended after newer exposure');
}

async function checkpointAndRevealCarryExperience() {
  const app = playSetup({ consent: true, roundFeedback: true });
  app.responses.push({ status: 200, data: preview() }, { status: 200, data: { stored: true } });
  app.drawRound(); app.paintRound(); await flush();
  const factsBefore = JSON.stringify(app.state.eventJournal);
  // Use a completed round so both storage and the future reveal carry a real
  // chronological record. Feedback is recorded only after the visible paint.
  app.dom.cardGrid.children[0].click();
  app.closeCurrentRound();
  app.setTime(1500); paintFeedbackFrames(app);
  const experience = normalize(app.snapshotExperience());
  assert.equal(experience.round_feedback, 'on');
  assert.equal(experience.exposures.length, 1);
  const journalAfterExposure = JSON.stringify(app.state.eventJournal);
  app.renderPlayFeedback(); paintFeedbackFrames(app);
  assert.equal(JSON.stringify(app.state.eventJournal), journalAfterExposure, 'The sidecar never injects model events');
  assert.notEqual(journalAfterExposure, factsBefore, 'Only real choices and a round close changed the journal');
  app.responses.push({ status: 200, data: { stored: true } });
  app.savePartialVisit(); await flush();
  const partial = app.requests.filter(request => request.url === '/api/record/partial').at(-1);
  assert.deepEqual(JSON.parse(partial.body).experience, experience);

  app.responses.push({ status: 200, data: preview() }, { status: 200, data: { stored: true } });
  app.drawRound(); app.paintRound(); await flush();
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny(); await flush();
  const analysis = app.requests.find(request => request.url === '/api/analyze');
  assert(analysis, 'The normal reveal posts its complete journal');
  assert.deepEqual(JSON.parse(analysis.body).experience, experience);
  assert.equal(app.state.sessionSealed, true);

  const withdrawn = playSetup({ consent: true, roundFeedback: true });
  withdrawn.responses.push({ status: 200, data: preview() });
  start(withdrawn); await flush(); choose(withdrawn, 0); complete(withdrawn);
  paintFeedbackFrames(withdrawn);
  const historical = normalize(withdrawn.snapshotExperience());
  withdrawn.responses.push({ status: 200, data: { withdrawn: true } });
  // A real partial save establishes the archival identity; withdrawal then
  // invalidates queued transmission without changing the frozen exposure mode.
  withdrawn.savePartialVisit();
  await withdrawn.forgetVisitor(); await flush();
  const count = withdrawn.requests.filter(request => request.url === '/api/record/partial').length;
  withdrawn.savePartialVisit(); await flush();
  assert.equal(withdrawn.requests.filter(request => request.url === '/api/record/partial').length, count);
  assert.equal(withdrawn.state.consent, false);
  assert.deepEqual(normalize(withdrawn.snapshotExperience()), historical);
  assert.equal(withdrawn.state.roundFeedbackEnabled, true);
}

async function privateAndWithdrawnRevealKeepExperienceLocal() {
  for (const consent of [false, true]) {
    const app = playSetup({ consent, roundFeedback: true });
    app.responses.push({ status: 200, data: preview() });
    if (consent) app.responses.push({ status: 200, data: { stored: true } });
    app.drawRound(); app.paintRound(); await flush();
    app.dom.cardGrid.children[0].click(); app.closeCurrentRound(); paintFeedbackFrames(app);
    assert.equal(app.snapshotExperience().exposures.length, 1);
    if (consent) {
      app.responses.push({ status: 200, data: { withdrawn: true } });
      await app.forgetVisitor(); await flush();
    }
    const historical = normalize(app.snapshotExperience());
    const partialCount = app.requests.filter(request => request.url === '/api/record/partial').length;
    app.responses.push({ status: 200, data: preview() });
    app.drawRound(); app.paintRound(); await flush();
    app.responses.push({ status: 200, data: successfulAnalysis });
    await app.revealDestiny(); await flush();
    const analysis = app.requests.find(request => request.url === '/api/analyze');
    assert(analysis, 'Private analysis still works without the archival sidecar');
    const body = JSON.parse(analysis.body);
    assert.equal(body.experience, undefined, 'Private or withdrawn exposure history stays in this page');
    assert.equal(body.visitor, undefined); assert.equal(body.consent, undefined);
    assert.equal(app.requests.filter(request => request.url === '/api/record/partial').length, partialCount);
    assert.equal(app.state.roundFeedbackEnabled, true, 'Withdrawal never relabels the historical treatment');
    assert.deepEqual(normalize(app.snapshotExperience()), historical);
  }
}

async function zeroExposureStillRecordsFrozenMode() {
  for (const roundFeedback of [false, true]) {
    const app = playSetup({ consent: true, roundFeedback });
    app.responses.push({ status: 200, data: preview() }, { status: 200, data: { stored: true } });
    app.drawRound(); app.paintRound(); await flush();
    const expected = { schema_version: 'norn.experience.v1',
      round_feedback: roundFeedback ? 'on' : 'off', exposures: [] };
    const partial = app.requests.find(request => request.url === '/api/record/partial');
    assert.deepEqual(JSON.parse(partial.body).experience, expected);
    app.responses.push({ status: 200, data: successfulAnalysis });
    await app.revealDestiny(); await flush();
    const analysis = app.requests.find(request => request.url === '/api/analyze');
    assert.deepEqual(JSON.parse(analysis.body).experience, expected,
      'The treatment setting remains recorded even when no completed-round hint was painted');
    paintFeedbackFrames(app);
    assert.equal(app.snapshotExperience().exposures.length, 0, 'The final overlay cannot create a late exposure');
  }
}

const scenarios = { bothEntrancesFreezeOptIn, unavailableAndDefaultMakeNoProviderCalls,
  precommitClonesBeforeAnyChoice, tiesLateAndFinalRetainedTarget, collectedResultsStayHiddenUntilPortrait,
  feedbackExposureRequiresVisiblePaint, exposureKindsAndMonotoneClock, checkpointAndRevealCarryExperience,
  privateAndWithdrawnRevealKeepExperienceLocal, zeroExposureStillRecordsFrozenMode };
scenarios[process.argv[2]]().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional for frontend fixtures")
class PlayFrontendTests(unittest.TestCase):
    def test_opt_in_and_prediction_product_boundaries(self):
        for scenario in ("bothEntrancesFreezeOptIn", "unavailableAndDefaultMakeNoProviderCalls",
                         "precommitClonesBeforeAnyChoice", "tiesLateAndFinalRetainedTarget",
                         "collectedResultsStayHiddenUntilPortrait", "feedbackExposureRequiresVisiblePaint",
                         "exposureKindsAndMonotoneClock", "checkpointAndRevealCarryExperience",
                         "privateAndWithdrawnRevealKeepExperienceLocal", "zeroExposureStillRecordsFrozenMode"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", PLAY_FIXTURE, str(APP), scenario],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
