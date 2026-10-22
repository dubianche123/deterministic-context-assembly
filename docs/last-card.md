# Last Card · 命运底牌

Norn is a complete entertainment game: explore pictures, make a final favourite
choice against a sealed prediction, meet an authored character, ask questions,
and save a card to share. The model owns the checkable guess; the language layer
creates the character story. Both are part of the product, with different meanings.

## The loop

1. **Explore.** Each round shows four pictures. Keep one, several or none, and
   revise choices before moving on. Real readiness, choice, revision, layout
   and attention observations retain the existing journal contract.
2. **Choose your pace.** After a completed round, the current pacing policy
   suggests continuing or offers the finale. The visitor can enter the finale
   after at least one completed round without accepting a suggestion.
3. **Seal the hand.** The server freezes the exploration prefix and profile,
   selects four pictures never dealt in this game, and seals its prediction.
   The browser receives candidates and a commitment, not the hidden answer.
4. **Choose one favourite and reveal.** The first settled answer is immutable.
   The server returns the prediction, real hit/miss, probabilities and the salt
   needed to verify the commitment. A request retry cannot change the answer.
5. **Meet the character.** The actual favourite is the main image. An authored
   role and share line frame two or three paragraphs grounded in that picture
   and the real exploration choices. The interface labels the reading once as
   entertainment. The story never rewrites the game's win or loss.
6. **Ask and take it home.** Three follow-up turns can challenge the reading,
   ask for its picture basis, a weakness or a motto. A browser Canvas creates
   a PNG containing the favourite, role, line, actual outcome and entertainment
   label. Downloading uploads no picture or conversation.

One game has one finale. **Play again** starts a fresh game and exploration
counter. It does not repeatedly insert a feedback challenge every five rounds.
The original proposal of five rounds was a pacing sketch, not an experimental
finding or a learned sufficiency threshold; it is not the current rule.

## Adaptive advice, not a trained stopping model

Product identity is exactly:

```json
{"schema_version":"norn.last-card.v1","pacing_policy":"norn.pacing.v1"}
```

`norn.pacing.v1` uses only genuinely completed exploration history. The first
policy is deliberately exploratory and **has not been trained**. Passing a
predictive evidence gate can produce an earlier offer; absent that signal,
randomized offers let the game vary its length while recording the offer
probability, draw and reason. Time cost can affect the invitation too. The
visitor retains the choice to stop exploring or continue.

The policy trace records each decision and its training prefix, including the
current model layer, selections and active time. The server rebuilds it from
the journal rather than accepting a client-authored claim. There is a ceiling
of 12 exploration rounds for product and resource reasons. Reaching that
ceiling does not mean the system has learned a reliable personal preference.
No particular round count establishes personality knowledge.

The relevant boundary is **advice now versus outcome later**: pacing may use
past choices, but it cannot use the final favourite, later rounds or the
language reading. Recorded propensities support later policy evaluation;
they do not, by themselves, remove self-selection from voluntary play. An
[offline pacing report](pacing.md) can compare properly bound finale outcomes
and propose an observational candidate; it does not activate a learned policy.

## The held-out guess

The finale's four candidates are sampled from pictures never dealt in the
exploration, independently of the eventual answer. The effective v6 weights
rank the same four candidates as the reference baseline. The server selects
one top prediction; a genuine top-score tie is broken using the sealed salt.
This is a game guess, including a reference-based guess when the evidence gate
has not accepted a deeper model.

The commitment binds the challenge, candidate order, local prediction,
probabilities, reference probabilities, exploration event/profile hashes, salt
and active layer. The result reveals enough data to recompute that commitment.
The final favourite is a separate sidecar and does not become an exploration
selection, a STOP event or training input to the already-frozen predictor.
This target is **the favourite among these four new pictures**. It is distinct
from the historical per-round target, the first finally-kept exploration image,
and from the post-reading answer to “which picture feels most like you?”.

The current API stages are `/api/pacing`, `/api/finale/start`,
`/api/jev/finale`, `/api/finale/choose` and then `/api/analyze` with the settled
`finale_id`. The authoritative game remains in temporary server/actor state;
only explicitly consented observations enter the research archive.

## Jev and resource boundaries

Where a server-held key makes it available, **Last Card enables Jev before
entry** and offers a clear pre-start opt-out. The choice locks when the game
starts. The invitation explains that earlier choices and numerical candidate
features go to Typesafe. This does not change archive consent.

Last Card reserves Jev for the finale, with one prediction attempt per game;
there are no Jev calls during exploration rounds. The local prediction is
already sealed. Jev is requested and settled separately before the favourite
choice. Its answer stays hidden until reveal. A result arriving after the
answer cannot earn live hit credit or rewrite the settled outcome.

The existing limits remain: pinned `jev-1.13.0`, a 15-second total deadline,
zero automatic retries, at most eight attempts across the actor's active
30-minute capabilities, a shared 100-attempt UTC-day budget and concurrency
of two. A missing key, exhausted budget, timeout or invalid result leaves
that opponent unavailable while the local game still works. The official
DeepSeek prose request has its own purpose and budget. Opening or scrubbing
X-Ray never requests another decision prediction.

## Personality as a creative role

`norn.reading-intent.last-card.v1` is a new, frozen creative context. It keeps
the exploration profile and its numerical evidence intact, adds the actual
favourite and real outcome, and supplies a `norn.persona.v1` authored role.
Eight role motifs give the result a name and a share line. Their selection from
picture metadata is a creative rule, not a personality classifier learned by
v6. LLM prose can be evocative and question the visitor; it must use real
picture details and cannot invent trauma, diagnoses, life history or a
successful guess that did not happen.

The new narrative has its own system/task instructions, not the previous
observed-pictures-only task. Older v1 reading histories retain their original
bytes and restore through their original prompt path. A final favourite still
supports a full character story when all exploration rounds were left blank;
that favourite does not artificially increase the exploration sample count.
Offline mock mode uses authored bilingual stories and is labelled as simulated.

## One product, optional contribution

Private and contributing visitors get the same game, character, three
questions and share card. Contribution is an independent, clear, optional and
withdrawable choice. With consent, the server keeps versioned product,
pacing and finale sidecars next to the unchanged learning journal, including
best-effort unfinished-visit checkpoints. The real final choice can be
checkpointed before language generation succeeds. A full record replaces the
same partial visit; withdrawal applies to both and prevents late resurrection.

New product records route to `<source>.last-card.v1`, or
`<source>.last-card.v1.round-feedback.v1` when the independent, default-off
exploration-feedback switch is enabled. The actual host remains
`source_deployment`. Older frozen visitor analysis excludes these new product
cohorts; it is not silently extended by changing the public game. Paper studies
and raw research results stay in the local research workspace.

The product asks whether someone wants to finish, read, question, share or
play again. Research separately asks whether pre-choice predictions help and
how much another exploration round is worth. A character resonance rating
answers an entertainment question; it is not a label of measured personality
accuracy. See [measurement](measurement.md), [X-Ray](xray.md) and
[the deck contract](research-deck.md).
