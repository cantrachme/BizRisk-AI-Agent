from __future__ import annotations

from typing import List, Optional

from app.loss_detection.detector import ChargebackLossDetector
from app.loss_detection.models import (
    ConfusionMatrix,
    CostModel,
    EvaluationMetrics,
    TransactionRecord,
)


def evaluate_detector(
    detector: ChargebackLossDetector,
    dataset: List[TransactionRecord],
    cost_model: Optional[CostModel] = None,
) -> EvaluationMetrics:
    """
    Evaluates a loss detector against a labelled transaction dataset and computes
    precision, recall, error rates, and financial cost impact.
    """
    cost_mod = cost_model or CostModel()

    tp = 0
    tn = 0
    fp = 0
    fn = 0

    fp_cost_total = 0.0
    fn_cost_total = 0.0
    baseline_cost_total = 0.0

    for tx in dataset:
        y_true = tx.ground_truth_chargeback
        if y_true is None:
            continue

        result = detector.evaluate_transaction(tx)
        y_pred = result.prediction
        amount = tx.order_amount

        # Baseline loss if detector did not exist (all transactions approved -> 100% of chargebacks hit merchant)
        if y_true == 1:
            baseline_cost_total += cost_mod.calculate_false_negative_cost(amount)

        if y_true == 1 and y_pred == 1:
            tp += 1
        elif y_true == 0 and y_pred == 0:
            tn += 1
        elif y_true == 0 and y_pred == 1:
            fp += 1
            fp_cost_total += cost_mod.calculate_false_positive_cost(amount)
        elif y_true == 1 and y_pred == 0:
            fn += 1
            fn_cost_total += cost_mod.calculate_false_negative_cost(amount)

    cm = ConfusionMatrix(
        true_positives=tp,
        true_negatives=tn,
        false_positives=fp,
        false_negatives=fn,
    )

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) > 0 else 0.0
    fpr = round(fp / (fp + tn), 4) if (fp + tn) > 0 else 0.0
    fnr = round(fn / (fn + tp), 4) if (fn + tp) > 0 else 0.0

    total_loss = round(fp_cost_total + fn_cost_total, 2)
    net_savings = round(baseline_cost_total - total_loss, 2)

    return EvaluationMetrics(
        confusion_matrix=cm,
        precision=precision,
        recall=recall,
        f1_score=f1,
        false_positive_rate=fpr,
        false_negative_rate=fnr,
        false_positive_cost=round(fp_cost_total, 2),
        false_negative_cost=round(fn_cost_total, 2),
        total_financial_loss=total_loss,
        baseline_cost_without_detector=round(baseline_cost_total, 2),
        net_cost_savings=net_savings,
    )
