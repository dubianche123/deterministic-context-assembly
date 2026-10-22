# X-Ray integration fixture — 2026-10-22

These are synthetic engineering inputs, not people, a preference study or a
model-quality result. No prose or decision API was called. The default remote
lane is `not_run`; identical local predictions at the frozen cutoff are retained.

`recording.json` was generated with the existing test helper:

```python
import sys
sys.path.insert(0, "tests")
from test_research_deck import session, research
journal = session(research.MIXED, rounds=12, seed=19, keep=lambda x: x[1] > .3)
```

The original journal was supplied as a file to the CLI (hence the report's
`user_journal` input-kind label); it is still synthetic. The exact imported
`offline-report.json` includes six frozen training rounds, six test rounds and
embedded thumbnails. Images are PAM∃LA / bethgelab, CC BY 4.0, as credited in the
report and the bundled deck attribution. No scores or targets were changed to
favor a system.

To generate a new report without network calls, choose new output paths:

```bash
python3 -m norn compare benchmarks/results/norn-xray-comparison-fixture-20261022/recording.json \
  --train-rounds 6 --output /tmp/xray-new-report.json \
  --html /tmp/xray-new-report.html --language zh
```

Import the journal under X-Ray → Technical details, then the offline report.
The report replaces all lanes with its frozen-prefix comparison; returning to
rolling predictions restores the round-by-round view. The display at round 8
is used in the desktop screenshot, irrespective of which lane has lower loss.

The hosted pure-interface acceptance is
`../norn-xray-comparison-live-20261022.json`; browser observations and changed
asset hashes have separate reports. They establish integration, not human
learning, prospective click accuracy or causal recommendation benefit.
