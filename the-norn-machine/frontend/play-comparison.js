/* Join already-returned play predictions to a local, retrospective comparison.
 * This module never requests a prediction or changes an event/report in place.
 */
(function (global) {
  'use strict';

  const FLOOR = 1e-12;
  const LANES = ['baseline', 'learning', 'jev'];
  const clone = value => Array.isArray(value) ? value.map(clone)
    : value && typeof value === 'object'
      ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)])) : value;
  const idsValid = ids => Array.isArray(ids) && ids.length > 0 && ids.length <= 6
    && ids.every(id => typeof id === 'string' && id.length > 0)
    && new Set(ids).size === ids.length;

  function joinedLane(row, prediction) {
    if (!prediction) return { status: 'not_run', reason: 'missing_precommit' };
    if (prediction.status !== 'ok') return { status: 'failed', reason: 'prediction_unavailable' };
    const reject = reason => ({ status: 'failed', reason });
    if (typeof row.prefix_hash !== 'string' || !row.prefix_hash
        || prediction.prefix_hash !== row.prefix_hash || prediction.round !== row.round
        || prediction.cutoff_rounds !== row.cutoff_rounds) return reject('prefix_mismatch');
    const planned = prediction.candidate_ids, shown = row.candidate_ids;
    const masses = prediction.probabilities;
    if (!idsValid(planned) || !idsValid(shown)
        || !shown.every(id => planned.includes(id))) return reject('candidate_mismatch');
    if (!Array.isArray(masses) || masses.length !== planned.length
        || !masses.every(p => Number.isFinite(p) && p >= 0 && p <= 1)
        || Math.abs(masses.reduce((sum, p) => sum + p, 0) - 1) > 1e-6) {
      return reject('invalid_probabilities');
    }
    // The API commits the whole deal. The comparison may use only cards ever
    // seen during that round; condition every lane on that same eligible set.
    const selected = shown.map(id => masses[planned.indexOf(id)]);
    const total = selected.reduce((sum, p) => sum + p, 0);
    if (!(total > 0)) return reject('zero_eligible_mass');
    const probabilities = selected.map(p => p / total);
    const highest = Math.max(...probabilities);
    const ranks = probabilities.map(p => 1 + probabilities.filter(other => other > p).length);
    const order = shown.map((id, index) => ({ id, p: probabilities[index] }))
      .sort((a, b) => b.p - a.p || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)).map(item => item.id);
    const target = shown.indexOf(row.target);
    const scored = row.scored === true && shown.length >= 2 && target >= 0;
    const targetProbability = scored ? probabilities[target] : null;
    return { status: 'ok', scores: probabilities.slice(), probabilities, ranks, order,
      target_nll: scored ? -Math.log(Math.max(targetProbability, FLOOR)) : null,
      top1_credit: scored ? (targetProbability === highest
        ? 1 / probabilities.filter(p => p === highest).length : 0) : null,
      zero_probability_target: scored && targetProbability === 0 };
  }

  function sharedScore(row) {
    return row.scored === true && LANES.every(name => row.lanes[name]
      && row.lanes[name].status === 'ok' && Number.isFinite(row.lanes[name].target_nll)
      && row.lanes[name].target_nll >= 0);
  }

  function merge(report, predictions) {
    const result = clone(report);
    // Frozen, imported reports have their own verified contract and provenance.
    if (!result || result.mode !== 'prequential' || !Array.isArray(result.rounds)) return result;
    const rows = result.rounds;
    let valid = 0, missing = 0, failed = 0, invalid = 0;
    rows.forEach(row => {
      const prediction = predictions && typeof predictions.get === 'function'
        ? predictions.get(row.round) : undefined;
      const lane = joinedLane(row, prediction);
      row.lanes = { ...row.lanes, jev: lane };
      row.local_cumulative = clone(row.cumulative);
      if (lane.status === 'ok') valid++;
      else if (!prediction) missing++;
      else if (prediction.status !== 'ok') failed++;
      else invalid++;
    });
    const shared = rows.filter(sharedScore);
    result.local_summary = clone(result.summary);
    result.live_jev = true;
    result.jev_coverage = { valid_rounds: valid, scored_rounds: shared.length,
      total_scored_rounds: rows.filter(row => row.scored === true).length,
      missing_rounds: missing, failed_rounds: failed, invalid_rounds: invalid,
      curve_scope: shared.length ? 'shared_scored_rounds' : 'local_only_no_jev_coverage' };
    result.lanes = { ...result.lanes, jev: valid ? { status: 'ok', source: 'live_precommit' }
      : { status: failed || invalid ? 'failed' : 'not_run', reason: 'no_valid_precommit' } };
    if (shared.length) {
      const totals = { baseline: 0, learning: 0, jev: 0 };
      let n = 0;
      rows.forEach(row => {
        if (sharedScore(row)) {
          n++;
          LANES.forEach(name => { totals[name] += row.lanes[name].target_nll; });
        }
        row.cumulative = { n, jev_n: n, ...Object.fromEntries(LANES.map(name =>
          [`${name}_nll`, n ? totals[name] / n : null])) };
      });
      result.summary = clone(rows[rows.length - 1].cumulative);
    } else {
      // An unavailable third lane must neither acquire fabricated values nor
      // erase the two local curves that are still available for inspection.
      rows.forEach(row => { row.cumulative = { ...row.cumulative, jev_n: 0, jev_nll: null }; });
      result.summary = { ...result.summary, jev_n: 0, jev_nll: null };
    }
    return result;
  }

  global.NornPlayComparison = Object.freeze({ merge });
})(window);
