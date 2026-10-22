# Why ML loses on the displayed fixture

Offline diagnosis, 2026-10-21. The original [interactive report](../benchmarks/results/fast-thinker-comparison-fixture-20261021/index.html) and its scores are preserved. No model settings, production behavior or held-out outcomes were changed, and no API or credential was used.

**Subsequent implementation:** the user then authorized [small-sample shrinkage and evidence fallback](choice-policy.md). That separate local v5 change preserves this diagnostic snapshot and all840 original journals/splits; its results include both improvements and slower learning. The deployed Cloudflare build remains v4.

## What the original report actually shows

The fixture is a pipeline recording, not a simulated person with a stable image preference. Each round shows `[NORN_r, NORN_(r+24), NORN_(r+48)]` and always keeps the first card. Its first eight chosen cards are all N-type, the next eight all S-type, and the last eight all N-type again. Training sees the first block only. Position, card-ID block and content are confounded; neither local predictor has a position or card-ID feature.

| Frozen first 8 rounds; lower NLL is better | Fixed rule adapter | Personal ML | Stop-only | Uniform |
| --- | ---: | ---: | ---: | ---: |
| Training, first kept / STOP | 0.9257 | 0.9311 | 1.6516 | 1.3863 |
| Training, full sequence | 1.1725 | **0.7383** | 1.1475 | 1.2425 |
| Test 9–24, first kept / STOP | 1.7162 | 2.3147 | 1.6516 | **1.3863** |
| Test 9–24, full sequence | 1.5678 | 1.5739 | **1.1475** | 1.2425 |

Both content systems lose to a content-free baseline on this fixture. The original first-choice gap is real, but is not evidence that fixed rules are generally better.

The new comparison trains the existing sequential model and scores only one first finally-kept card / STOP per round. That target was chosen to avoid leaking later choices through shrinking option sets in a shared remote batch. The full sequence additionally scores stopping after keeping cards. ML fits a stop bias while the rule adapter fixes it at zero; the original first-choice report omits ML's subsequent-STOP advantage. On test rounds that STOP NLL is **0.8331 for ML versus 1.4194 for rules**. Scoring the full sequence reduces the ML-minus-rule NLL gap from **0.5984 to 0.0061**; it does not make ML win NLL. These are different tasks and denominators, not an excuse to replace the original metric after seeing a loss.

## Did it learn anything?

Yes, from its training sample. The unchanged 13-parameter fit converges in six Newton iterations, with maximum absolute posterior gradient about `8.9e-16`. Independent finite-difference gradient/Hessian checks agree to below `1e-9`; the adapter's sequence probabilities reconstruct the engine's NLL exactly. There is no evidence of a numerical optimizer or probability-adapter bug on this path.

What it learned did not transfer reliably across this fixture:

- Frozen ML first-choice NLL changes from **0.9311** on training rounds 1–8 to **3.3092** on reversed-type rounds 9–16, then **1.3202** on N-type rounds 17–24. The rule adapter follows the same shift, less strongly: **0.9257 → 2.5067 → 0.9257**.
- Every round contains a same-type, same-axis twin of the target. The four-axis rule necessarily splits that pair equally: within-type loss is `ln(2) = 0.6931`. ML can use pixels, ranks the target above its twin in **8/8 training rounds**, but only **7/16 test rounds**. Its within-type NLL worsens from **0.4155 to 0.8908**. That is fitting an accidental training relationship, without held-out benefit on this sample.
- The observed first-choice gap splits arithmetically into **+0.2449** from card/STOP mass, **+0.1559** from target-type mass conditional on choosing a card, and **+0.1977** from within-type ranking. This is an identity of losses, not three independent causal effects.

Reordering only the displayed test candidates leaves aligned content predictions unchanged to numerical tolerance and preserves the prefix and labels. An explicit first-slot policy would solve this particular recording; it would be exploiting its generator. We do not add it as a production preference model.

The default 19-round training cutoff also has different rankings from the deliberately small 8-round prefix. Its five test rounds differ, so that sensitivity check is saved separately and is not presented as evidence that more training caused improvement.

## Reproducible controls

The [protocol](fast-thinker-diagnostic-protocol.md) was saved before the seven-cell run, after inspecting the fixture, optimizer and exploratory recovery controls. It is prospective for this bounded run, not a claim that all investigation was preregistered. Seven fixed mechanisms, three seeds, 120 simulated visitors per mechanism and five prefix lengths use unchanged predictors. All prefix lengths predict the same final eight rounds. The [readable summary](../benchmarks/results/fast-thinker-diagnostic-20261021-summary.json) preserves all aggregate estimates, and [the compressed raw result](../benchmarks/results/fast-thinker-diagnostic-20261021.json.gz) retains every visitor's train/test metrics and counts, along with model/features/source/protocol hashes and numerical audits.

The controls include no content signal, first-slot selection, stable 64-column pixel taste, motif taste, pixel preference reversal, a same-family positive implementation control, and a same-feature threshold-response bridge. The positive control is circular by design; success there establishes engineering recovery only. The pixel/motif tests have different representations and response processes, and their difficulty and stop rates differ. Compare predictors within each cell.

**First-kept / STOP mean NLL** (equal visitor weight; lower is better):

| Fixed mechanism | Rule, 8 training rounds | ML, 8 | Stop-only, 8 | Rule, 32 | ML, 32 | Stop-only, 32 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No content taste; threshold | 1.862 | 2.032 | 1.827 | 1.795 | 1.903 | 1.827 |
| Always first slot | 1.986 | 2.440 | 2.166 | 1.810 | 2.346 | 2.219 |
| Stable 64-column pixel taste; threshold | 1.841 | 1.841 | 1.821 | 1.772 | 1.690 | 1.813 |
| Recurring-motif taste; threshold | 1.855 | 2.038 | 1.798 | 1.799 | 1.865 | 1.794 |
| Pixel taste reversed after training | 1.937 | 2.575 | 1.822 | 1.876 | 2.644 | 1.814 |
| Own 12 features; sequential logit **(circular control)** | 1.509 | 1.242 | 1.821 | 1.468 | 1.069 | 1.811 |
| Own 12 features; threshold process | 1.649 | 1.623 | 1.814 | 1.590 | 1.535 | 1.816 |

Uniform first-choice NLL is 1.787 in every cell: exposures and initial choice-set sizes are shared. The first-slot oracle achieves zero loss only in its deliberately positional cell; neither local content model can read that slot. It is not a recommended production policy.

The stable-pixel learning curve uses the same final eight test rounds at every prefix:

| Training rounds | Rule NLL | ML NLL | ML minus stop-only, descriptive 95% interval |
| --- | ---: | ---: | --- |
| 4 | 1.939 | 1.943 | +0.122 [+0.043, +0.205] |
| 8 | 1.841 | 1.841 | +0.020 [−0.043, +0.086] |
| 16 | 1.795 | 1.737 | −0.077 [−0.133, −0.021] |
| 24 | 1.769 | 1.692 | −0.120 [−0.175, −0.068] |
| 32 | 1.772 | 1.690 | −0.123 [−0.181, −0.070] |

At 32 rounds the paired ML-minus-rule difference is −0.082 [−0.133, −0.034]. Cards-conditional ML-minus-stop-only is also negative, −0.069 [−0.128, −0.014], so this advantage is not entirely from STOP. These are uncorrected pointwise intervals for simulated visitors, not human confidence claims or a universal 16/32-round requirement.

The mechanisms expose concrete limits:

- ML can recover planted stable signal. The same-feature threshold bridge shows that success is not restricted to the exact logit response generator, though it still shares the model's representation. At eight rounds its advantage over rules is small and its paired interval crosses zero.
- Under **no content taste**, first-choice ML-minus-stop-only remains +0.205 at eight rounds and +0.076 at 32. Cards-conditional loss is also worse. The posterior center fits finite-sample noise; more history reduces but does not eliminate this penalty here. Existing validation reports baseline failure, but the fitted predictor does not automatically become a baseline when that flag is false.
- The **sparse motif** condition still does not yield a baseline advantage at 32 rounds. This condition has weaker/sparser utility than the feature/pixel cells and motifs can correlate with existing features. Its failure does not isolate missing semantics as the sole cause or prove that adding motif dimensions would solve it.
- The **reversal** cell shares all first 32 rounds with stable pixel, but its final eight utilities change sign. Learning the former taste makes the frozen model worse. This checks sensitivity to nonstationarity, not drift detection or adaptation.

Re-run without an account:

```bash
python3 scripts/fast_thinker_diagnostic.py --output /tmp/norn-diagnostic-new.json
```

The destination must be new. This regenerates the fixed controls, not human records. It never reads the learning archive or a provider key.

## What follows

Keep the current original report labeled as a pipeline fixture and preserve its losing ML scores. Use complete target definitions and content-free references when interpreting it. The fixed controls can distinguish a broken implementation from a model that succeeds only when its assumptions hold; they cannot establish human preference accuracy.

Before changing the production model, use the already frozen real-visitor predictive evaluation: held-out choices, within-type discrimination, and the new-image probe. Null/position losses can justify investigating stronger shrinkage or an explicit no-signal fallback, but the setting must be selected on separate development data and then evaluated untouched. More dimensions, a remote decision API, or a more flattering fixture do not by themselves solve absent or unstable preference signal.
