from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromotionDecision:
    approved: bool
    reason: str


def promote_only_after_evidence(
    *,
    validation_roi: float,
    validation_trades: int,
    validation_drawdown: float,
    forward_paper_roi: float | None = None,
    minimum_roi: float = 0.03,
    minimum_trades: int = 30,
    maximum_drawdown: float = -0.10,
) -> PromotionDecision:
    """Common promotion rule for research modules.

    Negative drawdown numbers are expected. A drawdown of -0.20 is worse than
    a limit of -0.10 and therefore fails.
    """
    if validation_trades < minimum_trades:
        return PromotionDecision(False, f"validation trades {validation_trades} < {minimum_trades}")
    if validation_roi < minimum_roi:
        return PromotionDecision(False, f"validation ROI {validation_roi:.4f} < {minimum_roi:.4f}")
    if validation_drawdown < maximum_drawdown:
        return PromotionDecision(False, f"validation drawdown {validation_drawdown:.4f} worse than {maximum_drawdown:.4f}")
    if forward_paper_roi is not None and forward_paper_roi <= 0:
        return PromotionDecision(False, "forward paper ROI is not positive")
    return PromotionDecision(True, "validation and forward evidence gates passed")
