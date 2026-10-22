"""Build a per-round trajectory for the X-Ray v2 prototype.

Model: Bayesian sequential multinomial logit with a "stop" option.
  card utility  u(c) = w_type . coords(c)/8  +  w_vis . pixel8(c)
  each round:   picks in click order are drawn one by one from the remaining
                displayed cards plus "stop"; the round ends when "stop" wins.
So no-pick and all-pick rounds still carry evidence, and click order is used.
Prior w ~ N(0, I/L2); posterior by Newton/Laplace. 12 + 1 parameters.

The page only scrubs this precomputed list, so dragging is instant and Python
stays the single scoring authority.

  python3 prototypes/xray-v2/build_trajectory.py                  # demo user
  python3 prototypes/xray-v2/build_trajectory.py --journal x.json # real export
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "the-norn-machine" / "backend" / "config"
AXES = ("E_I", "S_N", "T_F", "J_P")
L2_W, L2_B = 1.5, 0.5


def load_cards():
    meta = json.load(open(BACKEND / "cards_metadata.json", encoding="utf-8"))
    cat = json.load(open(BACKEND / "preference_features.json", encoding="utf-8"))
    ids = sorted(meta)
    coords = np.array([[meta[c]["coords"][a] / 8 for a in AXES] for c in ids])
    pix = [i for i, n in enumerate(cat["feature_names"]) if n.startswith("pixel.")]
    P = np.array([[cat["cards"][c][i] for i in pix] for c in ids])
    Pz = (P - P.mean(0)) / (P.std(0) + 1e-12)
    _, _, vt = np.linalg.svd(Pz, full_matrices=False)
    P8 = Pz @ vt[:8].T
    P8 /= P8.std(0)
    feats = np.hstack([coords, 0.5 * P8])
    return ids, meta, coords, P8, feats


def grid_layout(ids, meta, P8):
    """16x16 tapestry: S|N halves left/right, T|F halves top/bottom;
    inside each quadrant I|E columns and J|P rows; 16 cards per type block,
    ordered by the first pixel component so each block reads as a gradient."""
    cells = {}
    by_type = {}
    for i, c in enumerate(ids):
        by_type.setdefault(meta[c]["type"], []).append(i)
    for t, members in by_type.items():
        isE, isN, isF, isP = t[0] == "E", t[1] == "N", t[2] == "F", t[3] == "P"
        col0 = (2 * isN + isE) * 4
        row0 = (2 * isF + isP) * 4
        members.sort(key=lambda i: P8[i, 0])
        for k, i in enumerate(members):
            cells[ids[i]] = (col0 + k % 4, row0 + k // 4)
    return cells


def rows_from_journal(journal, index):
    """Final active picks per round, in click order (same labels as the shipped model)."""
    rows, shown, active, order = [], None, set(), []
    for e in journal["events"]:
        t = e["type"]
        if t == "round_started":
            shown, order = [index[c] for c in e["card_ids"]], []
        elif t == "card_selected":
            active.add(e["card_id"]); order.append(e["card_id"])
        elif t == "card_deselected":
            active.discard(e["card_id"])
        elif t == "round_completed":
            seen, picks = set(), []
            for c in order:                      # keep first click of cards still active
                if c in active and c not in seen and index[c] in shown:
                    picks.append(index[c]); seen.add(c)
            rows.append({"round": e["round"], "shown": shown, "picks": picks,
                         "duration_ms": e["duration_ms"]})
    return rows


def fit(feats, rows, start=None):
    d = feats.shape[1] + 1
    prior = np.array([L2_W] * (d - 1) + [L2_B])
    theta = np.zeros(d) if start is None else start.copy()
    stop = np.zeros(d); stop[-1] = 1.0
    A = np.hstack([feats, np.zeros((len(feats), 1))])
    for _ in range(100):
        g = -prior * theta
        H = np.diag(prior).astype(float)
        for row in rows:
            remaining = list(row["shown"])
            for choice in row["picks"] + [None]:
                X = np.vstack([A[remaining], stop]) if remaining else stop[None, :]
                s = X @ theta; s -= s.max(); p = np.exp(s); p /= p.sum()
                mu = p @ X
                chosen = stop if choice is None else A[choice]
                g += chosen - mu
                H += (X * p[:, None]).T @ X - np.outer(mu, mu)
                if choice is not None:
                    remaining.remove(choice)
        step = np.linalg.solve(H, g)
        theta += step
        if np.abs(step).max() < 1e-9:
            break
    return theta, np.linalg.inv(H)


def type_probs(mean4, cov4, rng):
    z = rng.multivariate_normal(mean4, cov4, size=6000)
    letters = np.stack([np.where(z[:, 0] >= 0, "E", "I"), np.where(z[:, 1] >= 0, "N", "S"),
                        np.where(z[:, 2] >= 0, "T", "F"), np.where(z[:, 3] >= 0, "J", "P")], 1)
    names, counts = np.unique(["".join(r) for r in letters], return_counts=True)
    order = np.argsort(-counts)[:4]
    return [{"type": str(names[i]), "p": round(float(counts[i] / len(z)), 3)} for i in order]


def demo_journal(ids, feats, rounds=20, seed=4):
    """Simulated visitor with a known taste, generated by the same choice model."""
    rng = np.random.default_rng(seed)
    w = np.zeros(feats.shape[1])
    w[:4] = [-1.0, 1.6, -1.3, -0.5]          # I, N, F, P leaning
    w[4:6] = [0.7, -0.5]                     # plus a visual taste
    b = 0.4
    util = feats @ w
    events, seq, used = [], 0, set()

    def add(**e):
        nonlocal seq
        seq += 1
        events.append({"event_id": f"evt-{seq:06d}", "sequence": seq, **e})

    for r in range(1, rounds + 1):
        k = int(rng.integers(4, 7))
        pool = [i for i in range(len(ids)) if i not in used]
        shown = list(rng.choice(pool, size=k, replace=False)); used.update(shown)
        add(type="round_started", round=r, card_ids=[ids[i] for i in shown])
        remaining = list(shown)
        while remaining:
            s = np.append(util[remaining], b) + rng.gumbel(size=len(remaining) + 1)
            j = int(np.argmax(s))
            if j == len(remaining):
                break
            add(type="card_selected", round=r, card_id=ids[remaining[j]])
            remaining.pop(j)
        add(type="round_completed", round=r, duration_ms=int(rng.integers(2500, 9000)))
    add(type="session_completed")
    return {"schema_version": "norn.events.v1", "events": events}, w


def build(journal, truth=None, label="demo"):
    ids, meta, coords, P8, feats = load_cards()
    index = {c: i for i, c in enumerate(ids)}
    rows = rows_from_journal(journal, index)
    cells = grid_layout(ids, meta, P8)
    rng = np.random.default_rng(0)
    states, theta, prev4 = [], None, np.zeros(4)
    for r in range(len(rows) + 1):
        theta, cov = fit(feats, rows[:r], theta)
        mean4, cov4 = theta[:4], cov[:4, :4]
        scores = feats @ theta[:-1]
        top = [ids[i] for i in np.argsort(-scores)[:6]]
        state = {
            "round": r,
            "mean": [round(float(v), 4) for v in mean4],
            "sd": [round(float(math.sqrt(cov4[i, i])), 4) for i in range(4)],
            "cov_sn_tf": [[round(float(cov4[a, b]), 5) for b in (1, 2)] for a in (1, 2)],
            "scores": [round(float(v), 3) for v in scores],
            "top": top,
            "types": type_probs(mean4, cov4, rng),
            "stop_bias": round(float(theta[-1]), 3),
        }
        if r:
            row = rows[r - 1]
            state["shown"] = [ids[i] for i in row["shown"]]
            state["picks"] = [ids[i] for i in row["picks"]]
            state["delta"] = [round(float(v), 4) for v in (mean4 - prev4)]
            state["duration_ms"] = row["duration_ms"]
        prev4 = mean4.copy()
        states.append(state)
    return {
        "schema": "norn.xray.trajectory.proto1",
        "source": label,
        "model": "bayes-seq-mnl: 4 type axes + 8 pixel PCs + stop option; Laplace posterior",
        "axes": list(AXES),
        "cards": [{"id": c, "type": meta[c]["type"], "cell": cells[c],
                   "url": f"../../the-norn-machine/frontend/assets/cards/{c}.webp"} for c in ids],
        "truth": None if truth is None else [round(float(v), 3) for v in truth[:4]],
        "states": states,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", help="norn.events.v1 journal or norn.replay.v1 export")
    ap.add_argument("--output", default=str(Path(__file__).with_name("trajectory.json")))
    args = ap.parse_args()
    if args.journal:
        doc = json.load(open(args.journal, encoding="utf-8"))
        journal = doc.get("journal", doc)
        out = build(journal, label="recording")
    else:
        ids, _, _, _, feats = load_cards()
        journal, w = demo_journal(ids, feats)
        out = build(journal, truth=w, label="demo")
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    last = out["states"][-1]
    print(f"{len(out['states']) - 1} rounds -> {args.output}; final types {last['types'][:2]}")


if __name__ == "__main__":
    main()
