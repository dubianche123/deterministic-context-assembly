"""Exercise the shipped X-Ray v2 inspector against its actual JavaScript boundaries."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest

from test_frontend_events import APP, NODE_FIXTURE


XRAY_CASES = r"""
const textOf = element => [element.textContent, ...element.children.map(textOf)].join(' ');
const descendants = element => [element, ...element.children.flatMap(descendants)];
const CARD_IDS = Array.from({ length: 256 }, (_, index) => `NORN_${String(index + 1).padStart(4, '0')}`);
function trajectory(rounds, extra = {}) {
  const states = [];
  for (let round = 0; round <= rounds; round++) {
    const state = { round, mean: [0.3 * round, -0.2 * round, 0.15 * round, 0], sd: [0.8, 0.8, 0.8, 0.8 - 0.05 * round],
      cov_sn_tf: [[0.64, 0.01], [0.01, 0.64]], stop_bias: 0.1 * round,
      scores: CARD_IDS.map((_, index) => round ? ((index * 37) % 256) / 256 - 0.5 + round / 100 : 0),
      top: CARD_IDS.slice(round, round + 6),
      types: [{ type: 'INFP', p: 0.5 }, { type: 'INFJ', p: 0.3 }, { type: 'ENFP', p: 0.004 }, { type: 'ENFJ', p: 0.001 }],
      contributions: { weights: Array(12).fill(0.1 * round), weight_sd: Array(12).fill(0.5),
        cards: CARD_IDS.slice(round, round + 2).map(card_id => ({ card_id, axes: [0.1 * round, -0.2, 0.3, 0],
          pixel: 0.05, pixel_terms: Array(8).fill(0.00625), score: 0.25 + 0.1 * round, sd: 0.4 })) } };
    if (round) Object.assign(state, { shown: CARD_IDS.slice(round * 4, round * 4 + 4),
      picks: round % 2 ? [CARD_IDS[round * 4]] : [], delta: [0.3, -0.2, 0.01, 0], duration_ms: 3200 });
    states.push(state);
  }
  return { schema_version: 'norn.trajectory.v1', model_version: 'norn.choice.seq-mnl.v1',
    feature_version: 'sha256:feature', numerical_contract: 'norn.numeric.native-libm.v1',
    prior: { weight_precision: 1.5, stop_precision: 0.5 }, source: 'event_journal', verified: false, profile: null,
    components: [{ name: 'pixel.pc1', label: '<script>unsafe label</script>', explained_variance_ratio: 0.2 }],
    cards: CARD_IDS.map((id, index) => ({ id, type: 'INFP', cell: [index % 16, Math.floor(index / 16)] })),
    rounds, states, complete: false, open_round: true, events_hash: 'sha256:' + 'b'.repeat(64), event_count: 3, ...extra };
}
function guardedTrajectory(rounds) {
  const data = trajectory(rounds, { model_version: 'norn.choice.guarded-mnl.v1',
    prior: { weight_precision_formula: '1.5*(1+12/max(1,n_completed_exposed_rounds))', stop_precision: 0.5 } });
  data.states.forEach(current => {
    current.guard = { status: current.round === 2 ? 'active' : 'baseline',
      reason: 'content_evidence_below_threshold', log_evalue: 0, log_threshold: Math.log(20),
      ...(current.round === 2 ? { weight_precision: 3 } : {}) };
  });
  return data;
}
const noTimer = async (app, delay) => {
  await assert.rejects(app.runTimer(delay), /Expected a/);
};

function researchTrajectory() {
  const cards = CARD_IDS.map((_, index) => ({ id: `PAM_${String(index + 1).padStart(4, '0')}`,
    image: `./assets/decks/pamela-v1/PAM_${String(index + 1).padStart(4, '0')}.webp`,
    u: index / 128 - 1, x8: Array(8).fill(0.1) }));
  const states = ['population', 'sensitivity', 'personal', 'population'].map((layer, round) => {
    const weights = [layer === 'sensitivity' ? -0.5 : 1, ...Array(8).fill(layer === 'personal' ? 0.25 : 0)];
    const scores = cards.map(card => card.u * weights[0] + weights.slice(1).reduce((sum, weight) => sum + weight * 0.1, 0));
    const top = cards.map((card, index) => ({ id: card.id, score: scores[index] }))
      .sort((left, right) => right.score - left.score).slice(0, 6).map(card => card.id);
    return { round, layer, weights, candidate: Array(9).fill(100),
      log_evalues: { E1: round === 1 || round === 2 ? Math.log(25) : 0, E2: round === 2 ? Math.log(40) : -1 },
      log_threshold: Math.log(20), scores, top,
      shown: round ? cards.slice(round * 4, round * 4 + 4).map(card => card.id) : [],
      picked: round ? [cards[round * 4].id] : [],
      contributions: top.map(card_id => {
        const card = cards.find(card => card.id === card_id);
        const terms = [weights[0] * card.u, ...weights.slice(1).map(weight => weight * 0.1)];
        return { card_id, terms, score: terms.reduce((sum, term) => sum + term, 0) };
      }) };
  });
  const data = { schema_version: 'norn.research-trajectory.v1', cards, states,
    deck: 'pamela-v1@sha256:deck', algorithm_version: 'norn.research.v6.v1',
    source: 'event_journal', provider_calls: 0, events_hash: 'sha256:events', complete: false, open_round: true };
  let baselineLoss = 0, learningLoss = 0;
  const rows = states.slice(1).map((state, index) => {
    const baseline = [0.1, 0.2, 0.3, 0.4], learning = index ? [0.4, 0.3, 0.2, 0.1] : baseline;
    baselineLoss += -Math.log(baseline[0]); learningLoss += -Math.log(learning[0]);
    const lane = p => ({ status: 'ok', probabilities: p, ranks: p === baseline ? [4, 3, 2, 1] : [1, 2, 3, 4],
      target_nll: -Math.log(p[0]) });
    return { round: state.round, cutoff_rounds: state.round - 1, prefix_hash: `prefix-${index}`,
      options_hash: `options-${index}`, candidate_ids: state.shown, picks: state.picked, target: state.picked[0],
      scored: true, layer_before: states[index].layer, same_predictions: !index,
      display: { planned_count: 4, ready_loaded_count: 4, ever_seen_count: 4, ready_columns: 2, eligible_count: 4 },
      lanes: { baseline: lane(baseline), learning: lane(learning), jev: { status: 'not_run' } },
      cumulative: { n: index + 1, baseline_nll: baselineLoss / (index + 1), learning_nll: learningLoss / (index + 1) } };
  });
  data.comparison = { schema_version: 'norn.pamela-comparison.v1', mode: 'prequential', deck: data.deck,
    events_hash: data.events_hash, lanes: { baseline: { status: 'ok' }, learning: { status: 'ok' }, jev: { status: 'not_run' } },
    rounds: rows, provider_calls: 0 };
  return data;
}

async function closedPanelLiveRefetchAndLocalScrubbing() {
  const app = setup(); app.initializeXray();
  app.drawRound(); app.paintRound(); app.dom.cardGrid.children[0].click();
  assert.equal(app.requests.length, 0, 'A closed inspector makes no background requests');
  app.responses.push({ status: 200, data: trajectory(0) });
  app.setXrayOpen(true);
  assert.equal(app.dom.xrayPanel.hidden, false);
  assert.equal(app.dom.xrayToggle.attributes['aria-expanded'], 'true');
  assert.equal(app.dom.xrayTitle.focused, true);
  await app.runTimer(0);
  assert.equal(app.requests[0].url, '/api/trajectory');
  assert.deepEqual(JSON.parse(app.requests[0].body).journal, normalize(app.state.eventJournal));
  assert.equal(app.state.xrayCells.size, 256);
  assert.equal(app.dom.xrayRoundLabel.textContent, '0 / 0 轮');
  assert(app.dom.xrayStatus.textContent.includes('实时'));
  app.dom.cardGrid.children[1].click();
  app.dom.cardGrid.children[1].click();
  await noTimer(app, 180);
  assert.equal(app.requests.length, 1, 'Clicks inside an open round never refetch');
  const facts = JSON.stringify(app.state.eventJournal);
  app.nextRound(); app.paintRound();
  app.responses.push({ status: 200, data: trajectory(2) });
  await app.runTimer(180);
  assert.equal(app.requests.length, 2, 'Closing a round fetches the whole trajectory once');
  assert(JSON.parse(app.requests[1].body).journal.events.some(event => event.type === 'round_completed'));
  assert.equal(app.dom.xrayRoundLabel.textContent, '2 / 2 轮', 'A new trajectory follows the newest round');
  const cell = app.state.xrayCells.get(CARD_IDS[0]);
  app.dom.xrayRound.value = '0'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayRoundLabel.textContent, '0 / 2 轮');
  assert.equal(app.dom.xrayRoundHeading.textContent, '还没开始');
  assert.equal(cell.style.opacity, '0.580', 'Round 0 is uniformly half lit');
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayRoundHeading.textContent, '第 1 轮');
  assert.equal(app.state.xrayCells.get(CARD_IDS[4]).classes.has('picked'), true);
  assert.equal(app.state.xrayCells.get(CARD_IDS[5]).classes.has('shown'), true);
  assert(textOf(app.dom.xrayRoundNote).includes('选了 1 / 4 张'));
  assert(textOf(app.dom.xrayRoundNote).includes('外向 E'), 'The largest move names its direction');
  assert.notEqual(app.state.xrayMarker.group.style.transform, 'translate(50.00px, 50.00px)');
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert.equal(app.state.xrayCells.get(CARD_IDS[4]).classes.has('past'), true, 'Earlier picks keep a mark');
  assert.equal(app.state.xrayBars[0].positive.classes.has('lean'), false, 'A side lights up only beyond one sd');
  assert.equal(app.requests.length, 2, 'Scrubbing never requests');
  app.dom.xrayPlay.click();
  assert.equal(app.dom.xrayRoundLabel.textContent, '0 / 2 轮', 'Play from the end restarts at round 0');
  assert.equal(app.dom.xrayPlay.textContent, '❚❚');
  await app.runTimer(650); await app.runTimer(650);
  assert.equal(app.dom.xrayRoundLabel.textContent, '2 / 2 轮');
  await app.runTimer(650);
  assert.equal(app.dom.xrayPlay.textContent, '▶', 'Playback stops at the last round');
  assert.equal(app.requests.length, 2, 'Playback never requests');
  assert.equal(JSON.stringify(app.state.eventJournal).length >= facts.length, true);
  app.documentListeners.keydown({ key: 'Escape', preventDefault() {} });
  assert.equal(app.dom.xrayPanel.hidden, true);
  assert.equal(app.dom.xrayToggle.focused, true);
  app.nextRound(); app.paintRound();
  await noTimer(app, 180);
  assert.equal(app.requests.length, 2);
}

async function staleResponseAndFailure() {
  const app = setup(); app.initializeXray();
  app.drawRound(); app.paintRound();
  app.setXrayOpen(true);
  let release;
  app.responses.push({ status: 200, data: new Promise(resolve => { release = resolve; }) });
  const old = app.runTimer(0);
  await Promise.resolve();
  app.nextRound(); app.paintRound();
  app.responses.push({ status: 200, data: trajectory(1) });
  await app.runTimer(180);
  release(trajectory(5)); await old;
  assert.equal(app.dom.xrayRoundLabel.textContent, '1 / 1 轮', 'A stale response cannot replace a newer one');
  const facts = JSON.stringify(app.state.eventJournal);
  app.responses.push({ status: 503, data: { error: 'Unavailable' } });
  await app.refreshXray();
  assert(app.dom.xrayStatus.textContent.includes('选图与追问仍可继续'));
  assert.equal(app.dom.xrayContent.attributes['aria-busy'], 'false');
  assert.equal(JSON.stringify(app.state.eventJournal), facts);
  assert.equal(app.dom.xrayRoundLabel.textContent, '1 / 1 轮', 'Failure keeps the last trajectory');
}

async function frozenSessionImportAndReturn() {
  const app = setup(); app.initializeXray();
  app.drawRound(); app.paintRound();
  app.state.analysisResult = successfulAnalysis;
  const frozen = trajectory(3, { source: 'session', verified: true, complete: true, open_round: false,
    profile: { profile_version: 'norn.profile.v4', algorithm_version: 'norn.scoring.v4',
      preference_version: 'norn.choice.seq-mnl.v1', profile_hash: 'sha256:' + 'c'.repeat(64) },
    metrics: { raw_bytes: 123, prompt_bytes: 80, estimated_input_tokens: 27 },
    calls: [{ task: 'analysis', provider: 'mock', simulated: true, status: 'ok', provider_calls: 0, usage: {} }] });
  app.responses.push({ status: 200, data: frozen });
  app.setXrayOpen(true); await app.runTimer(0);
  assert.equal(app.requests[0].url, '/api/sessions/session-123/trajectory');
  assert.equal(app.requests[0].method, undefined);
  assert(app.dom.xrayStatus.textContent.includes('冻结'));
  assert.equal(app.dom.xraySource.textContent, '本次体验 · 已冻结');
  const original = '{\n "schema_version": "norn.replay.v1", "test": 8.0, "negative": -0.0\n}\n';
  app.dom.xrayImport.files = [{ size: original.length, text: async () => original }];
  app.responses.push({ status: 200, data: trajectory(2, { source: 'verified_export', verified: true }) });
  await app.importXrayRecord();
  assert.equal(app.requests[1].url, '/api/trajectory');
  assert.equal(app.requests[1].body, original, 'Import sends the original bytes unchanged');
  assert.equal(app.state.xraySource, 'import');
  assert.equal(app.state.analysisResult.session_id, 'session-123', 'Import cannot replace a live conversation');
  assert(app.dom.xrayStatus.textContent.includes('不恢复判词或追问会话'));
  assert.equal(app.dom.xrayLive.hidden, false);
  assert.equal(app.dom.xrayRoundLabel.textContent, '2 / 2 轮');
  app.dom.xrayRound.value = '0'; app.dom.xrayRound.listeners.input();
  app.dom.xrayRefresh.click(); await app.runTimer(0);
  assert.equal(app.requests.length, 2, 'An imported trajectory is already complete; refresh needs no request');
  app.responses.push({ status: 404, data: { error: 'expired' } });
  app.responses.push({ status: 200, data: trajectory(1) });
  app.dom.xrayLive.click(); await app.runTimer(0);
  assert.equal(app.state.xraySource, 'live');
  assert.equal(app.requests[2].url, '/api/sessions/session-123/trajectory');
  assert.equal(app.requests[3].url, '/api/trajectory', 'An expired session falls back to the page facts');
  assert.equal(app.dom.xrayLive.hidden, true);
  const facts = JSON.stringify(app.state.eventJournal);
  app.dom.xrayImport.files = [{ size: 8 * 1024 * 1024 + 1, text: async () => 'should not be read' }];
  await app.importXrayRecord();
  assert(app.dom.xrayStatus.textContent.includes('8 MiB'));
  assert.equal(app.requests.length, 4);
  assert.equal(JSON.stringify(app.state.eventJournal), facts);
}

async function engineeringDetailsStayCollapsedAndSafe() {
  const app = setup(); app.initializeXray();
  app.setXrayOpen(true);
  app.responses.push({ status: 200, data: trajectory(2, { source: 'session',
    profile: { profile_version: 'norn.profile.v4', algorithm_version: 'norn.scoring.v4', profile_hash: 'sha256:' + 'd'.repeat(64) },
    calls: [{ task: 'analysis', provider: 'openai-compatible', provider_calls: 1, simulated: false,
      usage: { cache_read_input_tokens: 0, cache_read_input_tokens_observed: true } }],
    metrics: { raw_bytes: 2048, prompt_bytes: 1024, estimated_input_tokens: 342 } }) });
  await app.runTimer(0);
  assert.equal(app.dom.xrayEngineering.open, undefined, '工程细节 starts collapsed');
  assert.equal(app.dom.xrayDetails.children.length, 0, 'Nothing is built while collapsed');
  assert(textOf(app.dom.xrayTypes).includes('INFP'));
  assert(textOf(app.dom.xrayTypes).includes('<1%'));
  assert.equal(app.dom.xrayGuesses.children.length, 6);
  app.dom.xrayEngineering.open = true; app.dom.xrayEngineering.listeners.toggle();
  const text = textOf(app.dom.xrayDetails);
  for (const expected of ['norn.choice.seq-mnl.v1', 'norn.profile.v4', 'dddddddddddd', '缓存读取 0 Token（实测）',
                          '模型上下文 1.0 KiB', 'Token 估算 342', '不请求后端']) {
    assert(text.includes(expected), expected);
  }
  assert(text.includes('<script>unsafe label</script>'), 'Untrusted labels are rendered as text');
  const parameters = app.state.xrayDetailNodes.parameters;
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert.equal(app.state.xrayDetailNodes.parameters, parameters, 'Scrubbing rewrites lines, not the section');
  assert(parameters.textContent.includes('第 1 轮后'));
  const { breakdown, table } = app.state.xrayDetailNodes;
  assert.equal(table.children.length, 0, 'The breakdown table is built only when opened');
  const requestsBefore = app.requests.length;
  breakdown.open = true; breakdown.listeners.toggle();
  const rows = table.children[0].children;
  assert.equal(rows.length, 3, 'Header plus one row per listed card');
  assert.deepEqual(rows[0].children.map(cell => cell.textContent), ['画面', 'E/I', 'N/S', 'T/F', 'J/P', '像素', '合计']);
  assert(rows[1].children[0].textContent.startsWith(CARD_IDS[1]));
  assert.equal(rows[1].children[2].textContent, '−0.20');
  assert.equal(rows[1].children[6].textContent, '0.35 ± 0.40');
  assert(rows[1].children[5].title.includes('pc8 +0.01'));
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert(table.children[0].children[1].children[0].textContent.startsWith(CARD_IDS[2]), 'Dragging refreshes the open table');
  assert.equal(app.requests.length, requestsBefore, 'The breakdown comes with the trajectory; no extra request');
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  const nodes = descendants(app.dom.xrayDetails);
  for (const node of nodes.filter(node => node.tag === 'pre')) {
    assert.equal(node.parent.tag, 'details');
    assert.notEqual(node.parent.open, true, 'Raw JSON sits in a collapsed disclosure');
    assert.equal(node.textContent, '展开后显示。', 'JSON is serialized only when opened');
  }
  const round = app.state.xrayDetailNodes.round;
  round.details.open = true; round.details.listeners.toggle();
  assert.equal(JSON.parse(round.pre.textContent).round, 1);
}

async function baselineUncertaintyAndTypesAreSuppressed() {
  const app = setup(); app.initializeXray();
  app.setXrayOpen(true);
  app.responses.push({ status: 200, data: guardedTrajectory(3) });
  await app.runTimer(0);
  assert.equal(app.dom.xrayEngineering.open, undefined, 'Technical policy data stays collapsed');
  assert.equal(app.state.xrayMarker.group.style.display, 'none', 'No candidate ellipse is drawn around a gated point');
  for (const bar of app.state.xrayBars) {
    assert.equal(bar.band.style.display, 'none');
    assert.equal(bar.dot.style.display, 'none');
    assert(!bar.row.attributes['aria-label'].includes('标准差'));
    assert.equal(bar.positive.classes.has('lean'), false);
  }
  assert.equal(textOf(app.dom.xrayTypes).trim(), '证据不足，暂不区分图片');
  assert(!app.dom.xrayWeave.attributes['aria-label'].includes('最可能'));
  assert.equal(app.dom.xrayGuesses.children.length, 0, 'A baseline does not manufacture predictions from tied card IDs');
  assert([...app.state.xrayCells.values()].every(cell => cell.style.opacity === '0.580'));
  app.state.xrayCells.get(CARD_IDS[0]).listeners.pointerenter({ clientX: 30, clientY: 40 });
  assert(textOf(app.dom.xrayTip).includes('证据不足，暂不区分图片'));
  assert(!textOf(app.dom.xrayTip).includes('当前排名'));

  app.dom.xrayEngineering.open = true; app.dom.xrayEngineering.listeners.toggle();
  const details = textOf(app.dom.xrayDetails);
  assert(details.includes('不确定性来自未门控候选'));
  assert(details.includes('生效参数'));
  assert(!details.includes('undefined'), 'Formula-based priors do not print a missing scalar');
  assert(app.state.xrayDetailNodes.parameters.textContent.includes('1.5*(1+12/'));
  const { breakdown, table } = app.state.xrayDetailNodes;
  breakdown.open = true; breakdown.listeners.toggle();
  const total = table.children[0].children[1].children[6];
  assert(!total.textContent.includes('±'), 'Candidate sd is not printed as the posterior range of a gated score');
  assert(total.title.includes('候选标准差'));

  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert.equal(app.state.xrayMarker.group.style.display, '');
  assert(app.state.xrayBars.every(bar => bar.band.style.display === '' && bar.dot.style.display === ''));
  assert(textOf(app.dom.xrayTypes).includes('INFP'), 'Admitted candidate states retain the interactive plots');
  assert(app.state.xrayDetailNodes.parameters.textContent.includes('3 / 0.5'));
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert.equal(app.state.xrayMarker.group.style.display, 'none');
  assert(!textOf(app.dom.xrayTypes).includes('INFP'), 'Scrubbing back removes placeholder type posteriors');
  assert.equal(app.requests.length, 1, 'Policy state changes stay local');
}

async function researchLayersScoresAndReplayStayLocal() {
  const app = setup({ config: { deck: 'pamela-v1' } }); app.initializeXray();
  assert.equal(app.dom.xrayToggle.hidden, false, 'The current deck keeps its X-Ray entry');
  const data = researchTrajectory();
  app.state.allCards = data.cards.map(card => ({ id: card.id, url: card.image }));
  app.responses.push({ status: 200, data });
  app.setXrayOpen(true); await app.runTimer(0);
  assert.equal(app.state.xrayCells.size, 256);
  assert.equal(app.dom.xrayPanel.classes.has('xray-research'), true);
  for (const axis of [app.dom.xrayAxisTop, app.dom.xrayAxisBottom, app.dom.xrayAxisLeft, app.dom.xrayAxisRight]) {
    assert.equal(axis.hidden, true, 'Legacy personality axes are removed even while an old stylesheet is cached');
  }
  assert.equal(app.dom.xrayRoundLabel.textContent, '3 / 3 轮');
  assert(textOf(app.dom.xrayBars).includes('参考口味'));
  assert.equal(app.dom.xrayGuesses.children.length, 6, 'Layer zero still ranks by the real population reference');
  assert(new Set([...app.state.xrayCells.values()].map(cell => cell.style.opacity)).size > 1,
    'A reference ranking must not be flattened as if all pictures were equally likely');
  const cell = app.state.xrayCells.get('PAM_0256');
  const position = [cell.style.gridColumn, cell.style.gridRow];
  const firstOpacity = cell.style.opacity;
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert(textOf(app.dom.xrayBars).includes('口味敏感度'));
  assert.equal(app.dom.xrayBars.children[0].children[1].classes.has('active'), true);
  assert.notEqual(cell.style.opacity, firstOpacity, 'The prediction map changes with the active model');
  assert.deepEqual([cell.style.gridColumn, cell.style.gridRow], position, 'Picture positions stay fixed while scrubbing');
  assert(textOf(app.dom.xrayRoundNote).includes('这一轮启用了口味敏感度'));
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayBars.children[0].children[2].classes.has('active'), true);
  assert(textOf(app.dom.xrayTypes).includes('2.00×'));
  assert(textOf(app.dom.xrayTypes).includes('不是准确率或概率'));
  app.dom.xrayRound.value = '0'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayBars.children[0].children[0].classes.has('active'), true);
  assert(textOf(app.dom.xrayRoundNote).includes('还没有个人证据'));
  assert(!textOf(app.dom.xrayBars).includes('100'), 'An ungated candidate is not presented as established preference');
  assert.equal(app.dom.xrayEngineering.open, undefined);
  assert.equal(app.dom.xrayDetails.children.length, 0, 'Raw details stay unbuilt by default');
  app.dom.xrayEngineering.open = true; app.dom.xrayEngineering.listeners.toggle();
  const nodes = app.state.xrayDetailNodes;
  assert.equal(nodes.chart.children.length, 0);
  nodes.breakdown.open = true; nodes.breakdown.listeners.toggle();
  assert.equal(nodes.chart.children.length, 6);
  assert.equal(nodes.chart.children[0].children[1].children[1].children.length, 9, 'Nine contribution terms are rendered as bars');
  assert(!descendants(nodes.chart).some(node => node.tag === 'table'), 'Contributions are visual rather than a wide table');
  assert(!descendants(app.dom.xrayDetails).filter(node => node.tag === 'pre').some(node => node.parent.open === true));
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert(nodes.parameters.textContent.includes('个人偏好'));
  assert.equal(app.requests.length, 1, 'Scrubbing, gates and contributions make no extra calls');
}

async function exportCopyMatchesActualContributions() {
  const app = setup({ config: { deck: 'pamela-v1' } });
  app.state.analysisResult = successfulAnalysis;
  app.responses.push({ status: 200, data: { journal: {}, profile: {}, contribution_ledger: { cards: [] } } });
  await app.exportSessionRecord();
  assert(app.dom.exportStatus.textContent.includes('贡献账本'));
  app.responses.push({ status: 200, data: { journal: {}, profile: {} } });
  await app.exportSessionRecord();
  assert(app.dom.exportStatus.textContent.includes('版本化画像'));
  assert(!app.dom.exportStatus.textContent.includes('贡献账本'), 'The export message must not claim missing contributions');
}

async function researchLoadingNeverShowsClassicExplanations() {
  const app = setup({ config: { deck: 'pamela-v1' } });
  app.initializeXray();
  assert.equal(app.dom.xrayContent.hidden, true, 'The deck inspector starts without the classic empty-state content');
  app.setXrayOpen(true);
  let release;
  app.responses.push({ status: 200, data: new Promise(resolve => { release = resolve; }) });
  const pending = app.runTimer(0);
  await Promise.resolve();
  assert.equal(app.dom.xrayContent.hidden, true, 'Pending trajectory requests cannot flash old personality axes');
  assert(app.dom.xrayStatus.textContent.includes('正在读取'));
  release(researchTrajectory());
  await pending;
  assert.equal(app.dom.xrayContent.hidden, false, 'Only a matching completed trajectory reveals the deck inspector');
  assert.equal(app.dom.xrayAxisTop.hidden, true);

  const failed = setup({ config: { deck: 'pamela-v1' } });
  failed.initializeXray(); failed.setXrayOpen(true);
  failed.responses.push({ status: 503, data: { error: 'Unavailable' } });
  await failed.runTimer(0);
  assert.equal(failed.dom.xrayContent.hidden, true, 'A first-load failure keeps classic content hidden');
  assert(failed.dom.xrayStatus.textContent.includes('透视暂时不可用'));

  const mismatched = setup({ config: { deck: 'pamela-v1' } });
  mismatched.initializeXray(); mismatched.setXrayOpen(true);
  mismatched.responses.push({ status: 200, data: trajectory(1) });
  await mismatched.runTimer(0);
  assert.equal(mismatched.dom.xrayContent.hidden, true, 'A stale classic response cannot populate a deck inspector');
  assert(mismatched.dom.xrayStatus.textContent.includes('牌库与轨迹版本不匹配'));
}

async function predictiveComparisonAndCollectionLock() {
  const locked = setup({ config: { deck: 'pamela-v1', collect: true } });
  locked.state.consent = true;
  locked.initializeXray(); locked.setXrayOpen(true);
  assert.equal(locked.requests.length, 0, 'A collecting visit receives no predictions while choosing');
  assert.equal(locked.dom.xrayContent.hidden, true);
  assert(locked.dom.xrayStatus.textContent.includes('避免预测提示'));
  await noTimer(locked, 0);
  locked.state.sessionSealed = true;
  locked.responses.push({ status: 200, data: researchTrajectory() });
  locked.scheduleXrayRefresh(0, true); await locked.runTimer(0);
  assert.equal(locked.requests.length, 1, 'Sealing unlocks the read-only comparison');
  assert.equal(locked.dom.xrayRound.disabled, false);

  const app = setup({ config: { deck: 'pamela-v1' } }); app.initializeXray();
  const data = researchTrajectory();
  app.state.allCards = data.cards.map(card => ({ id: card.id, url: card.image }));
  app.responses.push({ status: 200, data }); app.setXrayOpen(true); await app.runTimer(0);
  assert.equal(app.dom.xrayModelView.open, false, 'The old full-deck map and gates are secondary and collapsed');
  assert.equal(app.dom.xrayComparisonCards.children.length, 4);
  assert(textOf(app.dom.xrayComparisonLegend).includes('Jev · 本次未运行'));
  assert.equal(app.dom.xrayComparisonCards.children[0].children.length, 3, 'One shared image plus two probability rows, no fabricated Jev lane');
  const latest = app.dom.xrayComparisonCards.children[0].children[2].children[1].children[0].style.width;
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert(textOf(app.dom.xrayComparisonResult).includes('参考基线相同'));
  assert(textOf(app.dom.xrayComparisonNote).includes('第 0 轮'), 'First prediction uses no current-round choices');
  assert.notEqual(app.dom.xrayComparisonCards.children[0].children[2].children[1].children[0].style.width, latest);
  const svg = app.dom.xrayComparisonLoss.children[0];
  const lines = svg.children.filter(node => node.tag === 'polyline');
  assert(lines.every(line => line.attributes.points.split(' ').length === 1), 'Loss curves stop at the scrubbed round');
  assert(app.dom.xrayComparisonCards.children[0].classes.has('target'));
  app.dom.xrayRound.value = '0'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayComparisonCards.children.length, 0, 'No fake prediction or hit before a completed round');
  assert.equal(app.requests.length, 1, 'Private demo scrubbing never calls a model or endpoint');
}

async function offlineComparisonSwitchesEveryLane() {
  const app = setup({ config: { deck: 'pamela-v1' } }); app.initializeXray();
  const data = researchTrajectory();
  app.state.allCards = data.cards.map(card => ({ id: card.id, url: card.image }));
  app.responses.push({ status: 200, data }); app.setXrayOpen(true); await app.runTimer(0);
  const offline = JSON.parse(JSON.stringify(data.comparison));
  offline.mode = 'frozen_prefix'; offline.training = { cutoff: 1 };
  offline.rounds = offline.rounds.slice(1);
  offline.lanes.jev = { status: 'ok' };
  offline.rounds.forEach(row => {
    row.cutoff_rounds = 1;
    row.lanes.learning.probabilities = [0.25, 0.25, 0.25, 0.25];
    row.lanes.jev = { status: 'ok', probabilities: [0.7, 0.1, 0.1, 0.1], ranks: [1, 2, 2, 2] };
    row.cumulative.jev_nll = -Math.log(0.7);
  });
  const raw = JSON.stringify(offline).replace('"provider_calls":0', '"provider_calls":0.0');
  app.dom.xrayComparisonImport.files = [{ size: raw.length, text: async () => raw }];
  app.responses.push({ status: 200, data: { ...data, offline_comparison: offline } });
  await app.dom.xrayComparisonImport.listeners.change();
  assert.equal(app.requests.length, 2);
  assert(app.requests[1].url.endsWith('/trajectory'));
  assert(app.requests[1].body.includes('"provider_calls":0.0'), 'Raw Python float spelling survives the verification envelope');
  assert.equal(app.dom.xrayComparisonMode.textContent, '冻结前 1 轮');
  assert.equal(app.dom.xrayModelView.hidden, true, 'Rolling state visuals are not mixed into a frozen-prefix report');
  assert.equal(app.dom.xrayRound.value, '2');
  assert.equal(app.dom.xrayComparisonCards.children[0].children.length, 4, 'Imported results add the genuine third lane');
  assert.equal(app.dom.xrayComparisonCards.children[0].children[2].children[2].textContent, '25%', 'Learning scores switch to frozen-prefix results too');
  assert.equal(app.dom.xrayComparisonCards.children[0].children[3].children[2].textContent, '70%');
  app.dom.xrayComparisonOnline.listeners.click();
  assert.equal(app.dom.xrayComparisonMode.textContent, '逐轮预测');
  assert.equal(app.dom.xrayModelView.hidden, false);
  assert.equal(app.dom.xrayComparisonCards.children[0].children.length, 3);
  assert.equal(app.requests.length, 2, 'Switching reports is local');
  const wrong = { ...offline, events_hash: 'sha256:other' };
  app.responses.push({ status: 200, data: { ...data, offline_comparison: wrong } });
  await app.dom.xrayComparisonImport.listeners.change();
  assert.equal(app.state.xrayOfflineComparison, null, 'A report for another event history is not used');
  assert(app.dom.xrayStatus.textContent.includes('导入未通过'));
}

async function noChoiceAndMultipleChoiceAreNotFakeWins() {
  const app = setup({ config: { deck: 'pamela-v1' } }); app.initializeXray();
  const data = researchTrajectory();
  const last = data.comparison.rounds[2];
  last.target = null; last.picks = []; last.scored = false; last.skipped_reason = 'no_pick';
  last.cumulative = { n: 2, baseline_nll: 2.3, learning_nll: 1.6 };
  last.display.ever_seen_count = null; last.display.ready_columns = null;
  const multi = data.comparison.rounds[1];
  multi.picks = multi.candidate_ids.slice(0, 2);
  app.state.allCards = data.cards.map(card => ({ id: card.id, url: card.image }));
  app.responses.push({ status: 200, data }); app.setXrayOpen(true); await app.runTimer(0);
  assert(app.dom.xrayComparisonResult.textContent.includes('没有保留图片'));
  assert(app.dom.xrayLossValue.textContent.startsWith('2 个'), 'An empty round never counts as zero-loss success');
  assert(!app.dom.xrayComparisonContext.textContent.includes('曾见'), 'Missing visibility is not fabricated as a measured count');
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert.equal(app.dom.xrayComparisonCards.children.filter(card => card.classes.has('actual')).length, 2);
  assert.equal(app.dom.xrayComparisonCards.children.filter(card => card.classes.has('target')).length, 1,
    'Only the first finally-kept image is the conditional prediction target');
}

const cases = { closedPanelLiveRefetchAndLocalScrubbing, staleResponseAndFailure, frozenSessionImportAndReturn, engineeringDetailsStayCollapsedAndSafe, baselineUncertaintyAndTypesAreSuppressed, researchLayersScoresAndReplayStayLocal, exportCopyMatchesActualContributions, researchLoadingNeverShowsClassicExplanations, predictiveComparisonAndCollectionLock, offlineComparisonSwitchesEveryLane, noChoiceAndMultipleChoiceAreNotFakeWins };
cases[process.argv[2]]().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional; install it to execute the frontend fixture")
class XrayFrontendTests(unittest.TestCase):
    def test_actual_inspector_interactions(self):
        fixture = NODE_FIXTURE[:NODE_FIXTURE.index("const cases =")]
        for scenario in ("closedPanelLiveRefetchAndLocalScrubbing", "staleResponseAndFailure",
                         "frozenSessionImportAndReturn", "engineeringDetailsStayCollapsedAndSafe",
                         "baselineUncertaintyAndTypesAreSuppressed", "researchLayersScoresAndReplayStayLocal",
                         "exportCopyMatchesActualContributions", "researchLoadingNeverShowsClassicExplanations",
                         "predictiveComparisonAndCollectionLock", "offlineComparisonSwitchesEveryLane",
                         "noChoiceAndMultipleChoiceAreNotFakeWins"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", fixture + XRAY_CASES, str(APP), scenario],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class XrayStaticContractTests(unittest.TestCase):
    def test_v2_layout_semantics_and_mobile_contract(self):
        frontend = APP.parent
        html = (frontend / "index.html").read_text(encoding="utf-8")
        css = (frontend / "style.css").read_text(encoding="utf-8")
        panel = html[html.index('<aside id="xray-panel"'):html.index("</aside>")]
        self.assertIn('aria-expanded="false" aria-controls="xray-panel"', html)
        for contract in ('aria-labelledby="xray-title" hidden',
                         'id="xray-round" type="range"', 'id="xray-play"', 'id="xray-weave"',
                         'id="xray-bars"', 'id="xray-strip"', 'id="xray-types"', 'id="xray-guesses"',
                         'id="xray-import"', 'role="status" aria-live="polite"', '<summary>工程细节</summary>'):
            self.assertIn(contract, panel)
        self.assertNotIn("<details open", html)
        self.assertEqual(panel.count("<details"), 2, "Current-model visuals and engineering details each stay collapsed")
        self.assertLess(panel.index('id="xray-comparison"'), panel.index('id="xray-model-view"'))
        self.assertIn('id="xray-comparison-import"', panel)
        self.assertLess(panel.index('id="xray-guesses"'), panel.index("<summary>工程细节</summary>"),
                        "工程细节 is the last section")
        for retired in ("xray-whatif", "xray-map", "xray-prefix", "xray-timeline", "旧四轴参考"):
            self.assertNotIn(retired, html)
        self.assertIn(".xray-panel :focus-visible", css)
        self.assertLess(html.index('</details>', html.index('id="entry-detail"')), html.index('id="deck-credit"'),
                        "Image credits remain visible without collecting any records")
        self.assertIn("overscroll-behavior: contain", css)
        self.assertIn(".xray-panel { width: 100%", css)
        self.assertIn(".xray-scrub { position: sticky", css)
        self.assertIn("prefers-reduced-motion", css)
        source = APP.read_text(encoding="utf-8")
        block = source[source.index("// ─── Optional engineering inspector"):source.index("function cleanMarkdown")]
        self.assertNotIn("innerHTML = `", block, "Inspector text is assigned through textContent")
        self.assertIsNone(re.search(r"/api/preview|/replay'", block), "v2 reads only the trajectory endpoints")


if __name__ == "__main__":
    unittest.main()
