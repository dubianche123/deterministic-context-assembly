"""Execute the shipped pure Jev join; probabilities use the same seen set."""

from pathlib import Path
import shutil
import subprocess
import unittest


MODULE = Path(__file__).resolve().parents[1] / "the-norn-machine" / "frontend" / "play-comparison.js"
FIXTURE = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const window = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), { window });
const merge = window.NornPlayComparison.merge;
const normalize = value => JSON.parse(JSON.stringify(value));
const close = (actual, expected) => assert(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);
function row(round, target = 'a', ids = ['a', 'b']) {
  const lane = p => ({ status: 'ok', probabilities: p, ranks: [1, 2], order: ['a', 'b'],
    scores: p.slice(), target_nll: target === null ? null : -Math.log(p[ids.indexOf(target)]),
    top1_credit: target === null ? null : 1, zero_probability_target: false });
  return { round, cutoff_rounds: round - 1, prefix_hash: `prefix-${round}`,
    candidate_ids: ids, picks: target === null ? [] : [target], target, scored: target !== null,
    lanes: { baseline: lane([0.8, 0.2]), learning: lane([0.6, 0.4]), jev: { status: 'not_run' } },
    cumulative: { n: round, baseline_nll: 100 + round, learning_nll: 200 + round } };
}
function report(rows = [row(1)]) {
  return { schema_version: 'norn.pamela-comparison.v1', mode: 'prequential',
    lanes: { baseline: { status: 'ok' }, learning: { status: 'ok' }, jev: { status: 'not_run' } },
    rounds: rows, summary: { n: rows.length, baseline_nll: 123, learning_nll: 234 }, provider_calls: 0 };
}
function prediction(round = 1, extra = {}) {
  return { status: 'ok', round, cutoff_rounds: round - 1, prefix_hash: `prefix-${round}`,
    candidate_ids: ['a', 'b'], probabilities: [0.7, 0.3], ...extra };
}
function prefixBinding() {
  for (const change of [{ prefix_hash: 'other' }, { round: 2 }, { cutoff_rounds: 19 }]) {
    const result = merge(report(), new Map([[1, prediction(1, change)]]));
    assert.equal(result.rounds[0].lanes.jev.status, 'failed');
    assert.equal(result.rounds[0].lanes.jev.reason, 'prefix_mismatch');
    assert.equal(result.jev_coverage.invalid_rounds, 1);
    assert.equal(result.rounds[0].cumulative.jev_nll, null);
  }
}
function subsetAndReordering() {
  const result = merge(report([row(1, 'a', ['b', 'a'])]), new Map([[1, prediction(1, {
    candidate_ids: ['x', 'a', 'z', 'b', 'y', 'w'], probabilities: [0.1, 0.1, 0.1, 0.3, 0.2, 0.2],
    ranks: [99], target_nll: 999, scores: [-999], order: ['invented'] })]]));
  const lane = result.rounds[0].lanes.jev;
  assert.equal(lane.status, 'ok');
  close(lane.probabilities[0], 0.75); close(lane.probabilities[1], 0.25);
  assert.deepEqual(normalize(lane.ranks), [1, 2]);
  assert.deepEqual(normalize(lane.order), ['b', 'a']);
  close(lane.target_nll, -Math.log(0.25));
  assert.equal(lane.top1_credit, 0);
  close(result.summary.jev_nll, lane.target_nll);
  assert.equal(result.summary.n, 1);
}
function zeroMassAndZeroTarget() {
  const zero = merge(report(), new Map([[1, prediction(1, {
    candidate_ids: ['a', 'b', 'c'], probabilities: [0, 0, 1] })]]));
  assert.equal(zero.rounds[0].lanes.jev.reason, 'zero_eligible_mass');
  assert.equal(zero.summary.jev_nll, null);
  const target = merge(report(), new Map([[1, prediction(1, { probabilities: [0, 1] })]]));
  assert.equal(target.rounds[0].lanes.jev.zero_probability_target, true);
  close(target.summary.jev_nll, -Math.log(1e-12));
}
function rejectsInvalidDistributions() {
  for (const extra of [
    { probabilities: [NaN, 0.3] }, { probabilities: [Infinity, 0] },
    { probabilities: [-0.2, 1.2] }, { probabilities: [0.8, 0.8] },
    { probabilities: [0.7] }, { probabilities: ['0.7', 0.3] }, { probabilities: [true, 0] },
    { candidate_ids: ['a', 'a'], probabilities: [0.5, 0.5] },
    { candidate_ids: ['a', 'c'], probabilities: [0.5, 0.5] },
  ]) {
    const result = merge(report(), new Map([[1, prediction(1, extra)]]));
    assert.equal(result.rounds[0].lanes.jev.status, 'failed', JSON.stringify(extra));
    assert.equal(result.jev_coverage.scored_rounds, 0);
    assert.equal(result.summary.jev_nll, null);
  }
}
function commonDenominator() {
  const rows = [row(1), row(2, 'b'), row(3, null), row(4, 'b'), row(5)];
  const result = merge(report(rows), new Map([[2, prediction(2)], [3, prediction(3)], [5, prediction(5)]]));
  assert.deepEqual(result.rounds.map(r => r.cumulative.n).join(','), '0,1,1,1,2');
  assert.equal(result.jev_coverage.valid_rounds, 3);
  assert.equal(result.jev_coverage.scored_rounds, 2);
  assert.equal(result.jev_coverage.total_scored_rounds, 4);
  assert.equal(result.jev_coverage.curve_scope, 'shared_scored_rounds');
  assert.equal(result.rounds[0].cumulative.baseline_nll, null);
  for (const name of ['baseline', 'learning', 'jev']) {
    close(result.summary[name + '_nll'],
      (result.rounds[1].lanes[name].target_nll + result.rounds[4].lanes[name].target_nll) / 2);
    assert.equal(result.rounds[3].cumulative[name + '_nll'], result.rounds[1].cumulative[name + '_nll']);
  }
  assert.equal(result.summary.n, result.summary.jev_n);
  assert.equal(result.rounds[0].local_cumulative.baseline_nll, 101);
  assert.equal(result.local_summary.baseline_nll, 123);
}
function missingFailedAndUnscored() {
  for (const predictions of [undefined, new Map(), new Map([[1, { status: 'failed' }]])]) {
    const original = report();
    const result = merge(original, predictions);
    assert.notEqual(result.rounds[0].lanes.jev.status, 'ok');
    assert.equal(result.rounds[0].cumulative.baseline_nll, 101);
    assert.equal(result.summary.n, 1);
    assert.equal(result.summary.jev_nll, null);
    assert.equal(result.jev_coverage.scored_rounds, 0);
    assert.equal(result.jev_coverage.curve_scope, 'local_only_no_jev_coverage');
  }
  const unscored = merge(report([row(1, null)]), new Map([[1, prediction()]]));
  assert.equal(unscored.rounds[0].lanes.jev.status, 'ok');
  assert.equal(unscored.rounds[0].lanes.jev.target_nll, null);
  assert.equal(unscored.summary.jev_nll, null);
  assert.equal(unscored.jev_coverage.scored_rounds, 0);
  const tied = merge(report(), new Map([[1, prediction(1, { probabilities: [0.5, 0.5] })]]));
  assert.deepEqual(normalize(tied.rounds[0].lanes.jev.ranks), [1, 1]);
  assert.equal(tied.rounds[0].lanes.jev.top1_credit, 0.5);
}
function noMutationOrNetwork() {
  const original = report(), predictionValue = prediction();
  const before = JSON.stringify(original), predictionBefore = JSON.stringify(predictionValue);
  const freeze = value => { if (value && typeof value === 'object') {
    Object.freeze(value); Object.values(value).forEach(freeze); } return value; };
  const result = merge(freeze(original), new Map([[1, freeze(predictionValue)]]));
  assert.equal(result.live_jev, true);
  assert.equal(result.provider_calls, 0);
  assert.equal(JSON.stringify(original), before);
  assert.equal(JSON.stringify(predictionValue), predictionBefore);
  assert.notEqual(result, original);
  assert.notEqual(result.rounds[0].lanes.baseline, original.rounds[0].lanes.baseline);
  const offline = { ...report(), mode: 'frozen_prefix' };
  assert.deepEqual(normalize(merge(offline, new Map([[1, prediction()]]))), offline);
}
const cases = { prefixBinding, subsetAndReordering, zeroMassAndZeroTarget,
  rejectsInvalidDistributions, commonDenominator, missingFailedAndUnscored, noMutationOrNetwork };
cases[process.argv[2]]();
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional for frontend fixtures")
class PlayComparisonTests(unittest.TestCase):
    def test_live_comparison_contract(self):
        for scenario in ("prefixBinding", "subsetAndReordering", "zeroMassAndZeroTarget",
                         "rejectsInvalidDistributions", "commonDenominator", "missingFailedAndUnscored",
                         "noMutationOrNetwork"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", FIXTURE, str(MODULE), scenario],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
