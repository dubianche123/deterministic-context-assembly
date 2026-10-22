# Offline fast-thinker diagnostic — frozen 2026-10-21

This is an engineering diagnosis of the displayed fixture, not a human model-selection trial. No API, credential, archive, production model, feature projection, prior, temperature or deployed application is changed. Preserve the original report and its scores.

## Questions fixed before the diagnostic simulation

1. Does the original deterministic fixture contain stable content preference, or a prefix/test shift?
2. Did the current ML fit converge and improve its training objective? Do direct predictions reconstruct the engine loss?
3. How much does scoring only the first finally-kept card differ from scoring the full trained sequence?
4. With unchanged settings, does a stable representable signal generalize? What happens with no content signal, unrepresented preferences, a different response process, and preference reversal?

The fixture audit uses its original first eight rounds for training and rounds 9–24 for testing; separately show 1–8, 9–16 and 17–24. Report the default 19-round cutoff only as a separate sensitivity check: it has a different test set and is not a learning curve. Inspect type/axis/pixel distributions, same-type twins, training/test loss, and invariance to reordering displayed candidates without changing choices. Audit the gradient and Hessian by finite differences at step `1e-5`.

## Fixed simulation

- Seeds: **11, 29, 47**, **40 independent simulated visitors per seed** and cell (120 per cell).
- One visit, **40 rounds**, 4–6 cards per round, sampled without replacement from the existing 256-card catalog. Duration is a constant 3,000 ms in every simulated round, so invented timing does not create a preference signal.
- Independent deterministic random streams for exposure, preference, stop bias and each round's response noise. All cells share each visitor's exposure deck and stop bias. Stable-pixel and reversal cells share the exact preference, noise and first 32 rounds.
- Training prefixes **4, 8, 16, 24, 32**. Every prefix predicts the **same final eight rounds (33–40)**; intermediate rounds outside the prefix are ignored, not fitted. No test outcome updates a predictor.
- Predictors: unchanged historical-axis softmax adapter, current 12-feature sequential MNL, training-only stop baseline, and uniform baseline. The rule's fixed temperature and fixed STOP differ from ML's fitted STOP; the comparison is between these concrete systems.
- The main displayed-report target is first finally-kept card / STOP. Also report the full sequential target, cards-conditional and same-type loss, binary card/STOP loss and their denominators. Forced STOP after all cards were selected is excluded. A random first-kept card in threshold cells does not mean the most-liked card.

| Cell | Latent utility and response |
| --- | --- |
| `null_threshold` | Zero card utility, threshold response; no content taste |
| `position_only` | Keep exactly the first displayed card; content models cannot observe position |
| `pixel_threshold` | A fixed direction in all 64 standardized stored pixel-descriptor columns, scale 1; threshold response |
| `motif_threshold` | Twelve liked recurring motifs, utility 1.5 per match; threshold response |
| `pixel_reversal` | Same as stable pixel through round 32, then negate card utility; tests a frozen old preference, not drift adaptation |
| `features_logit` | Gaussian weights, sd 1, over the model's own 12 features; sequential logit. **Circular positive implementation control**, not independent evidence of real-world quality |
| `features_threshold` | Same feature utility as the positive control, threshold response; bridges response-process mismatch |

Threshold means independently Gumbel-noised utility above a visitor's fixed bar, followed by random click order, using the existing simulator. Stop bar is `Normal(0.5, 0.3)`; the position-only cell deliberately ignores this bar. Pixel descriptors come from the existing group-normalized feature file, then receive per-column standardization; they are not unprocessed image measurements. Motif and pixel utilities are not rescaled after observing results. Their selection rates and difficulty differ: compare predictors **within a cell**, never raw losses between cells. Motifs may correlate with existing axes/features; the pixel projection can retain some 64-column signal. These cells do not isolate perfectly independent representations.

## Reporting fixed before outcomes

Save every visitor's frozen-prefix train/test metrics, per-cell learning curves, paired ML minus rule and ML minus stop-only mean loss, and deterministic visitor bootstrap 95% intervals (2,000 resamples, seed 20261021). Average visitor means with equal visitor weight; report optional metrics only for visitors with eligible decisions and give counts. These are descriptive pointwise intervals, uncorrected for multiple comparisons; they describe this simulated population only, not human uncertainty. No automatic winner gate, hyperparameter search or production replacement follows from them.

A successful same-family control establishes recoverability under planted assumptions. A failed mismatched control can reflect limited features, too little history, an unsuitable response model, or regularization; it does not by itself distinguish these. Null and position cells should not be used to demand a learned content advantage. Reversal may punish a model precisely because it learned the former preference. Human predictive value remains unestablished until a frozen, paired real-visitor evaluation.

Reproduce with `python3 scripts/fast_thinker_diagnostic.py --output NEW_PATH.json`. The command writes a new file only, records this protocol and source hashes, and never reads a credential or calls a provider. Do not retune settings or replace the original fixture based on the results.
