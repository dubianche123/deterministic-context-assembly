"""Deterministic A/B assignment for consented visitors.

An experiment is one registry entry. A visitor's arm comes from a hash of the
experiment name and the hashed visitor id, so the same browser stays in the
same arm across visits and nothing about the assignment needs storing beyond
the record itself. Readings without consent get an arm too (so both groups see
the same product) but leave no record, so only consented readings are measured.

Add an entry, read ``plan["experiments"][name]`` where the arm changes
behaviour, and compare arms with ``norn records report --by experiments.<name>``.
"""

import hashlib

# name -> {"arms": (control, variant, ...), "weights": (...)} ; keep finished
# experiments out of this table so new records stop carrying their arms.
EXPERIMENTS = {}


def assign(unit, experiments=None):
    """Return {experiment: arm} for one assignment unit (a visitor id)."""
    table = EXPERIMENTS if experiments is None else experiments
    arms = {}
    for name in sorted(table):
        spec = table[name]
        choices, weights = tuple(spec["arms"]), tuple(spec.get("weights") or (1,) * len(spec["arms"]))
        if len(choices) < 2 or len(weights) != len(choices) or min(weights) <= 0:
            raise ValueError(f"Experiment {name} needs two or more arms with positive weights")
        digest = hashlib.sha256(f"{name}:{unit}".encode("utf-8")).digest()
        point = int.from_bytes(digest[:8], "big") / 2 ** 64 * sum(weights)
        for arm, weight in zip(choices, weights):
            point -= weight
            if point < 0:
                break
        arms[name] = arm
    return arms
