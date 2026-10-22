"""Versioned transcendental math for exact native/WASM replay.

The portable contract converts each IEEE-754 input exactly to Decimal, performs
the transcendental operation at 34 decimal digits with ROUND_HALF_EVEN, then
rounds once to binary64. Basic float arithmetic and math.fsum stay unchanged.
Historical scorers retain their native libm calls. ContextVar scopes cannot
change another request's numerical contract or the process-global math module.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN
import math


PORTABLE_CONTRACT = "norn.numeric.decimal34.v1"
NATIVE_CONTRACT = "norn.numeric.native-libm.v1"
_CONTRACT = ContextVar("norn_numerical_contract", default=None)


def current_contract():
    return _CONTRACT.get()


@contextmanager
def use(contract):
    if contract not in (PORTABLE_CONTRACT, NATIVE_CONTRACT):
        raise ValueError("Unsupported numerical contract")
    token = _CONTRACT.set(contract)
    try:
        yield
    finally:
        _CONTRACT.reset(token)


def _decimal(operation, value):
    # A fresh explicit context avoids dependence on ambient decimal precision,
    # rounding, traps, or task-local flags changed by application code.
    context = Context(prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                      capitals=1, clamp=0, flags=[], traps=[InvalidOperation, DivisionByZero, Overflow])
    decimal = Decimal.from_float(float(value))
    if operation == "log1p":
        return float(context.ln(context.add(Decimal(1), decimal)))
    return float(getattr(context, operation)(decimal))


def exp(value):
    return _decimal("exp", value) if current_contract() == PORTABLE_CONTRACT else math.exp(value)


def log(value):
    return _decimal("ln", value) if current_contract() == PORTABLE_CONTRACT else math.log(value)


def log1p(value):
    return _decimal("log1p", value) if current_contract() == PORTABLE_CONTRACT else math.log1p(value)


def sqrt(value):
    return _decimal("sqrt", value) if current_contract() == PORTABLE_CONTRACT else math.sqrt(value)
