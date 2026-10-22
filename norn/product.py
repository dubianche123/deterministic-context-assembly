"""Versioned product pacing, separate from frozen learning observations.

The pacing policy is recorded explicitly. Its resource ceiling is neither a
sample-size claim nor permission to promote a model layer.
"""

from copy import deepcopy


PRODUCT_VERSION = "norn.last-card.v1"
PACING_POLICY_VERSION = "norn.pacing.v1"
MAX_EXPLORATION_ROUNDS = 12
PRODUCT_COHORT_SUFFIX = ".last-card.v1"


def validate_product(value, journal=None):
    """Validate one explicitly chosen product format without changing facts."""
    if (type(value) is not dict or set(value) != {"schema_version", "pacing_policy"}
            or value["schema_version"] != PRODUCT_VERSION
            or value["pacing_policy"] != PACING_POLICY_VERSION):
        raise ValueError("product must contain the supported product and pacing policy versions")
    if journal is not None:
        if type(journal) is not dict or type(journal.get("events")) is not list:
            raise ValueError("product requires an event journal")
    return deepcopy(value)


def product_extends(earlier, later):
    """A visit cannot silently switch formats or acquire a format after entry."""
    return earlier == later


def finale_extends(earlier, later):
    """One choice may be added once; later writes must retain it byte-for-byte."""
    return earlier is None or earlier == later


def validate_product_fields(record):
    """Validate product sidecars at both builder and shared archive boundaries."""
    if "product" in record:
        record["product"] = validate_product(record["product"], record.get("journal"))
    if "finale" in record:
        if "product" not in record:
            raise ValueError("finale requires an explicit product version")
        from .finale import validate_result
        record["finale"] = validate_result(record["finale"], record.get("journal"))
    if "pacing" in record:
        if "product" not in record:
            raise ValueError("pacing requires an explicit product version")
        from .pacing import validate_trace
        record["pacing"] = validate_trace(record["pacing"], record.get("journal"))
    return record
