"""Execute the shipped JavaScript against a small DOM fixture, without a browser.

Node is optional for a bare Python checkout. These tests run the actual event
handlers and outgoing requests; the fixture supplies only DOM, clock and network
boundaries. Real-browser layout, image and accessibility checks remain separate.
"""

from pathlib import Path
import shutil
import subprocess
import unittest


APP = Path(__file__).resolve().parents[1] / "the-norn-machine" / "frontend" / "app.js"


NODE_FIXTURE = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const normalize = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    this.textContent = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.classes = new Set();
    this.classList = {
      add: (...values) => values.forEach(value => this.classes.add(value)),
      remove: (...values) => values.forEach(value => this.classes.delete(value)),
      contains: value => this.classes.has(value),
    };
  }
  set className(value) { this.classes = new Set(value.split(' ')); }
  set innerHTML(value) {
    this.children = [];
    if (value) {
      for (const match of value.matchAll(/<p><\/p>/g)) this.appendChild(new Element('p'));
    }
  }
  setAttribute(key, value) { this.attributes[key] = value; if (key === 'class') this.className = value; }
  focus() { this.focused = true; }
  matches(selector) { return selector === ':hover' && this.hovered === true; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 520, height: 92 }; }
  addEventListener(key, callback) { this.listeners[key] = callback; }
  appendChild(child) { this.children.push(child); child.parent = this; }
  querySelectorAll(selector) {
    return this.children.filter(child => selector === '.card'
      ? child.classList.contains('card') : child.tag === selector);
  }
  getContext() {
    // A no-op 2D context: drawing calls (and gradients they return) are inert.
    const context = new Proxy({}, { get: (target, key) => key in target ? target[key] : () => context });
    return context;
  }
  click() { if (this.listeners.click) this.listeners.click(); }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
  }
}

function setup({ observer = false, hover = false, pilot = false, config = {} } = {}) {
  let now = 1000;
  const observers = [];
  const frames = [];
  const timers = [];
  const requests = [];
  const responses = [];
  const downloads = [];
  const blobs = [];
  const elements = new Map();
  const documentListeners = {};
  const windowListeners = {};
  const document = {
    body: new Element('body'),
    visibilityState: 'visible',
    addEventListener(key, callback) { documentListeners[key] = callback; },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement(tag) {
      const element = new Element(tag);
      if (tag === 'a') element.click = () => downloads.push({ href: element.href, filename: element.download });
      return element;
    },
    createElementNS(namespace, tag) { return new Element(tag); },
  };
  const context = vm.createContext({
    document,
    window: {
      __NORN_CONFIG__: { apiEndpoint: '/api', provider: 'mock', ...config,
                         ...(pilot ? { collect: true, consentVersion: 'norn.consent.v4' } : {}) },
      location: { protocol: 'http:', reload() {}, ...(pilot ? { search: '?pilot=new-material-v1' } : {}) },
      innerWidth: 1280, innerHeight: 800,
      addEventListener(key, callback) { windowListeners[key] = callback; },
      ...(hover ? { matchMedia: query => ({ matches: query === '(any-hover: hover)' }) } : {}),
    },
    performance: { now: () => now },
    console: { log() {}, warn() {}, error() {} },
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    cancelAnimationFrame() {},
    setTimeout: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer; },
    clearTimeout: timer => { const index = timers.indexOf(timer); if (index !== -1) timers.splice(index, 1); },
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options });
      const response = responses.shift();
      if (!response) throw new Error('Network unavailable');
      if (response.wait) await response.wait;
      return {
        ok: response.status < 400,
        status: response.status,
        json: async () => response.raw === undefined ? response.data : JSON.parse(response.raw),
        text: async () => response.raw === undefined ? JSON.stringify(response.data) : response.raw,
      };
    },
    Blob,
    AbortController,
    URLSearchParams,
    URL: { createObjectURL(blob) { blobs.push(blob); return 'blob:record'; }, revokeObjectURL() {} },
  });
  if (pilot) {
    context.URLSearchParams = URLSearchParams;
    context.Image = class { set src(value) { this.loaded = value; } };
    context.crypto = { getRandomValues: array => array.fill(7) };
    context.btoa = value => Buffer.from(value, 'binary').toString('base64');
  }
  if (observer) {
    // Only the browser boundary is faked: tests decide what intersects.
    context.IntersectionObserver = class {
      constructor(callback, options) { this.callback = callback; this.options = options; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); }
      disconnect() { this.disconnected = true; this.targets = []; }
    };
  }
  const marker = "document.addEventListener('DOMContentLoaded', init);";
  assert(source.includes(marker), 'Test exposure marker must target the shipped application');
  vm.runInContext(source.replace(marker,
    'globalThis.api = { state, dom, startTest, drawRound, nextRound, revealDestiny, retryAnalysis, exportSessionRecord, handleDialogueSubmit, submitFeedback, skipFeedback, initializeXray, setXrayOpen, refreshXray, scheduleXrayRefresh, importXrayRecord, showXrayRound, toggleXrayPlayback, slotContext, forgetVisitor, onVisitorStorageChange, savePartialVisit, schedulePartialVisit, openRoundCheckpoint };'), context);
  const api = context.api;
  for (const match of source.matchAll(/dom\.(\w+)\s*= document\.getElementById\('([^']+)'\)/g)) {
    api.dom[match[1]] = document.getElementById(match[2]);
  }
  api.state.allCards = Array.from({ length: 4 }, (_, index) => ({
    id: `NORN_${String(index + 1).padStart(4, '0')}`, url: './local.webp', type: 'INTJ', coords: {},
  }));
  api.state.cardsPerRound = { min: 4, max: 4 };
  const cardImage = card => card.children.find(child => child.tag === 'img');
  function settleImage(index, loaded = true) {
    const img = cardImage(api.dom.cardGrid.children[index]);
    (loaded ? img.onload : img.onerror)();
  }
  function paintRound({ images = 'load' } = {}) {
    const transition = timers.findIndex(timer => timer.delay === 400);
    if (transition !== -1) {
      const timer = timers.splice(transition, 1)[0];
      now += 400;
      timer.callback();
    }
    assert(frames.length, 'Round rendering must enter the browser render loop');
    frames.shift()(now);
    if (images === 'load') api.dom.cardGrid.children.forEach((_, index) => settleImage(index));
  }
  function intersect(index, ratio) {
    const target = api.dom.cardGrid.children[index];
    const active = observers.find(item => !item.disconnected && item.targets.includes(target));
    assert(active, 'Each displayed card must be observed while its round is open');
    active.callback([{ target, isIntersecting: ratio > 0, intersectionRatio: ratio }]);
  }
  function setHidden(hidden) {
    document.visibilityState = hidden ? 'hidden' : 'visible';
    documentListeners.visibilitychange();
  }
  function runFrames(until, limit = 2000) {
    for (let count = 0; count < limit && frames.length && !until(); count++) {
      now += 50;
      frames.shift()(now);
    }
    assert(until(), 'Expected the render loop to reach the requested state');
  }
  return { ...api, requests, responses, downloads, blobs, paintRound, settleImage, intersect, setHidden,
    runFrames, observers, documentListeners, windowListeners, timers,
    runTimer: async delay => {
      const index = timers.findIndex(timer => timer.delay === delay);
      assert(index !== -1, `Expected a ${delay}ms timer`);
      return timers.splice(index, 1)[0].callback();
    }, setTime: value => { now = value; }, time: () => now };
}

const successfulAnalysis = {
  reading: 'A local reading.', session_id: 'session-123', provider: 'mock', simulated: true,
  max_dialogue_turns: 1, profile_version: 'norn.profile.v1', profile_hash: 'a'.repeat(64),
};

async function workflow() {
  const app = setup();
  app.drawRound();
  assert.equal(app.state.roundTransitioning, true);
  assert.equal(app.dom.nextBtn.disabled, true);
  assert.equal(app.dom.revealBtn.disabled, true);
  app.dom.cardGrid.children[0].click();
  assert.equal(app.state.eventJournal.events.length, 0, 'Unrendered cards cannot record a selection');
  app.paintRound();
  assert.equal(app.dom.nextBtn.disabled, false);
  const oldCard = app.dom.cardGrid.children[0];
  oldCard.click(); oldCard.click();
  app.setTime(1700);
  oldCard.click();
  assert.deepEqual(normalize(app.state.eventJournal.events.map(event => event.type)),
    ['round_started', 'round_ready', 'card_selected', 'card_deselected', 'card_selected']);
  assert.deepEqual(normalize(app.state.eventJournal.events[1]),
    { event_id: 'evt-000002', sequence: 2, type: 'round_ready', round: 1, ready_offset_ms: 0, unloaded_card_ids: [],
      layout: null });
  assert.deepEqual(normalize(app.state.eventJournal.events.slice(2).map(event => event.t_ms)), [0, 0, 700],
    'Choice time is measured from readiness');
  app.setTime(3500);
  app.nextRound();
  const lengthDuringTransition = app.state.eventJournal.events.length;
  oldCard.click();
  app.nextRound();
  await app.revealDestiny();
  assert.equal(app.state.roundNumber, 2, 'Fast next clicks must not create phantom rounds');
  assert.equal(app.state.eventJournal.events.length, lengthDuringTransition);
  assert.equal(app.dom.revealOverlay.classList.contains('active'), false);
  app.paintRound();
  const repeatedCard = app.dom.cardGrid.children.find(card => card.dataset.id === oldCard.dataset.id);
  assert.equal(repeatedCard.classList.contains('selected'), true);
  repeatedCard.click();
  assert.equal(app.state.selectedCards.length, 0, 'A repeated card may withdraw a previous-round selection');
  app.setTime(5200);
  await app.revealDestiny(); // expected first network failure
  assert.equal(app.requests.length, 1);
  const firstRequest = app.requests[0].body;
  const payload = JSON.parse(firstRequest);
  assert.deepEqual(Object.keys(payload), ['journal', 'request_id', 'language']);
  assert.equal(payload.language, 'zh', 'Legacy embeds retain the Chinese default');
  assert.match(payload.request_id, /^reading-[a-zA-Z0-9-]+$/);
  assert.equal(payload.journal.schema_version, 'norn.events.v5');
  assert.deepEqual(payload.journal.events.filter(event => event.type === 'round_completed').map(event => event.hover),
    [null, null], 'Without hover support there is no hover timeline, not an empty one');
  assert(payload.journal.events.filter(event => event.type === 'card_selected').every(event => event.input === null),
    'A click without pointer details records an unknown input');
  assert(payload.journal.events.filter(event => event.type.startsWith('card_')).every(event => event.layout === null),
    'Unmeasurable choice-time layout is unknown, not an invented screen position');
  const events = payload.journal.events;
  assert.deepEqual(events.map(event => event.sequence), events.map((_, index) => index + 1));
  assert.deepEqual(events.map(event => event.event_id), events.map((_, index) => `evt-${String(index + 1).padStart(6, '0')}`));
  assert.deepEqual(events.filter(event => event.type === 'round_completed').map(event => event.duration_ms), [2500, 1300]);
  assert.deepEqual(events.filter(event => event.type === 'round_completed').map(event => [event.hidden_ms, event.seen_card_ids]),
    [[0, null], [0, null]], 'Without an observer, visibility is unknown rather than invented');
  assert.equal(events[events.length - 1].type, 'session_completed');
  assert.equal(events[8].type, 'card_deselected');
  assert.equal(events[8].round, 2);
  assert(Object.isFrozen(app.state.pendingAnalysisPayload));
  assert(Object.isFrozen(app.state.eventJournal.events));
  app.state.selectedCards.push({ id: 'CLIENT_UI_IS_NOT_AUTHORITY', round: 999 });
  repeatedCard.click();
  await app.revealDestiny();
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.retryAnalysis();
  assert.equal(app.requests.length, 2);
  assert.equal(app.requests[1].body, firstRequest, 'A retry must reuse the exact frozen event journal');
  assert.equal(app.dom.recordExport.hidden, false);
  assert(app.dom.profileVersion.textContent.includes('norn.profile.v1'));
  const exportRaw = `{\n  "journal": ${JSON.stringify(payload.journal)},\n  "profile": {"profile_hash": "${'a'.repeat(64)}", "coordinates": {"E_I": 8.0, "S_N": -0.0, "T_F": 1e-07}},\n  "ledger": {"entries": []}\n}\n`;
  const exported = JSON.parse(exportRaw);
  app.responses.push({ status: 200, raw: exportRaw });
  await app.exportSessionRecord();
  assert.equal(app.requests[2].url, '/api/sessions/session-123/export');
  assert.equal(app.requests[2].method, undefined);
  assert.equal(app.downloads[0].filename, 'norn-session-aaaaaaaaaaaa.json');
  const downloadedText = await app.blobs[0].text();
  assert.equal(downloadedText, exportRaw, 'Export must preserve canonical numeric spelling and whitespace byte for byte');
  assert.deepEqual(JSON.parse(downloadedText), exported);
  app.responses.push({ status: 404, data: { error: 'expired' } });
  await app.exportSessionRecord();
  assert(app.dom.exportStatus.textContent.includes('重新编织'));
  assert.equal(app.dom.exportRecord.disabled, false, 'An expired export must not lock the page');
}

async function emptySelection() {
  const app = setup();
  app.drawRound(); app.paintRound();
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  assert.deepEqual(JSON.parse(app.requests[0].body).journal.events.map(event => event.type),
    ['round_started', 'round_ready', 'round_completed', 'session_completed']);
  app.dom.dialogueInput.value = '为什么？';
  app.responses.push({ status: 200, data: { reply: 'A local follow-up.', turn_count: 1, ended: true } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  const dialogueRequest = JSON.parse(app.requests[1].body);
  assert.equal(dialogueRequest.session_id, 'session-123');
  assert.equal(dialogueRequest.user_message, '为什么？');
  assert.match(dialogueRequest.turn_id, /^turn-[a-zA-Z0-9-]+$/);
  assert.equal(app.state.dialogueTurns, 1);
}

async function eventLimit() {
  const app = setup();
  app.drawRound(); app.paintRound();
  const card = app.dom.cardGrid.children[0];
  while (app.state.eventJournal.events.length < 9998) card.click();
  assert.equal(card.attributes['aria-disabled'], 'true');
  assert.equal(app.dom.nextBtn.disabled, true);
  assert.equal(app.dom.revealBtn.disabled, false);
  card.click(); app.nextRound();
  assert.equal(app.state.eventJournal.events.length, 9998);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  const events = JSON.parse(app.requests[0].body).journal.events;
  assert.equal(events.length, 10000);
  assert.deepEqual(events.slice(-2).map(event => event.type), ['round_completed', 'session_completed']);
}

async function roundLimit() {
  const app = setup();
  for (let round = 1; round <= 256; round++) {
    app.drawRound(); app.paintRound();
    assert.equal(app.state.roundNumber, round);
  }
  assert.equal(app.dom.nextBtn.disabled, true);
  assert.equal(app.dom.revealBtn.disabled, false);
  assert(app.dom.roundStatus.textContent.includes('揭示命运'));
  app.nextRound();
  assert.equal(app.state.roundNumber, 256);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  const events = JSON.parse(app.requests[0].body).journal.events;
  assert.equal(events.filter(event => event.type === 'round_started').length, 256);
  assert.equal(events.filter(event => event.type === 'round_completed').length, 256);
}

async function idempotentDialogueRetries() {
  const app = setup();
  for (let round = 0; round < 21; round++) { app.drawRound(); app.paintRound(); }
  app.responses.push({ status: 200, data: { ...successfulAnalysis, max_dialogue_turns: 3 } });
  await app.revealDestiny();
  assert.equal(app.state.pendingAnalysisPayload, null, 'A confirmed reading clears the pending operation');
  app.dom.dialogueInput.value = '为什么？';
  await app.handleDialogueSubmit({ preventDefault() {} }); // response lost or network unavailable
  const failed = app.requests[1].body;
  assert.equal(app.state.dialogueTurns, 0);
  assert.equal(app.dom.dialogueInput.value, '为什么？');
  app.responses.push({ status: 200, data: { reply: 'A reply.', turn_count: 1, max_turns: 3, ended: false } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  assert.equal(app.requests[2].body, failed, 'A retry of an unconfirmed turn retains its exact operation ID');
  assert.equal(app.state.pendingDialoguePayload, null);
  app.dom.dialogueInput.value = '为什么？';
  app.responses.push({ status: 200, data: { reply: 'Another reply.', turn_count: 2, max_turns: 3, ended: false } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  assert.notEqual(JSON.parse(app.requests[3].body).turn_id, JSON.parse(failed).turn_id,
    'Repeated text after success is a new authorized operation');
  app.dom.dialogueInput.value = '再说一句';
  await app.handleDialogueSubmit({ preventDefault() {} });
  const secondFailure = JSON.parse(app.requests[4].body);
  app.dom.dialogueInput.value = '换个问题';
  await app.handleDialogueSubmit({ preventDefault() {} });
  assert.notEqual(JSON.parse(app.requests[5].body).turn_id, secondFailure.turn_id,
    'Editing an unconfirmed question starts a new operation');
}

async function terminalAndAdmissionRetries() {
  const app = setup();
  app.drawRound(); app.paintRound();
  app.responses.push({ status: 503, data: { error: 'Unavailable', status: 'provider_unavailable' } });
  await app.revealDestiny();
  const first = JSON.parse(app.requests[0].body);
  app.responses.push({ status: 429, data: { error: 'Busy', status: 'concurrency_limit' } });
  await app.retryAnalysis();
  const admitted = JSON.parse(app.requests[1].body);
  assert.notEqual(first.request_id, admitted.request_id,
    'An explicit retry after a confirmed provider failure starts a new operation');
  assert.deepEqual(first.journal, admitted.journal);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.retryAnalysis();
  assert.equal(app.requests[2].body, app.requests[1].body, '429 admission retry retains its operation ID');
  app.dom.dialogueInput.value = '为什么？';
  app.responses.push({ status: 502, data: { error: 'Invalid', status: 'invalid_output' } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  const failedTurn = JSON.parse(app.requests[3].body);
  app.responses.push({ status: 429, data: { error: 'Busy', status: 'concurrency_limit' } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  const retryTurn = JSON.parse(app.requests[4].body);
  assert.notEqual(retryTurn.turn_id, failedTurn.turn_id);
  app.responses.push({ status: 200, data: { reply: 'A reply.', turn_count: 1, ended: true } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  assert.equal(app.requests[5].body, app.requests[4].body);
}

async function randomDisplayIgnoresLegacyAxes() {
  const app = setup();
  app.state.allCards = Array.from({ length: 12 }, (_, index) => {
    const card = { id: `NORN_${String(index + 1).padStart(4, '0')}`, url: './local.webp' };
    Object.defineProperty(card, 'type', { get() { throw new Error('Display cannot inspect personality labels'); } });
    Object.defineProperty(card, 'coords', { get() { throw new Error('Display cannot inspect old four-axis scores'); } });
    return card;
  });
  const known = new Set(app.state.allCards.map(card => card.id));
  const cycle = new Set();
  for (let round = 0; round < 3; round++) {
    app.drawRound(); app.paintRound();
    const cards = app.state.currentRoundCards.map(card => card.id);
    assert.equal(new Set(cards).size, 4);
    assert(cards.every(id => known.has(id) && !cycle.has(id)));
    cards.forEach(id => cycle.add(id));
    const started = app.state.eventJournal.events[app.state.eventJournal.events.length - 2];
    assert.equal(started.type, 'round_started');
    assert.deepEqual(normalize(started.card_ids), normalize(cards), 'The journal records exactly the random display evidence');
  }
  assert.equal(cycle.size, 12);
  app.drawRound(); app.paintRound();
  assert(app.state.currentRoundCards.every(card => known.has(card.id)));
  assert.equal(app.state.usedCardIds.size, 4, 'Pool exhaustion begins another random cycle');
  assert.equal(app.requests.length, 0);
}

async function readinessVisibilityAndIdle() {
  const app = setup({ observer: true });
  app.drawRound(); app.paintRound({ images: 'none' });
  const [first, second, third, failed] = app.dom.cardGrid.children;
  assert.deepEqual(normalize(app.state.eventJournal.events.map(event => event.type)), ['round_started']);
  first.click();
  assert.equal(app.state.eventJournal.events.length, 1, 'A round whose images are still loading is not choosable');
  assert.equal(first.attributes['aria-disabled'], 'true');
  assert.equal(app.dom.nextBtn.disabled, false, 'A loading round may still be skipped');
  app.intersect(0, 0.6);   // on screen before its image exists: not yet seen
  app.setTime(1300); app.settleImage(0); app.settleImage(1); app.settleImage(2);
  assert.equal(app.state.eventJournal.events.length, 1);
  app.setTime(1500); app.settleImage(3, false);
  const ready = normalize(app.state.eventJournal.events[1]);
  assert.deepEqual(ready, { event_id: 'evt-000002', sequence: 2, type: 'round_ready', round: 1,
    ready_offset_ms: 500, unloaded_card_ids: [failed.dataset.id], layout: null });
  assert.equal(first.attributes['aria-disabled'], 'false');
  app.intersect(1, 0.4);   // below the threshold, e.g. the bottom row on a phone
  app.intersect(3, 1);     // failed image: on screen but nothing to see
  app.setTime(2000); app.setHidden(true);
  app.intersect(2, 0.9);   // the observer may fire in a background tab
  app.setTime(4000); app.setHidden(false);
  app.setTime(4700); first.click();
  app.setTime(4900); second.click();  // a direct click is evidence that a rendered card was seen
  app.setTime(6500); app.nextRound();
  const closed = normalize(app.state.eventJournal.events[4]);
  assert.deepEqual(normalize(app.state.eventJournal.events.slice(2, 4).map(event => event.t_ms)), [3200, 3400]);
  assert.deepEqual(closed, { event_id: 'evt-000005', sequence: 5, type: 'round_completed', round: 1,
    duration_ms: 5500, hidden_ms: 2000, seen_card_ids: [first.dataset.id, third.dataset.id, second.dataset.id],
    hover: null });
  assert(app.observers[0].disconnected, 'A closed round stops observing its cards');
  app.paintRound();         // round 2 opens at 6900 with cached images
  app.setTime(7000); app.setHidden(true);
  app.setTime(9000); app.setHidden(false);
  const fresh = app.dom.cardGrid.children.find(card => !card.classList.contains('selected'));
  app.setTime(9200); fresh.click();
  app.setTime(9500);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  const events = JSON.parse(app.requests[0].body).journal.events;
  const last = events[events.length - 2];
  assert.deepEqual(events.slice(5, 8).map(event => [event.type, event.ready_offset_ms, event.t_ms]),
    [['round_started', undefined, undefined], ['round_ready', 0, undefined], ['card_selected', undefined, 2300]]);
  assert.deepEqual(last, { event_id: 'evt-000009', sequence: 9, type: 'round_completed', round: 2,
    duration_ms: 2600, hidden_ms: 2000, seen_card_ids: [fresh.dataset.id], hover: null },
    'Time in a hidden tab is reported, not silently counted as deliberation');
}

async function readinessTimeout() {
  const app = setup();
  app.drawRound(); app.paintRound({ images: 'none' });
  app.setTime(1000 + 8000);
  await app.runTimer(8000);
  const ready = app.state.eventJournal.events[1];
  assert.equal(ready.type, 'round_ready');
  assert.equal(ready.ready_offset_ms, 8000);
  assert.deepEqual(normalize(ready.unloaded_card_ids), normalize(app.state.currentRoundCards.map(card => card.id)));
  app.settleImage(0);
  assert.equal(app.state.eventJournal.events.length, 2, 'Readiness is recorded once per round');
  app.dom.cardGrid.children[0].click();
  assert.equal(app.state.eventJournal.events[2].t_ms, 0);
}

async function behaviourSignals() {
  const app = setup({ hover: true });
  app.drawRound(); app.paintRound({ images: 'none' });
  const cards = app.dom.cardGrid.children;
  // Two rows of two, as a phone grid would lay four cards out.
  cards.forEach((card, index) => Object.assign(card, { offsetTop: index < 2 ? 0 : 120,
    offsetLeft: (index % 2) * 100, offsetWidth: 90 }));
  cards[1].listeners.pointerenter({ pointerType: 'mouse' });  // resting on a card while images load
  cards[1].hovered = true;
  app.setTime(1200); cards.forEach((_, index) => app.settleImage(index));
  assert.deepEqual(normalize(app.state.eventJournal.events[1].layout), [[0, 0], [0, 1], [1, 0], [1, 1]]);
  app.setTime(1500); cards[1].listeners.pointerleave();
  cards[1].hovered = false;
  app.setTime(1600); cards[2].listeners.pointerenter({ pointerType: 'touch' });  // a touch is not a hover
  app.setTime(1700); cards[0].listeners.pointerenter({ pointerType: 'mouse' });
  cards[0].hovered = true;
  app.setTime(1900); cards[0].listeners.pointerdown({ pointerType: 'mouse' }); cards[0].listeners.click({});
  // An orientation change or drawer changes the layout before the next choice.
  cards.forEach((card, index) => Object.assign(card, { offsetTop: index * 120, offsetLeft: 0 }));
  app.setTime(2000); cards[3].listeners.pointerdown({ pointerType: 'touch' }); cards[3].listeners.click({});
  app.setTime(2100); cards[3].listeners.keydown({ key: 'Enter', preventDefault() {} });
  app.setTime(2400); app.setHidden(true);   // leaving the tab ends the open hover
  app.setTime(3000); app.setHidden(false);
  app.setTime(3200); app.nextRound();
  const events = normalize(app.state.eventJournal.events);
  assert.deepEqual(events.filter(event => event.type.startsWith('card_')).map(event => [event.type, event.input]),
    [['card_selected', 'mouse'], ['card_selected', 'touch'], ['card_deselected', 'keyboard']]);
  assert.deepEqual(events.filter(event => event.type.startsWith('card_')).map(event => event.layout),
    [[[0, 0], [0, 1], [1, 0], [1, 1]], [[0, 0], [1, 0], [2, 0], [3, 0]], [[0, 0], [1, 0], [2, 0], [3, 0]]],
    'Each selection and revision records its actual layout, separately from readiness');
  assert.deepEqual(events.find(event => event.type === 'round_completed').hover,
    [[1, 0, 300], [0, 500, 1200], [0, 1800, 2000]],
    'Confirmed hover resumes on return, excluding every hidden millisecond and touch');
  app.paintRound();
  app.setTime(4000);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  process.stdout.write(JSON.parse(app.requests[0].body).journal ? app.requests[0].body : '');
}

async function mixedDisplay() {
  const path = require('node:path');
  const data = name => JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', name), 'utf8'));
  const features = data('display-features-v1.json').cards;
  for (const [share, policy] of [[0, 'norn.display.random.v1'], [1, 'norn.display.mixed.v1']]) {
    const app = setup({ config: { displayMixedShare: share } });
    app.drawRound(); app.paintRound();
    assert.equal(app.state.eventJournal.events[0].display.policy, policy, 'displayMixedShare decides the arm');
  }
  const app = setup();
  app.state.allCards = data('cards.json').map(card => ({ id: card.id, url: card.url }));
  app.state.displayFeatures = new Map(Object.entries(features));
  app.state.displayPolicy = 'norn.display.mixed.v1';
  app.state.cardsPerRound = { min: 4, max: 6 };
  for (let round = 1; round <= 10; round++) {
    app.drawRound(); app.paintRound();
    const started = app.state.eventJournal.events.filter(event => event.type === 'round_started').pop();
    const roles = normalize(started.display.roles);
    assert.equal(roles.length, started.card_ids.length);
    assert.equal(roles.filter(role => role === 'predicted').length, Math.floor(roles.length / 2));
    // A visitor who keeps the cards with a positive first pixel component.
    app.state.currentRoundCards.forEach((card, index) => {
      if (features[card.id][4] > 0) { app.setTime(app.time() + 150); app.dom.cardGrid.children[index].click(); }
    });
    app.setTime(app.time() + 400);
  }
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  process.stdout.write(app.requests[0].body);
}

async function researchDisplay() {
  const path = require('node:path');
  const deck = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', 'decks', 'pamela-v1.json'), 'utf8'));
  const app = setup({ config: { deck: 'pamela-v1' } });
  app.state.allCards = deck.cards.map(card => ({ id: card.id, url: card.url }));
  app.state.displayFeatures = new Map(deck.cards.map(card => [card.id, card.x2]));
  app.state.deckLabel = `${deck.deck}@${deck.deck_version}`;
  app.state.displayPolicy = 'norn.display.mixed.v2';
  app.state.cardsPerRound = { min: 4, max: 6 };
  for (let round = 1; round <= 10; round++) {
    app.drawRound(); app.paintRound();
    const started = app.state.eventJournal.events.filter(event => event.type === 'round_started').pop();
    assert.equal(started.display.deck, app.state.deckLabel);
    const roles = normalize(started.display.roles);
    assert.equal(roles.filter(role => role === 'predicted').length, Math.floor(roles.length / 2));
    assert.equal(roles.filter(role => role.startsWith('contrast_')).length, 2);
    // A visitor who keeps the cards with a positive first content component.
    app.state.currentRoundCards.forEach((card, index) => {
      if (app.state.displayFeatures.get(card.id)[1] > 0) { app.setTime(app.time() + 150); app.dom.cardGrid.children[index].click(); }
    });
    app.setTime(app.time() + 400);
  }
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  process.stdout.write(app.requests[0].body);
}

// The same page with a deck that carries a population context (argv: a JSON file with
// deck_version and context); this visitor also always keeps the first card on screen.
async function researchDisplayWithContext() {
  const path = require('node:path');
  const deck = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', 'decks', 'pamela-v1.json'), 'utf8'));
  const extra = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  const app = setup({ config: { deck: 'pamela-v1' } });
  app.state.allCards = deck.cards.map(card => ({ id: card.id, url: card.url }));
  app.state.displayFeatures = new Map(deck.cards.map(card => [card.id, card.x2]));
  app.state.deckLabel = `${deck.deck}@${extra.deck_version}`;
  app.state.slotContext = app.slotContext(extra.context);
  app.state.displayPolicy = 'norn.display.mixed.v2';
  app.state.cardsPerRound = { min: 4, max: 6 };
  for (let round = 1; round <= 10; round++) {
    app.drawRound(); app.paintRound();
    app.state.currentRoundCards.forEach((card, index) => {
      if (index === 0 || app.state.displayFeatures.get(card.id)[1] > 0) {
        app.setTime(app.time() + 150); app.dom.cardGrid.children[index].click();
      }
    });
    app.setTime(app.time() + 400);
  }
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  process.stdout.write(app.requests[0].body);
}

// A consented research visit: a partial snapshot as each round starts, then the reading with the same session key.
async function researchPartialVisits() {
  const path = require('node:path');
  const deck = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', 'decks', 'pamela-v1.json'), 'utf8'));
  const app = setup({ pilot: true, config: { deck: 'pamela-v1' } });
  app.state.allCards = deck.cards.map(card => ({ id: card.id, url: card.url }));
  app.state.displayFeatures = new Map(deck.cards.map(card => [card.id, card.x2]));
  app.state.deckLabel = `${deck.deck}@${deck.deck_version}`;
  app.state.displayPolicy = 'norn.display.random.v2';
  app.state.cardsPerRound = { min: 4, max: 6 };
  app.state.consent = true;
  for (let round = 1; round <= 3; round++) {
    app.drawRound(); app.paintRound();
    app.setTime(app.time() + 150); app.dom.cardGrid.children[0].click();
    app.setTime(app.time() + 400);
    await new Promise(resolve => setImmediate(resolve));  // allow each background checkpoint to settle
  }
  await new Promise(resolve => setImmediate(resolve));  // the background snapshots go out first
  const partial = app.requests.filter(request => request.url.endsWith('/record/partial')).map(request => request.body);
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  const reading = app.requests.find(request => request.url.endsWith('/analyze'));
  await new Promise(resolve => setImmediate(resolve));
  const after = app.requests.filter(request => request.url.endsWith('/record/partial')).length;
  process.stdout.write(JSON.stringify({ partial, reading: reading.body, after }));
}

async function partialPrivacyBoundaries() {
  const path = require('node:path');
  const deck = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', 'decks', 'pamela-v1.json'), 'utf8'));
  function visit(consent) {
    const app = setup({ pilot: true, config: { deck: 'pamela-v1' } });
    app.state.allCards = deck.cards.map(card => ({ id: card.id, url: card.url }));
    app.state.displayFeatures = new Map(deck.cards.map(card => [card.id, card.x2]));
    app.state.deckLabel = `${deck.deck}@${deck.deck_version}`;
    app.state.displayPolicy = 'norn.display.random.v2';
    app.state.consent = consent;
    app.drawRound(); app.paintRound();
    return app;
  }
  const privateVisit = visit(false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(privateVisit.requests.length, 0, 'Private play must send no archival request');
  assert.equal(privateVisit.state.sessionKey, null, 'Private play needs no archival session key');
  for (const status of [200, 503]) {
    const app = visit(true); // the snapshot has been captured but not dispatched yet
    app.responses.push({ status, data: status === 200 ? { withdrawn: true } : { error: 'Unknown' } });
    await app.forgetVisitor();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.requests.filter(r => r.url.endsWith('/record/partial')).length, 0,
      'Withdrawal must invalidate a snapshot waiting for configuration');
    assert.equal(app.state.consent, false, 'Even an unknown withdrawal result pauses collection');
    if (status === 503) {
      app.responses.push({ status: 200, data: { withdrawn: true } });
      await app.forgetVisitor();
      assert.equal(app.requests.filter(r => r.url.endsWith('/record/withdraw')).length, 2,
        'An uncertain withdrawal must retain its key for a retry');
    }
    app.drawRound(); app.paintRound();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.requests.filter(r => r.url.endsWith('/record/partial')).length, 0,
      'Continuing this visit after withdrawal must not silently create a new identity');
  }
  const otherTab = visit(true);
  otherTab.onVisitorStorageChange({ key: 'norn.visitor.v1', oldValue: 'previous-key', newValue: null });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(otherTab.state.consent, false);
  assert.equal(otherTab.requests.length, 0, 'Another tab withdrawing invalidates queued snapshots too');
  const replaced = visit(true);
  replaced.onVisitorStorageChange({ key: 'norn.visitor.v1', oldValue: 'previous-key', newValue: 'new-tab-key' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(replaced.state.consent, false);
  assert.equal(replaced.requests.length, 0, 'Do not attribute an old visit to a replacement identity');
}

function checkpointVisit(options = {}) {
  const path = require('node:path');
  const deck = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'data', 'decks', 'pamela-v1.json'), 'utf8'));
  const app = setup({ pilot: true, config: { deck: 'pamela-v1' }, ...options });
  app.state.allCards = deck.cards.map(card => ({ id: card.id, url: card.url }));
  app.state.displayFeatures = new Map(deck.cards.map(card => [card.id, card.x2]));
  app.state.deckLabel = `${deck.deck}@${deck.deck_version}`;
  app.state.displayPolicy = 'norn.display.random.v2';
  app.state.consent = true;
  return app;
}

async function activeRoundCheckpoints() {
  const app = checkpointVisit({ observer: true, hover: true });
  const settle = () => new Promise(resolve => setImmediate(resolve));
  app.responses.push({ status: 200, data: { stored: true } });
  app.drawRound(); app.paintRound();
  await settle();
  assert.equal(app.requests.length, 1, 'The initial deal is checkpointed without waiting for interaction');
  app.setTime(1700); app.dom.cardGrid.children[0].click();
  app.setTime(1800); app.dom.cardGrid.children[0].click();
  app.setTime(1900); app.dom.cardGrid.children[0].click();
  app.setTime(2000); app.dom.cardGrid.children[0].listeners.pointerenter({ pointerType: 'mouse' });
  assert.equal(app.requests.length, 1, 'Rapid revisions coalesce without blocking the choice handlers');
  const rawHover = normalize(app.state.roundAttention.hover);
  const openHover = normalize(app.state.roundAttention.hoverOpen);
  app.setTime(2500);
  app.responses.push({ status: 200, data: { stored: true } });
  await app.runTimer(500); await settle();
  const first = JSON.parse(app.requests.at(-1).body);
  assert.equal(first.journal.events.at(-1).type, 'card_selected');
  assert.equal(first.journal.events.filter(e => e.type === 'round_completed').length, 0,
    'Saving a checkpoint must not invent a stop or end the round');
  assert.deepEqual(normalize(first.open_round.hover), [[0, 1000, 1500]]);
  assert.equal(first.open_round.elapsed_ms, 1500);
  assert.equal(first.open_round.hidden_ms, 0);
  assert.deepEqual(normalize(app.state.roundAttention.hover), rawHover);
  assert.deepEqual(normalize(app.state.roundAttention.hoverOpen), openHover,
    'Checkpointing must not close or otherwise mutate live attention');
  app.setTime(13000);
  app.responses.push({ status: 200, data: { stored: true } });
  await app.runTimer(10000); await settle();
  const pulse = JSON.parse(app.requests.at(-1).body);
  assert.deepEqual(pulse.journal, first.journal, 'A dwell update needs no synthetic journal event');
  assert.equal(pulse.open_round.elapsed_ms, 12000);
  app.setTime(15000);
  app.responses.push({ status: 200, data: { stored: true } });
  app.setHidden(true); await settle();
  const hidden = JSON.parse(app.requests.at(-1).body);
  assert.equal(app.requests.at(-1).keepalive, true, 'A small lifecycle checkpoint uses keepalive');
  assert.equal(hidden.open_round.elapsed_ms, 14000);
  assert.equal(hidden.open_round.hidden_ms, 0);
  assert.equal(app.timers.filter(timer => timer.delay === 10000).length, 0,
    'No periodic requests while the page is hidden');
  app.setTime(17000);
  app.responses.push({ status: 200, data: { stored: true } });
  app.windowListeners.pagehide(); await settle();
  const leaving = JSON.parse(app.requests.at(-1).body);
  assert.equal(leaving.open_round.hidden_ms, 2000);
  assert.equal(leaving.open_round.elapsed_ms, 16000);
  assert.deepEqual(leaving.open_round.hover, hidden.open_round.hover,
    'Background dwell cannot become extra hover');
  assert.equal(app.state.roundOpen, true);
  const fractional = checkpointVisit({ hover: true });
  fractional.responses.push({ status: 200, data: { stored: true } });
  fractional.drawRound(); fractional.paintRound({ images: 'pending' }); await settle();
  fractional.setTime(1000.6);
  fractional.dom.cardGrid.children.forEach((_, index) => fractional.settleImage(index));
  fractional.setTime(1000.7);
  fractional.dom.cardGrid.children[0].listeners.pointerenter({ pointerType: 'mouse' });
  fractional.setTime(1002.05);
  fractional.responses.push({ status: 200, data: { stored: true } });
  fractional.savePartialVisit(); await settle();
  const fractionalBefore = JSON.parse(fractional.requests.at(-1).body);
  fractional.setTime(1002.1);
  fractional.dom.cardGrid.children[0].listeners.pointerleave();
  fractional.responses.push({ status: 200, data: { stored: true } });
  fractional.nextRound(); fractional.paintRound(); await settle();
  const fractionalAfter = JSON.parse(fractional.requests.at(-1).body);
  assert.deepEqual(fractionalBefore.open_round.hover, [[0, 0, 1]]);
  assert.deepEqual(fractionalAfter.journal.events.find(e => e.type === 'round_completed').hover, [[0, 0, 1]],
    'Fractional clocks must not lose a measured hover segment when the real round closes');
  process.stdout.write(JSON.stringify({ checkpoints: [first, pulse, hidden, leaving],
    fractional_checkpoints: [fractionalBefore, fractionalAfter] }));
}

async function checkpointQueueAndExit() {
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const app = checkpointVisit();
  let resolveFirst;
  app.responses.push({ status: 200, data: { stored: true }, wait: new Promise(resolve => { resolveFirst = resolve; }) });
  app.drawRound(); app.paintRound(); await settle();
  app.setTime(1600); app.dom.cardGrid.children[0].click();
  await app.runTimer(500); await settle();
  app.setTime(1800); app.dom.cardGrid.children[1].click();
  await app.runTimer(500); await settle();
  assert.equal(app.requests.length, 1, 'An outstanding request has only one replaceable pending checkpoint');
  assert.equal(JSON.parse(app.state.partialPending.body).journal.events.at(-1).card_id,
    app.dom.cardGrid.children[1].dataset.id);
  resolveFirst(); await settle();
  assert.equal(app.requests.length, 2);
  assert.equal(JSON.parse(app.requests[1].body).journal.events.filter(e => e.type === 'card_selected').length, 2,
    'When transport frees up, send the latest complete prefix, not an old revision');
  const exit = checkpointVisit();
  exit.responses.push({ status: 200, data: { stored: true }, wait: new Promise(() => {}) });
  exit.drawRound(); exit.paintRound(); await settle();
  exit.setTime(1800); exit.dom.cardGrid.children[0].click();
  exit.responses.push({ status: 200, data: { stored: true } });
  exit.windowListeners.pagehide(); await settle();
  assert.equal(exit.requests.length, 2);
  assert.equal(exit.requests[0].signal.aborted, true,
    'The browser cancels its older request before trying the latest exit snapshot');
  assert.equal(exit.requests[1].keepalive, true);
  assert.equal(JSON.parse(exit.requests[1].body).journal.events.at(-1).type, 'card_selected');
  const large = checkpointVisit();
  large.responses.push({ status: 200, data: { stored: true } });
  large.drawRound(); large.paintRound(); await settle();
  for (let index = 0; index < 500; index++) {
    large.setTime(1600 + index); large.dom.cardGrid.children[0].click();
  }
  large.responses.push({ status: 200, data: { stored: true } });
  large.windowListeners.pagehide(); await settle();
  assert(new Blob([large.requests.at(-1).body]).size > 60 * 1024);
  assert.equal(large.requests.at(-1).keepalive, undefined,
    'Oversized whole journals must never enter the browser keepalive quota');
}

async function hoverReturnNeedsConfirmation() {
  for (const confirmation of ['pointer-left', 'unknown', 'closed-round']) {
    const app = setup({ hover: true });
    app.drawRound(); app.paintRound();
    const card = app.dom.cardGrid.children[0];
    app.setTime(1600); card.hovered = true; card.listeners.pointerenter({ pointerType: 'mouse' });
    app.setTime(1800); app.setHidden(true);
    if (confirmation === 'pointer-left') card.hovered = false;
    if (confirmation === 'unknown') card.matches = undefined;
    if (confirmation === 'closed-round') app.nextRound();
    app.setTime(2200); app.setHidden(false);
    app.setTime(2500); app.nextRound();
    const closed = app.state.eventJournal.events.find(event => event.type === 'round_completed');
    assert.deepEqual(normalize(closed.hover), [[0, 600, 800]],
      `A hidden pointer move, missing confirmation or ended round cannot create a phantom segment: ${confirmation}`);
  }
}

async function pilotNewImages() {
  const deck = fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),
    'data', 'probe-new-material-v1.json'), 'utf8');
  for (const consent of [false, true]) {
    const app = setup({ pilot: true });
    app.dom.ambientAudio.play = async () => {};  // the entrance also starts ambient sound
    app.responses.push({ status: 200, raw: deck });
    app.startTest(consent);
    await new Promise(resolve => setImmediate(resolve));
    app.paintRound();
    app.dom.cardGrid.children[0].click();
    app.setTime(2000);
    if (!consent) {
      app.responses.length = 0;
      app.responses.push({ status: 200, data: successfulAnalysis });
      await app.revealDestiny();
      assert.equal(app.requests.length, 1, 'Without consent the new images are never fetched or shown');
      assert(!('probe' in JSON.parse(app.requests[0].body)));
      continue;
    }
    await app.revealDestiny();   // seals the main journal and opens the first new-image round
    assert.equal(app.requests.length, 1, 'Only the deck was fetched: no reading before the new images');
    const main = app.state.probe.main.journal;
    assert(Object.isFrozen(main) && main.events[main.events.length - 1].type === 'session_completed');
    for (let round = 1; round <= 4; round++) {
      app.paintRound();
      assert.equal(app.dom.roundNum.textContent, `${round}/4`);
      const shown = app.state.currentRoundCards;
      assert.equal(shown.length, 4);
      assert.equal(new Set(shown.map(card => card.scene)).size, 2, 'Two whole scenes per round');
      app.dom.cardGrid.children[round % 4].click();
      app.setTime(2000 + round * 1000);
      if (round < 4) app.nextRound();
    }
    assert.equal(app.dom.nextBtn.disabled, true, 'There is no fifth set');
    app.responses.push({ status: 200, data: successfulAnalysis });
    await app.revealDestiny();
    const payload = JSON.parse(app.requests[1].body);
    assert.deepEqual(Object.keys(payload).sort(), ['consent', 'journal', 'language', 'probe', 'request_id', 'visitor']);
    assert.equal(app.state.eventJournal, main, 'The main journal is back in place after the new images');
    assert.equal(String(app.dom.roundNum.textContent), '1');
    process.stdout.write(JSON.stringify(payload));
  }
}

async function pilotSkipAndRetry() {
  const deck = fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),
    'data', 'probe-new-material-v1.json'), 'utf8');
  const app = setup({ pilot: true });
  app.dom.ambientAudio.play = async () => {};
  app.responses.push({ status: 200, raw: deck });
  app.startTest(true);
  await new Promise(resolve => setImmediate(resolve));
  app.paintRound();
  app.dom.cardGrid.children[1].click();
  app.setTime(2000);
  await app.revealDestiny();
  app.paintRound();
  app.dom.cardGrid.children[2].click();
  app.setTime(3000);
  await app.revealDestiny();   // reveal after one set of new images; the network then fails
  assert.equal(app.state.probe.active, false);
  const first = JSON.parse(app.requests[1].body);
  assert.equal(first.probe.events.filter(event => event.type === 'round_started').length, 1);
  assert.equal(first.probe.events[first.probe.events.length - 1].type, 'session_completed');
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.retryAnalysis();
  const retried = JSON.parse(app.requests[2].body);
  assert.deepEqual(retried, first, 'A retry resends the same main journal and probe');
  process.stdout.write(JSON.stringify(first));
}

async function optionalRating() {
  const app = setup();
  app.drawRound(); app.paintRound();
  app.dom.cardGrid.children[0].click(); app.dom.cardGrid.children[1].click(); app.dom.cardGrid.children[1].click();
  app.dom.cardGrid.children[2].click();
  const finalIds = [app.dom.cardGrid.children[0].dataset.id, app.dom.cardGrid.children[2].dataset.id];
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.revealDestiny();
  assert.equal(app.dom.feedbackPanel.hidden, true, 'The question waits until the reading has appeared');
  app.runFrames(() => app.dom.feedbackPanel.hidden === false);
  assert.equal(app.dom.feedbackRating.children.length, 5);
  assert.deepEqual(app.dom.feedbackClosest.children.map(button => button.dataset.value), finalIds,
    'Only cards in the sealed final selection can be named');
  assert.equal(app.dom.feedbackSave.disabled, true);
  assert.equal(app.dom.exportRecord.disabled, false, 'Rating is never required for export');
  const journal = JSON.stringify(app.state.eventJournal);
  app.skipFeedback();
  assert.equal(app.dom.feedbackPanel.hidden, true);
  assert.equal(app.requests.length, 1, 'Skipping sends nothing');
  app.dom.feedbackPanel.hidden = false;
  app.dom.feedbackRating.children[3].click();
  assert.equal(app.dom.feedbackRating.children[3].attributes['aria-checked'], 'true');
  assert.equal(app.dom.feedbackSave.disabled, false);
  app.dom.feedbackRating.children[3].click();
  assert.equal(app.dom.feedbackSave.disabled, true, 'Choosing the same score again clears it');
  app.dom.feedbackRating.children[3].click();
  app.dom.feedbackClosest.children[1].click();
  app.responses.push({ status: 200, data: { saved: true } });
  await app.submitFeedback();
  assert.equal(app.requests[1].url, '/api/feedback');
  assert.equal(app.requests[1].method, 'POST');
  assert.deepEqual(JSON.parse(app.requests[1].body),
    { session_id: 'session-123', reading_rating: 4, closest_card_id: finalIds[1] });
  assert(app.dom.feedbackStatus.textContent.includes('已记下'));
  assert.equal(JSON.stringify(app.state.eventJournal), journal, 'A rating never enters the sealed journal');
  app.responses.push({ status: 404, data: { error: 'expired' } });
  await app.submitFeedback();
  assert(app.dom.feedbackStatus.textContent.includes('过期'));
  assert.equal(app.dom.feedbackSave.disabled, false, 'A failed save can be retried or ignored');
}

async function ratingNeedsAReading() {
  const app = setup();
  app.drawRound(); app.paintRound(); app.dom.cardGrid.children[0].click();
  app.responses.push({ status: 503, data: { error: 'Unavailable', status: 'provider_unavailable' } });
  await app.revealDestiny();
  app.submitFeedback();
  assert.equal(app.dom.feedbackPanel.hidden, true);
  assert.equal(app.requests.length, 1, 'Without a confirmed session there is nothing to rate');
  app.responses.push({ status: 200, data: successfulAnalysis });
  await app.retryAnalysis();
  app.runFrames(() => app.dom.feedbackPanel.hidden === false);
  assert.equal(app.dom.feedbackClosestBlock.hidden, true, 'One selected card is not a choice');
}

const cases = { mixedDisplay, researchDisplay, researchDisplayWithContext, researchPartialVisits, partialPrivacyBoundaries, activeRoundCheckpoints, checkpointQueueAndExit, workflow, emptySelection, eventLimit, roundLimit, idempotentDialogueRetries, terminalAndAdmissionRetries, randomDisplayIgnoresLegacyAxes, readinessVisibilityAndIdle, readinessTimeout, behaviourSignals, hoverReturnNeedsConfirmation, pilotNewImages, pilotSkipAndRetry, optionalRating, ratingNeedsAReading };
cases[process.argv[2]]().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional; install it to execute the frontend fixture")
class FrontendEventTests(unittest.TestCase):
    def test_javascript_syntax(self):
        result = subprocess.run([shutil.which("node"), "--check", str(APP)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_event_handlers_and_request_boundaries(self):
        for scenario in ("workflow", "emptySelection", "eventLimit", "roundLimit", "idempotentDialogueRetries", "terminalAndAdmissionRetries", "randomDisplayIgnoresLegacyAxes", "readinessVisibilityAndIdle", "readinessTimeout", "hoverReturnNeedsConfirmation", "optionalRating", "ratingNeedsAReading"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), scenario],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pilot_new_images_reach_the_server_as_a_valid_probe(self):
        import json
        from norn.events import replay_events
        from norn.probe import probe_rows, validate_probe
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "pilotNewImages"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        replay_events(payload["journal"])
        probe, _ = validate_probe(payload["probe"])
        self.assertEqual([len(row["shown"]) for row in probe_rows(probe)], [4, 4, 4, 4])
        self.assertEqual(sum(len(row["picks"]) for row in probe_rows(probe)), 4)

    def test_an_early_reveal_and_a_retry_keep_a_valid_partial_probe(self):
        import json
        from norn.probe import probe_rows, validate_probe
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "pilotSkipAndRetry"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        probe, _ = validate_probe(json.loads(result.stdout)["probe"])
        self.assertEqual(len(probe_rows(probe)), 1)

    def test_mixed_rounds_from_the_page_match_the_engine_rule(self):
        import json
        from norn.core import engine_module
        from norn.events import replay_events
        display = engine_module("display_policy")
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "mixedDisplay"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = replay_events(json.loads(result.stdout)["journal"])["journal"]
        check = display.verify(journal)
        self.assertEqual((check["rounds"], check["predicted_rounds_checked"], check["failures"]), (10, 10, []))
        catalog = engine_module("choice_model").load_catalog()
        positive = {card for i, card in enumerate(catalog.ids) if catalog.features[i][4] > 0}
        late = [card for event in journal["events"] if event["type"] == "round_started" and event["round"] > 5
                for card, role in zip(event["card_ids"], event["display"]["roles"]) if role == "predicted"]
        self.assertGreater(sum(card in positive for card in late) / len(late), 0.8,
                           "After a few rounds the predicted cards follow what the visitor kept")

    def test_research_deck_rounds_from_the_page_match_the_engine_rule(self):
        import json
        from norn.core import engine_module
        from norn.events import replay_events
        research = engine_module("research_deck")
        deck = research.load_deck("pamela-v1")
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "researchDisplay"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = replay_events(json.loads(result.stdout)["journal"], cards=deck.cards)["journal"]
        check = research.verify(journal, deck)
        self.assertEqual((check["rounds"], check["mixed_rounds_checked"], check["failures"]), (10, 10, []))

    def test_research_deck_with_population_context_matches_the_engine_rule(self):
        import json
        import tempfile
        from copy import deepcopy
        from norn.core import engine_module
        from norn.events import replay_events
        research, model = engine_module("research_deck"), engine_module("research_model")
        document = deepcopy(research.load_deck("pamela-v1").document)
        offsets = {str(m): [1.2 - 2.4 * k / (m - 1) for k in range(m)] for m in (4, 5, 6)}
        document["context"] = {"schema_version": research.CONTEXT_SCHEMA_VERSION, "offsets": offsets,
                               "gamma": -0.8, "provenance": {"purpose": "test"}}
        document.pop("deck_version")
        document["deck_version"] = engine_module("choice_model")._hash(document)
        deck = research.Deck(document)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"deck_version": deck.version, "context": document["context"]}, handle)
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "researchDisplayWithContext",
                                 handle.name], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = replay_events(json.loads(result.stdout)["journal"], cards=deck.cards)["journal"]
        check = research.verify(journal, deck)
        self.assertEqual((check["rounds"], check["mixed_rounds_checked"], check["failures"]), (10, 10, []))
        rows = research.observations(journal)
        aware, plain = model.Session(deck.x2, deck.context), model.Session(deck.x2)
        for row in rows:
            aware.add(row)
            plain.add(row)
        self.assertGreater(max(abs(a - b) for a, b in zip(aware.l2, plain.l2)), 1e-3,
                           "The context must change the fit this test exercises")

    def test_consented_research_visit_keeps_a_partial_record_until_its_reading(self):
        import json
        import tempfile
        from pathlib import Path
        from norn.actor_state import new_actor_service
        from norn.collection import SQLiteCollector
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "researchPartialVisits"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sent = json.loads(result.stdout)
        self.assertEqual(len(sent["partial"]), 3, "One snapshot as each round starts")
        self.assertEqual(sent["after"], 3, "Nothing partial after the reveal")
        bodies = [json.loads(body) for body in sent["partial"]]
        reading = json.loads(sent["reading"])
        self.assertEqual({body["session_key"] for body in bodies}, {reading["session_key"]})
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCollector(Path(directory) / "records.sqlite3")
            self.addCleanup(store.close)
            service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
            service.collection = store
            for number, body in enumerate(bodies, 1):
                self.assertEqual(service.record_partial(body), {"stored": True})
                [record] = store.page()
                self.assertEqual(record["schema_version"], "norn.partial.v1")
                self.assertEqual(sum(e["type"] == "round_started" for e in record["journal"]["events"]), number)
            service.analyze(reading)
            [record] = store.page()
            self.assertEqual(record["schema_version"], "norn.record.v2")
            self.assertEqual((record["visit"], record["experiments"]["session_key"]), (1, reading["session_key"]))

    def test_private_and_withdrawn_visits_never_dispatch_queued_snapshots(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "partialPrivacyBoundaries"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_current_round_choices_dwell_and_hover_checkpoint_without_closing(self):
        import json
        import tempfile
        from pathlib import Path
        from norn.actor_state import new_actor_service
        from norn.collection import SQLiteCollector
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "activeRoundCheckpoints"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        checkpoints = report["checkpoints"]
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCollector(Path(directory) / "records.sqlite3")
            self.addCleanup(store.close)
            service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
            service.collection = store
            for checkpoint in checkpoints:
                self.assertEqual(service.record_partial(checkpoint), {"stored": True})
                [record] = store.page()
                self.assertEqual(record["journal"], checkpoint["journal"])
                self.assertEqual(record["open_round"], checkpoint["open_round"])
            self.assertEqual(checkpoints[-1]["open_round"]["hidden_ms"], 2000)
            fractional = SQLiteCollector(Path(directory) / "fractional.sqlite3")
            self.addCleanup(fractional.close)
            service.collection = fractional
            for checkpoint in report["fractional_checkpoints"]:
                self.assertEqual(service.record_partial(checkpoint), {"stored": True})

    def test_checkpoint_transport_coalesces_and_prioritizes_bounded_exit(self):
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "checkpointQueueAndExit"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_behaviour_signals_from_the_page_pass_python_replay(self):
        import json
        from norn.events import EVENTS_SCHEMA_VERSION, replay_events
        result = subprocess.run([shutil.which("node"), "-e", NODE_FIXTURE, str(APP), "behaviourSignals"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = json.loads(result.stdout)["journal"]
        self.assertEqual(journal["schema_version"], EVENTS_SCHEMA_VERSION)
        first, second = replay_events(journal)["round_evidence"]
        self.assertEqual((first["layout"], first["hover"]), ([[0, 0], [0, 1], [1, 0], [1, 1]],
                                                             [[1, 0, 300], [0, 500, 1200], [0, 1800, 2000]]))
        self.assertEqual([row["layout"] for row in first["choice_layouts"]],
                         [[[0, 0], [0, 1], [1, 0], [1, 1]], [[0, 0], [1, 0], [2, 0], [3, 0]],
                          [[0, 0], [1, 0], [2, 0], [3, 0]]])
        self.assertEqual(second["hover"], [])


if __name__ == "__main__":
    unittest.main()
