from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class LossClass(str, Enum):
    CHARGEBACK_FRAUD = "CHARGEBACK_FRAUD"


class DecisionAction(str, Enum):
    BLOCK_CHARGEBACK_RISK = "BLOCK_CHARGEBACK_RISK"
    STEP_UP_REVIEW = "STEP_UP_REVIEW"
    APPROVE = "APPROVE"


@dataclass
class TransactionRecord:
    """
    Standard transaction input record representing an individual order or payment event.
    """
    transaction_id: str
    order_amount: float
    customer_id: str
    merchant_id: str
    amount_to_avg_ratio: float
    velocity_1h: int
    payment_instrument_risk_score: float
    billing_shipping_mismatch: bool
    entity_verification_confidence: float
    disposable_email_or_domain: bool
    international_transaction: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ground_truth_chargeback: Optional[int] = None


@dataclass
class SignalAttribution:
    """
    Explainable signal attribution detailing why a specific rule contributed to the risk score.
    """
    signal_name: str
    weight: float
    contribution: float
    description: str
    observed_value: Any


@dataclass
class DetectionResult:
    """
    Structured outcome of the loss detection evaluation.
    """
    transaction_id: str
    prediction: int  # 1: High Risk / Action Required, 0: Low Risk / Approve
    risk_score: float  # Normalized 0.0 to 1.0
    decision: DecisionAction
    triggered_signals: List[str]
    signal_attributions: List[SignalAttribution]
    explanation: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CostModel:
    """
    Explicit financial cost model quantifying false-positive and false-negative losses.
    """
    lost_margin_rate: float = 0.15  # 15% gross merchant margin lost on false positive rejection
    fixed_customer_friction_cost: float = 10.0  # $10 customer recovery friction cost on false positive
    fixed_chargeback_fee: float = 15.0  # $15 acquiring bank penalty fee on chargeback
    chargeback_loss_rate: float = 1.00  # 100% of order amount lost on fraudulent chargeback

    def calculate_false_positive_cost(self, order_amount: float) -> float:
        """Cost incurred when a legitimate order is incorrectly blocked."""
        try:
            amt = max(0.0, float(order_amount or 0.0))
            if amt != amt:  # NaN check
                amt = 0.0
        except (ValueError, TypeError):
            amt = 0.0
        return (amt * self.lost_margin_rate) + self.fixed_customer_friction_cost

    def calculate_false_negative_cost(self, order_amount: float) -> float:
        """Cost incurred when a fraudulent chargeback is incorrectly approved."""
        try:
            amt = max(0.0, float(order_amount or 0.0))
            if amt != amt:  # NaN check
                amt = 0.0
        except (ValueError, TypeError):
            amt = 0.0
        return (amt * self.chargeback_loss_rate) + self.fixed_chargeback_fee


@dataclass
class ConfusionMatrix:
    true_positives: int = 0
    true_negatives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def total_samples(self) -> int:
        return self.true_positives + self.true_negatives + self.false_positives + self.false_negatives


@dataclass
class EvaluationMetrics:
    """
    Comprehensive evaluation summary on a dataset partition.
    """
    confusion_matrix: ConfusionMatrix
    precision: float
    recall: float
    f1_score: float
    false_positive_rate: float
    false_negative_rate: float
    false_positive_cost: float
    false_negative_cost: float
    total_financial_loss: float
    baseline_cost_without_detector: float
    net_cost_savings: float
