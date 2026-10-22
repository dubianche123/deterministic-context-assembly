"""Planted-preference comparison of the 979-D ranker and low-dimensional choice models.

This is the simulation behind the 2026-10-20 review cited in docs/learning.md,
moved from a scratch file into the repository so it can be rerun:

    python3 scripts/model_comparison_planted.py --output benchmarks/results/model-comparison-planted-v1.json

Each simulated user gets a true utility over all 256 cards, planted in one of
four spaces, and plays frontend-like rounds (4-6 unseen cards, Gumbel-max pick,
sometimes a second pick). Every model sees the same journals. Metric: AUC of
the fitted model's card scores against the planted utility over all 256 cards,
averaged over users (mean, standard error).

Read the results with their limits:
- "type" users are planted on exactly the four coordinates the 4-D model uses,
  so that model winning there is built in, not discovered.
- "visual" users are planted on the 64 raw pixel statistics, which the 979-D
  ranker sees in full and the 12-D hybrid only through 8 components.
- "motif" users like recurring subjects that no low-dimensional model sees;
  every model is close to chance there.
- Simulated choices follow a logit rule; no result here says how people choose.

Reproduction: the type, visual and mixed cells match the 2026-10-20 scratch run
bit for bit. That run listed common motifs in set order, which follows Python's
string hash seed, so its motif cells cannot be replayed exactly; the list is
sorted here and the motif cells differ from the original by at most 0.017 AUC.
Needs numpy. Synthetic data only.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "the-norn-machine" / "backend"))
import preference_model as pm  # noqa: E402  the shipped trainer; legacy 979-D version below

cat = json.load(open(ROOT / "the-norn-machine" / "backend" / "config" / "preference_features.json"))
meta = json.load(open(ROOT / "the-norn-machine" / "backend" / "config" / "cards_metadata.json"))
ids = sorted(cat["cards"])
X979 = np.array([cat["cards"][c] for c in ids])
names = cat["feature_names"]
pix_idx = [i for i, n in enumerate(names) if n.startswith("pixel.")]
coords = np.array([[meta[c]["coords"][d] / 8 for d in ("E_I", "S_N", "T_F", "J_P")] for c in ids])
types = sorted({meta[c]["type"] for c in ids})
T16 = np.array([[1.0 if meta[c]["type"] == t else 0.0 for t in types] for c in ids])
P = X979[:, pix_idx]
Pz = (P - P.mean(0)) / (P.std(0) + 1e-12)
# low-dim pixel summary: top-8 PCs
U, S, Vt = np.linalg.svd(Pz, full_matrices=False)
P8 = Pz @ Vt[:8].T
P8 /= P8.std(0)
from collections import Counter
mc = Counter(m for c in ids for m in set(meta[c]["motifs"]))
common = sorted(m for m, k in mc.items() if k >= 3)  # sorted: set order follows the string hash seed
N = len(ids)


def planted(kind, rng):
    if kind == "type":           # the project's design hypothesis: 4-D temperament
        th = rng.normal(size=4); return coords @ th
    if kind == "visual":         # colour / brightness / composition taste
        w = rng.normal(size=Pz.shape[1]); w /= np.linalg.norm(w); return Pz @ w * 2
    if kind == "motif":          # likes particular recurring subjects
        liked = set(rng.choice(common, size=12, replace=False))
        return np.array([sum(m in liked for m in meta[c]["motifs"]) for c in ids], float) * 1.5
    if kind == "mixed":
        a = planted("type", rng); b = planted("visual", rng)
        return (a / a.std() + b / b.std())
    raise ValueError(kind)


def simulate(util, rounds, rng, temp=1.0):
    """Frontend-like journal: 4-6 unseen cards/round, Gumbel-max pick + maybe 2nd."""
    ev, seq, used = [], 0, set()
    def add(**e):
        nonlocal seq; seq += 1; ev.append({"event_id": f"e{seq}", "sequence": seq, **e})
    rows = []
    for r in range(1, rounds + 1):
        k = int(rng.integers(4, 7))
        pool = [i for i in range(N) if i not in used]
        if len(pool) < k: used.clear(); pool = list(range(N))
        shown = list(rng.choice(pool, size=k, replace=False)); used.update(shown)
        add(type="round_started", round=r, card_ids=[ids[i] for i in shown])
        noisy = util[shown] / temp + rng.gumbel(size=k)
        order = np.argsort(-noisy)
        picks = [shown[order[0]]]
        if noisy[order[1]] > noisy[order[0]] - 0.5: picks.append(shown[order[1]])
        for i in picks: add(type="card_selected", round=r, card_id=ids[i])
        add(type="round_completed", round=r, duration_ms=3000)
        rows.append((shown, picks))
    add(type="session_completed")
    return {"schema_version": "norn.events.v1", "events": ev}, rows


def fit_clogit(F, rows, l2):
    """MAP conditional logit (Plackett-Luce-ish: each pick vs the shown set)."""
    d = F.shape[1]; w = np.zeros(d)
    for _ in range(300):
        g = -l2 * w; H = -l2 * np.eye(d)
        for shown, picks in rows:
            remaining = list(shown)
            for p in picks:
                Fs = F[remaining]; s = Fs @ w; s -= s.max(); pr = np.exp(s); pr /= pr.sum()
                mu = pr @ Fs
                g += F[p] - mu
                H -= (Fs * pr[:, None]).T @ Fs - np.outer(mu, mu)
                remaining.remove(p)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8: break
    return w


def auc(score, util):
    i, j = np.triu_indices(N, 1)
    du, ds = util[i] - util[j], score[i] - score[j]
    m = np.abs(du) > 1e-9
    agree = np.sign(du[m]) == np.sign(ds[m]); tie = ds[m] == 0
    return (agree.sum() + 0.5 * tie.sum()) / m.sum()


MODELS = {
    "linear_bt_979": None,                                   # the shipped 979-D ranker (legacy version)
    "axes_4": (coords, 1.0),                                 # conditional logit on the four type coordinates
    "type_16": (T16, 2.0),                                   # one-hot type
    "pixel_pca_8": (P8, 2.0),                                # first 8 components of the 64 pixel statistics
    "hybrid_12": (np.hstack([coords, P8 * 0.5]), 1.5),       # axes + pixel components, as in scoring v4
}


def run(kinds=("type", "visual", "motif", "mixed"), rounds_list=(8, 16, 24, 48), users=80, seed=7):
    rng = np.random.default_rng(seed)
    out = {}
    for kind in kinds:
        for R in rounds_list:
            acc = {m: [] for m in MODELS}
            for u in range(users):
                util = planted(kind, rng)
                journal, rows = simulate(util, R, rng)
                ck = pm.train([journal], model_version=pm.LEGACY_MODEL_VERSION)
                w = np.array(ck["weights"]); acc["linear_bt_979"].append(auc(X979 @ w, util))
                for m, spec in MODELS.items():
                    if spec is None: continue
                    F, l2 = spec
                    acc[m].append(auc(F @ fit_clogit(F, rows, l2), util))
            out[(kind, R)] = {m: (float(np.mean(v)), float(np.std(v) / math.sqrt(len(v)))) for m, v in acc.items()}
            print(kind, R, {m: round(v[0], 3) for m, v in out[(kind, R)].items()}, file=sys.stderr, flush=True)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--users", type=int, default=80, help="Simulated users per (population, rounds) cell")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, help="Write-once JSON result")
    args = parser.parse_args(argv)
    results = run(users=args.users, seed=args.seed)
    document = {
        "schema_version": "norn.model-comparison-planted.v1", "data": "simulated only",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "users_per_cell": args.users, "seed": args.seed, "metric": "AUC of model scores vs planted utility "
        "over all 256 cards; [mean, standard error] over users",
        "limitations": ["type users are planted on the 4-D model's own coordinates (built-in advantage)",
                        "visual users are planted on the 64 pixel statistics the 979-D ranker sees in full",
                        "motif users like subjects no low-dimensional model sees",
                        "choices follow a logit rule; nothing here measures real people"],
        "cells": {f"{kind}|{rounds}": cell for (kind, rounds), cell in results.items()},
    }
    text = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        with open(args.output, "x", encoding="utf-8") as handle:  # earlier results are never replaced
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
