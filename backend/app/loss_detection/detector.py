from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.loss_detection.models import (
    DecisionAction,
    DetectionResult,
    SignalAttribution,
    TransactionRecord,
)


@dataclass
class DetectorConfig:
    """
    Configurable parameters and decision thresholds for the Chargeback Loss Detector.
    """
    decision_threshold: float = 0.65  # Threshold at or above which a transaction is flagged/blocked (RISK = 1)
    step_up_threshold: float = 0.45   # Threshold for secondary review / 3DS challenge
    
    # Rule weight contributions (must sum to 1.00)
    rule_weights: Dict[str, float] = field(default_factory=lambda: {
        "HIGH_VELOCITY_BURST": 0.25,
        "HIGH_ORDER_AMOUNT_ANOMALY": 0.20,
        "RISKY_PAYMENT_INSTRUMENT": 0.20,
        "DISPOSABLE_DOMAIN_OR_EMAIL": 0.15,
        "BILLING_SHIPPING_DISCORDANCE": 0.10,
        "UNVERIFIED_ENTITY_IDENTITY": 0.10,
    })

    # Rule trigger conditions
    velocity_burst_threshold: int = 4
    amount_ratio_anomaly_threshold: float = 3.5
    payment_risk_threshold: float = 0.70
    entity_verification_min_confidence: float = 0.50


import math


def _safe_float(val: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    try:
        if val is None or isinstance(val, bool):
            f = default
        else:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                f = default
    except (ValueError, TypeError):
        f = default
    if min_val is not None:
        f = max(min_val, f)
    if max_val is not None:
        f = min(max_val, f)
    return f


def _safe_int(val: Any, default: int = 0, min_val: Optional[int] = None) -> int:
    try:
        if val is None or isinstance(val, bool):
            i = default
        else:
            i = int(val)
    except (ValueError, TypeError):
        i = default
    if min_val is not None:
        i = max(min_val, i)
    return i


class ChargebackLossDetector:
    """
    Explainable, deterministic loss detector for Transaction Chargeback Fraud.
    Evaluates order velocity, amount anomalies, payment gateway indicators,
    address consistency, and entity verification to prevent chargeback loss.
    """

    def __init__(self, config: Optional[DetectorConfig] = None):
        self.config = config or DetectorConfig()

    def evaluate_transaction(self, tx: TransactionRecord) -> DetectionResult:
        """
        Evaluates an individual transaction and returns an explainable detection decision.
        """
        # Handle malformed, missing, NaN, or extreme fields safely
        order_amount = _safe_float(getattr(tx, "order_amount", 0.0), default=0.0, min_val=0.0)
        velocity = _safe_int(getattr(tx, "velocity_1h", 0), default=0, min_val=0)
        amount_ratio = _safe_float(getattr(tx, "amount_to_avg_ratio", 1.0), default=1.0, min_val=0.0)
        payment_risk = _safe_float(getattr(tx, "payment_instrument_risk_score", 0.0), default=0.0, min_val=0.0, max_val=1.0)
        billing_shipping_mismatch = bool(getattr(tx, "billing_shipping_mismatch", False))
        entity_conf = _safe_float(getattr(tx, "entity_verification_confidence", 1.0), default=1.0, min_val=0.0, max_val=1.0)
        disposable_email = bool(getattr(tx, "disposable_email_or_domain", False))

        triggered_signals: List[str] = []
        attributions: List[SignalAttribution] = []
        total_score: float = 0.0

        # Rule 1: High Velocity Burst
        if velocity >= self.config.velocity_burst_threshold:
            w = self.config.rule_weights.get("HIGH_VELOCITY_BURST", 0.25)
            total_score += w
            triggered_signals.append("HIGH_VELOCITY_BURST")
            attributions.append(SignalAttribution(
                signal_name="HIGH_VELOCITY_BURST",
                weight=w,
                contribution=w,
                description=f"Transaction velocity ({velocity} orders in 1h) exceeds burst threshold ({self.config.velocity_burst_threshold})",
                observed_value=velocity,
            ))

        # Rule 2: High Order Amount Anomaly
        if amount_ratio >= self.config.amount_ratio_anomaly_threshold:
            w = self.config.rule_weights.get("HIGH_ORDER_AMOUNT_ANOMALY", 0.20)
            total_score += w
            triggered_signals.append("HIGH_ORDER_AMOUNT_ANOMALY")
            attributions.append(SignalAttribution(
                signal_name="HIGH_ORDER_AMOUNT_ANOMALY",
                weight=w,
                contribution=w,
                description=f"Order amount (${order_amount:.2f}) is {amount_ratio:.1f}x the historical customer baseline (threshold: {self.config.amount_ratio_anomaly_threshold:.1f}x)",
                observed_value=amount_ratio,
            ))

        # Rule 3: Risky Payment Instrument
        if payment_risk >= self.config.payment_risk_threshold:
            w = self.config.rule_weights.get("RISKY_PAYMENT_INSTRUMENT", 0.20)
            total_score += w
            triggered_signals.append("RISKY_PAYMENT_INSTRUMENT")
            attributions.append(SignalAttribution(
                signal_name="RISKY_PAYMENT_INSTRUMENT",
                weight=w,
                contribution=w,
                description=f"Payment instrument gateway risk score ({payment_risk:.2f}) exceeds safety limit ({self.config.payment_risk_threshold:.2f})",
                observed_value=payment_risk,
            ))

        # Rule 4: Disposable Email / High-Risk Domain
        if disposable_email:
            w = self.config.rule_weights.get("DISPOSABLE_DOMAIN_OR_EMAIL", 0.15)
            total_score += w
            triggered_signals.append("DISPOSABLE_DOMAIN_OR_EMAIL")
            attributions.append(SignalAttribution(
                signal_name="DISPOSABLE_DOMAIN_OR_EMAIL",
                weight=w,
                contribution=w,
                description="Customer used a temporary/disposable email address or proxy domain",
                observed_value=True,
            ))

        # Rule 5: Billing / Shipping Address Discordance
        if billing_shipping_mismatch:
            w = self.config.rule_weights.get("BILLING_SHIPPING_DISCORDANCE", 0.10)
            total_score += w
            triggered_signals.append("BILLING_SHIPPING_DISCORDANCE")
            attributions.append(SignalAttribution(
                signal_name="BILLING_SHIPPING_DISCORDANCE",
                weight=w,
                contribution=w,
                description="Delivery shipping destination does not match the payment billing address",
                observed_value=True,
            ))

        # Rule 6: Unverified Entity Identity
        if entity_conf < self.config.entity_verification_min_confidence:
            w = self.config.rule_weights.get("UNVERIFIED_ENTITY_IDENTITY", 0.10)
            total_score += w
            triggered_signals.append("UNVERIFIED_ENTITY_IDENTITY")
            attributions.append(SignalAttribution(
                signal_name="UNVERIFIED_ENTITY_IDENTITY",
                weight=w,
                contribution=w,
                description=f"Entity background verification confidence ({entity_conf:.2f}) is below minimum requirement ({self.config.entity_verification_min_confidence:.2f})",
                observed_value=entity_conf,
            ))

        # Normalize score in [0.0, 1.0]
        normalized_score = round(min(1.0, max(0.0, total_score)), 4)

        # Decision threshold evaluation
        if normalized_score >= self.config.decision_threshold:
            prediction = 1
            decision = DecisionAction.BLOCK_CHARGEBACK_RISK
            explanation = (
                f"HIGH CHARGEBACK RISK (Score: {normalized_score:.2f} >= {self.config.decision_threshold:.2f}). "
                f"Action: Block payment capture. Primary risk drivers: {', '.join(triggered_signals)}."
            )
        elif normalized_score >= self.config.step_up_threshold:
            prediction = 0
            decision = DecisionAction.STEP_UP_REVIEW
            explanation = (
                f"MODERATE RISK (Score: {normalized_score:.2f} >= {self.config.step_up_threshold:.2f}). "
                f"Action: Route to 3D-Secure or manual verification. Triggered indicators: {', '.join(triggered_signals)}."
            )
        else:
            prediction = 0
            decision = DecisionAction.APPROVE
            explanation = (
                f"LOW CHARGEBACK RISK (Score: {normalized_score:.2f} < {self.config.step_up_threshold:.2f}). "
                f"Action: Approve transaction. Standard operational parameters satisfied."
            )

        return DetectionResult(
            transaction_id=str(getattr(tx, "transaction_id", "UNKNOWN")),
            prediction=prediction,
            risk_score=normalized_score,
            decision=decision,
            triggered_signals=triggered_signals,
            signal_attributions=attributions,
            explanation=explanation,
        )

    def batch_evaluate(self, transactions: List[TransactionRecord]) -> List[DetectionResult]:
        """Evaluates a collection of transactions deterministically."""
        return [self.evaluate_transaction(tx) for tx in transactions]
