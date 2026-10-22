# Display experiment v1: which cards a round shows

Status: **implemented locally, not deployed.** The Cloudflare pilot still draws
random rounds with `norn.events.v4`. Deploying this needs the owner's
confirmation. The research pilot does not use this version: it runs display v2
on the PAM∃LA research deck ([research deck](research-deck.md)), where a
population reference exists.

This version has three goals:
- show a visitor more cards they are likely to keep;
- keep learning as well as random display does;
- collect real choice data that a later analysis can learn from.

The research behind it, and the frozen analysis plan for the data it collects,
live outside this repository.

## Rules

1. **One randomization per session.** Before the first round, the browser draws
   the session's display policy:
   - `norn.display.mixed.v1` with probability `displayMixedShare` (runtime
     config; the generated Cloudflare config sets 0.5);
   - otherwise `norn.display.random.v1`.

   Setting the share to 0 is the fallback switch back to random rounds. Reloading
   starts a new session and a new draw.
2. **Random rounds** are unchanged: 4–6 cards drawn uniformly from the cards not
   yet shown in this pass through the deck.
3. **Mixed rounds** use the same card count. Half the cards, rounded down, are
   the ones the visitor's scoring-v5 *candidate* ranks highest among the
   available cards:
   - the candidate is the regularized MAP over completed rounds, before the
     content gate;
   - display is not a claim, so the gate does not apply;
   - the other cards are drawn uniformly;
   - ties, including every card in the first round, are broken at random.

   No population popularity exists yet, so this is the cold-start rule.
4. **No fixed slots.** Screen order is a uniform permutation of the drawn cards,
   so role and position stay independent on every layout. Full balancing over
   positions would need card counts to stay fixed, and they vary from 4 to 6.
5. **Only the past.** A round is drawn only from completed rounds: never the open
   round, and never anything after it. The prequential evidence gate of the
   reading therefore stays valid whenever the visitor stops.
6. **Fallback inside a mixed session.** If the features are missing or the
   candidate cannot be fitted, the round is drawn like a random round. Every
   role is then logged as `random`; nothing is labelled as a prediction that was
   not one.

## Logging: `norn.events.v5`

- **What v5 adds.** v5 keeps every v4 field. `round_started` gains
  `display: {policy, roles}`, with one role per displayed card: `predicted` or
  `random`.
- **What replay rejects:**
  - a missing or unknown policy;
  - a policy change inside a session;
  - a role count that does not match the cards;
  - predicted cards in a random round;
  - a mixed round whose predicted count is neither half the cards, rounded down,
    nor zero.
- **What does not change:**
  - display roles never change scoring facts, profiles or preference observations;
  - v1–v4 journals replay exactly as before;
  - the pilot's new-image probe stays pinned to v4.
- **Consented records** carry `experiments.display` (`mixed` or `random`), so
  `norn records report --by experiments.display` splits outcomes by arm.
- **Verification.** `display_policy.verify(journal)` recomputes every logged
  prediction from the journal with the engine reference. It checks that every
  card was available and that no available card outranked a predicted one.

## Where the code is

| Piece | File |
| --- | --- |
| Engine reference: rule, structural checks, verification, simulation | `the-norn-machine/backend/display_policy.py` |
| Browser rule: same features, same candidate, same tie and placement rules | `the-norn-machine/frontend/app.js` (`drawDisplayRound`) |
| Browser copy of the twelve scoring features | `frontend/data/display-features-v1.json`, from `scripts/prepare_display_features.py` (`--check` fails when stale) |
| Contract | `norn/events.py` (v5) |

## Tests

- **Contract:** v5 rules and rejections; scoring facts identical to the same
  journal without display.
- **Engine reference:** verification passes on reference sessions and catches
  tampering.
- **Browser against engine:** a real page fixture draws mixed rounds over the
  full deck. The engine verifies every logged prediction.
- **Records:** the arm reaches consented records.
- **Bundle:** the Cloudflare bundle ships the feature file.

## Before and after deploying

- **Deploy.** Rebuild the Cloudflare bundle (`scripts/prepare_cloudflare_bundle.py`).
  Check that `runtime-config.js` carries `displayMixedShare`. Then deploy only
  with the owner's confirmation.
- **Acceptance.** After deploying:
  - open a few sessions;
  - export them;
  - confirm their journals are v5 and verify;
  - withdraw the acceptance records, or list their visitor ids for exclusion
    before any analysis.
- **Next version.** A population term (deck popularity and slot effects estimated
  from consented records) is a new policy version, not an edit of v1.
