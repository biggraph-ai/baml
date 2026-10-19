"""Explicit capability contracts; manifests contain metadata, never credentials."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from threading import Event, TIMEOUT_MAX
from typing import Callable


def validate_effect(value: object) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in {
        "read",
        "mutation",
        "recovery",
    }:
        raise ValueError("Effect requires kind read, mutation, or recovery")
    allowed = {
        "kind",
        "capability",
        "receiptType",
        "idempotent",
        "reconcilable",
        "maxAttempts",
        "maxReconciliations",
        "timeoutSeconds",
        "reconcileTimeoutSeconds",
        "irreversiblePolicy",
    }
    if set(value) - allowed:
        raise ValueError(
            "Unknown effect metadata fields; manifests must contain contracts, not credentials"
        )
    if value["kind"] == "read" and set(value) - {
        "kind",
        "capability",
        "timeoutSeconds",
    }:
        raise ValueError(
            "Read capabilities support only capability and timeoutSeconds metadata"
        )
    if value["kind"] != "recovery" and "maxAttempts" in value:
        raise ValueError(
            "Forward retry attempts must be explicit graph visits, not manifest retries"
        )
    result = dict(value)
    if (
        not isinstance(result.get("capability"), str)
        or not result["capability"].strip()
    ):
        raise ValueError("Effect requires a named authorization capability")
    if result["kind"] == "read" and "timeoutSeconds" in result:
        number = result["timeoutSeconds"]
        if type(number) not in {int, float} or not 0 < number <= TIMEOUT_MAX:
            raise ValueError(
                "Read timeoutSeconds must be finite and within the platform wait limit"
            )
    if result["kind"] in {"mutation", "recovery"}:
        for flag in ["idempotent", "reconcilable"]:
            if result.get(flag) is not True:
                raise ValueError(f"Mutating/recovery capability requires {flag}: true")
        if not isinstance(result.get("receiptType"), str):
            raise ValueError("Mutation/recovery requires a typed receipt contract")
        for key, default in [("maxAttempts", 2), ("maxReconciliations", 3)]:
            if key == "maxAttempts" and result["kind"] != "recovery":
                continue
            result.setdefault(key, default)
            if type(result[key]) is not int or not 1 <= result[key] <= 100:
                raise ValueError(f"{key} must be an integer in [1,100]")
        for key in ["timeoutSeconds", "reconcileTimeoutSeconds"]:
            result.setdefault(key, 1.0)
            number = result[key]
            if (
                type(number) not in {int, float}
                or not 0 < number <= TIMEOUT_MAX
                or not math.isfinite(number)
            ):
                raise ValueError(
                    f"{key} must be finite and within the platform wait limit"
                )
        if "irreversiblePolicy" in result and (
            result["kind"] != "mutation"
            or not isinstance(result["irreversiblePolicy"], str)
            or not result["irreversiblePolicy"].strip()
        ):
            raise ValueError(
                "irreversiblePolicy must be an explicit policy name for a mutation"
            )
    return result


@dataclass(frozen=True)
class InvocationContext:
    run_id: str
    invocation_id: str
    idempotency_key: str
    fence: int
    phase: str
    receipt: object = None
    original_state: object = None
    cancel_requested: Event = field(default_factory=Event, compare=False)
    cancel_acknowledged: Event = field(default_factory=Event, compare=False)


@dataclass(frozen=True)
class AuthorizationRequest:
    capability: str
    phase: str
    context: InvocationContext
    state: dict
    irreversible_policy: str | None = None


@dataclass(frozen=True)
class EffectResult:
    value: object
    receipt: object


@dataclass(frozen=True)
class Reconciliation:
    # not_committed is FINAL: the adapter guarantees this key cannot commit later.
    status: str  # completed | not_committed | unknown
    result: EffectResult | None = None


@dataclass(frozen=True)
class Capability:
    invoke: (
        Callable  # (context, state) -> EffectResult for mutation, state/result for read
    )
    reconcile: Callable | None = None  # (context) -> Reconciliation


@dataclass(frozen=True)
class IsolatedTask:
    invoke: Callable  # pure/local (state) -> result, executed in a killable process
