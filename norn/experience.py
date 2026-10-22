"""Observed product presentation, kept outside frozen choices and model hashes.

Feedback labels record what the interface displayed, not an authenticated
prediction result. Absence is unspecified and must never be read as ``off``.
"""

from copy import deepcopy

from .events import MAX_DURATION_MS, MAX_ROUNDS


EXPERIENCE_VERSION = "norn.experience.v1"
FEEDBACK_KINDS = ("hit", "miss", "unscored", "blank")
ROUND_FEEDBACK_COHORT_SUFFIX = ".round-feedback.v1"


def validate_experience(value, journal):
    """Validate an explicit configuration and its actual ordered UI exposures."""
    if (type(value) is not dict or set(value) != {"schema_version", "round_feedback", "exposures"}
            or value["schema_version"] != EXPERIENCE_VERSION
            or value["round_feedback"] not in ("on", "off")
            or type(value["exposures"]) is not list or len(value["exposures"]) > MAX_ROUNDS):
        raise ValueError("experience must contain a supported feedback mode and bounded exposures")
    if type(journal) is not dict or type(journal.get("events")) is not list:
        raise ValueError("experience requires an event journal")
    completed = {event["round"] for event in journal["events"]
                 if type(event) is dict and event.get("type") == "round_completed"
                 and type(event.get("round")) is int}
    if value["round_feedback"] == "off" and value["exposures"]:
        raise ValueError("Feedback off cannot contain displayed feedback exposures")
    previous_round, previous_elapsed = 0, 0
    for row in value["exposures"]:
        if type(row) is not dict or set(row) != {"after_round", "feedback", "elapsed_ms"}:
            raise ValueError("experience exposure has unexpected fields")
        number, elapsed = row["after_round"], row["elapsed_ms"]
        if (type(number) is not int or not previous_round < number <= MAX_ROUNDS
                or number not in completed or row["feedback"] not in FEEDBACK_KINDS
                or type(elapsed) is not int or not previous_elapsed <= elapsed <= MAX_DURATION_MS):
            raise ValueError("experience exposures must follow real completed rounds and a monotonic visit clock")
        previous_round, previous_elapsed = number, elapsed
    return deepcopy(value)


def experience_extends(earlier, later):
    """The first stored mode is immutable; exposures can only append in order."""
    if earlier is None or later is None:
        return earlier is None and later is None
    return (earlier["schema_version"] == later["schema_version"]
            and earlier["round_feedback"] == later["round_feedback"]
            and len(earlier["exposures"]) <= len(later["exposures"])
            and earlier["exposures"] == later["exposures"][:len(earlier["exposures"])])


def archive_cohort_fields(source_deployment, experience, product=None):
    """Route product and feedback interventions away from the original cohort.

    ``deployment`` is the historical reader's cohort routing label. A variant
    retains its actual host source separately, allowing the unchanged live-v3
    reader to fail closed as ``other_deployment`` while all observations remain
    in the archive. Off and old unspecified records retain their old shape.
    """
    from .product import PRODUCT_COHORT_SUFFIX, validate_product
    if product is not None:
        validate_product(product)
    feedback = experience is not None and experience["round_feedback"] == "on"
    if product is None and not feedback:
        return {"deployment": source_deployment}
    if type(source_deployment) is not str or not 1 <= len(source_deployment) <= 128:
        raise ValueError("Variant cohort requires a bounded source deployment")
    return {"deployment": source_deployment
            + (PRODUCT_COHORT_SUFFIX if product is not None else "")
            + (ROUND_FEEDBACK_COHORT_SUFFIX if feedback else ""),
            "source_deployment": source_deployment}


def validate_archive_cohort(record):
    """Prevent outbox/RPC writes from mixing game or feedback data into old cohorts."""
    experience = record.get("experience")
    product = record.get("product")
    if product is not None or (experience is not None and experience["round_feedback"] == "on"):
        fields = archive_cohort_fields(record.get("source_deployment"), experience, product)
        if any(record.get(key) != value for key, value in fields.items()):
            raise ValueError("Product or feedback-on record must retain its separate archive cohort")


def classify_round_feedback(record):
    """Classify an archive row without turning older unknown visits into off.

    This is presentation metadata validation, not research eligibility. The
    separately frozen live-v3 reader ignores this sidecar; archive_cohort_fields
    keeps on records out through its existing deployment eligibility rule.
    """
    if type(record) is not dict:
        return "invalid"
    if "experience" not in record:
        return "unspecified"
    try:
        return validate_experience(record["experience"], record.get("journal"))["round_feedback"]
    except ValueError:
        return "invalid"


def filter_explicit_feedback_off(records):
    """Return explicitly-off rows and exclusion counts; never mutate inputs.

    This optional preprocessing helper does not run automatically in any
    research analysis. Protocol approval and the existing eligibility checks
    remain the research caller's responsibility.
    """
    kept, excluded = [], {"on": 0, "unspecified": 0, "invalid": 0}
    for record in records:
        mode = classify_round_feedback(record)
        if mode == "off":
            kept.append(record)
        else:
            excluded[mode] += 1
    return kept, excluded
