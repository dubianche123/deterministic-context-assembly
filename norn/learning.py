"""Public personal-learning adapters and explicit local checkpoint commands."""

from copy import deepcopy
import json
from pathlib import Path
import sys

from .core import engine_module

_model = engine_module("preference_model")
MODEL_VERSION = _model.MODEL_VERSION
CHECKPOINT_SCHEMA_VERSION = _model.CHECKPOINT_SCHEMA_VERSION
DEFAULT_CONFIG = deepcopy(_model.DEFAULT_CONFIG)
load_catalog = _model.load_catalog
train = _model.train
summarize = _model.summarize
rank = _model.rank
predict_scores = _model.predict_scores
verify_checkpoint = _model.verify_checkpoint
observations_from_journals = _model.observations_from_journals
feature_label = _model.feature_label
MAX_FILE_BYTES = 8 * 1024 * 1024


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Learning input exceeds the 8 MiB limit")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))


def recording_journal(document, include_open=False):
    from .events import SUPPORTED_EVENTS_SCHEMA_VERSIONS, replay_events
    if isinstance(document, dict) and document.get("schema_version") in SUPPORTED_EVENTS_SCHEMA_VERSIONS:
        return replay_events(document, require_complete=not include_open)["journal"]
    from .replay import replay_document
    bundle = replay_document(document)
    if bundle.get("journal") is None:
        raise ValueError("A legacy snapshot has no displayed-card history for preference learning")
    return replay_events(bundle["journal"], require_complete=not include_open)["journal"]


def learn_recordings(recordings, *, config=None, catalog=None, include_open=False):
    """Read local recordings only on an explicit caller request; never persist implicitly."""
    journals = [recording_journal(read_json(item) if isinstance(item, (str, Path)) else item, include_open)
                for item in recordings]
    return train(journals, catalog=catalog, config=config, include_open=include_open)


def save_checkpoint(checkpoint, path, *, catalog=None):
    verify_checkpoint(checkpoint, catalog=catalog)
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(checkpoint, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def load_checkpoint(path, *, catalog=None, journals=None):
    checkpoint = read_json(path)
    verify_checkpoint(checkpoint, catalog=catalog, journals=journals)
    return checkpoint


def add_learning_arguments(parser):
    parser.add_argument("recordings", nargs="+", type=Path, help="Completed event journals or exported recordings")
    parser.add_argument("--output", type=Path, required=True, help="New local checkpoint; refuses overwrite")
    parser.add_argument("--epochs", type=int, default=DEFAULT_CONFIG["epochs"])
    parser.add_argument("--l2", type=float, default=DEFAULT_CONFIG["l2"])
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_CONFIG["max_pairs"])
    parser.add_argument("--include-open", action="store_true", help="Explicitly include a provisional open-round snapshot")
    parser.add_argument("--json", action="store_true", dest="as_json")


def learning_main(args):
    try:
        checkpoint = learn_recordings(args.recordings,
            config={"epochs": args.epochs, "l2": args.l2, "max_pairs": args.max_pairs}, include_open=args.include_open)
        save_checkpoint(checkpoint, args.output)
        summary = summarize(checkpoint)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Learning failed: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print(f"Saved personal preference model: {args.output}")
        print(f"{summary['observation_count']} relative-choice rounds; {summary['pair_count']} sampled pairs")
        print(f"Validation: {summary['validation']['status']} ({summary['validation'].get('reason') or 'held-out relative choices'})")
        print("Ranking scores describe relative choices, not personality accuracy. Model calls: 0")
    return 0


def add_rank_arguments(parser):
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--recording", type=Path, action="append", default=[], help="Optionally replay original training sources")
    parser.add_argument("--card", action="append", dest="cards")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--json", action="store_true", dest="as_json")


def rank_main(args):
    try:
        journals = [recording_journal(read_json(path)) for path in args.recording] if args.recording else None
        checkpoint = load_checkpoint(args.checkpoint, journals=journals)
        predictions = rank(checkpoint, card_ids=args.cards, top_k=args.top)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Ranking failed: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"verified": True, "source_replayed": journals is not None,
                          "predictions": predictions, "model_calls": 0}, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        for item in predictions:
            print(f"{item['card_id']}: {item['score']:.6f} (relative ranking logit)")
    return 0
