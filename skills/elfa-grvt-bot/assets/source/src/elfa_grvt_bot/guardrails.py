from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .registry import Strategy


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Reject:
    reason: str
    category: str  # alert category, e.g. "guardrail_rejected"


GuardrailResult = Union[Allow, Reject]


def check_guardrails(
    *,
    strategy: Strategy,
    current_mid: float,
    receiver_env: str,
) -> GuardrailResult:
    # Symbol existence is GRVT's responsibility, not ours: a strategy with an
    # unsupported symbol will fail at fetch_mid_price or order placement and
    # surface as a grvt_other / grvt_error alert. Authoring-time validation
    # (Claude verifying the symbol via fetch_market before creating the
    # strategy) is the user-facing safeguard. We don't second-guess GRVT
    # here.
    if strategy.status != "active":
        return Reject(
            reason=f"strategy status is {strategy.status!r}, only 'active' fires",
            category="guardrail_status",  # distinct from 'guardrail_rejected' so receiver can log-only
        )
    if strategy.env != receiver_env:
        return Reject(
            reason=f"strategy env {strategy.env!r} does not match receiver env {receiver_env!r}",
            category="guardrail_rejected",
        )
    notional = current_mid * strategy.amount
    if notional > strategy.max_notional_usd:
        return Reject(
            reason=(
                f"estimated notional {notional:.2f} USD exceeds cap "
                f"{strategy.max_notional_usd:.2f} USD"
            ),
            category="guardrail_rejected",
        )
    return Allow()
