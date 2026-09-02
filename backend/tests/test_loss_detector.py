import math
import pytest
from datetime import datetime, timezone

from app.loss_detection import (
    ChargebackLossDetector,
    CostModel,
    DecisionAction,
    DetectorConfig,
    TransactionRecord,
    evaluate_detector,
    get_calibration_dataset,
    get_held_out_test_dataset,
)


# ==============================================================================
# 1. POSITIVE DETECTION
# ==============================================================================

def test_positive_chargeback_detection():
    detector = ChargebackLossDetector()
    tx = TransactionRecord(
        transaction_id="TX-POS-001",
        order_amount=850.0,
        customer_id="CUST-001",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=4.5,            # Trigger (+0.20)
        velocity_1h=5,                      # Trigger (+0.25)
        payment_instrument_risk_score=0.85, # Trigger (+0.20)
        billing_shipping_mismatch=True,     # Trigger (+0.10)
        entity_verification_confidence=0.20,# Trigger (+0.10)
        disposable_email_or_domain=True,    # Trigger (+0.15)
        ground_truth_chargeback=1,
    )

    result = detector.evaluate_transaction(tx)
    assert result.prediction == 1
    assert result.decision == DecisionAction.BLOCK_CHARGEBACK_RISK
    assert result.risk_score >= 0.85
    assert "HIGH_VELOCITY_BURST" in result.triggered_signals
    assert "RISKY_PAYMENT_INSTRUMENT" in result.triggered_signals
    assert "HIGH_ORDER_AMOUNT_ANOMALY" in result.triggered_signals


# ==============================================================================
# 2. NEGATIVE DETECTION
# ==============================================================================

def test_negative_legitimate_order_detection():
    detector = ChargebackLossDetector()
    tx = TransactionRecord(
        transaction_id="TX-NEG-001",
        order_amount=65.0,
        customer_id="CUST-002",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=1.0,
        velocity_1h=1,
        payment_instrument_risk_score=0.05,
        billing_shipping_mismatch=False,
        entity_verification_confidence=0.95,
        disposable_email_or_domain=False,
        ground_truth_chargeback=0,
    )

    result = detector.evaluate_transaction(tx)
    assert result.prediction == 0
    assert result.decision == DecisionAction.APPROVE
    assert result.risk_score == 0.0
    assert len(result.triggered_signals) == 0
    assert "LOW CHARGEBACK RISK" in result.explanation


# ==============================================================================
# 3. BORDERLINE CASE
# ==============================================================================

def test_borderline_moderate_risk_triggers_step_up_review():
    detector = ChargebackLossDetector()
    # Triggers: Velocity (0.25) + Disposable Email (0.15) + Billing Mismatch (0.10) = 0.50
    # 0.45 <= Score 0.50 < 0.65 -> Step-up review (prediction 0, decision STEP_UP_REVIEW)
    tx = TransactionRecord(
        transaction_id="TX-BORDER-001",
        order_amount=120.0,
        customer_id="CUST-003",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=1.2,
        velocity_1h=4,                      # Trigger (+0.25)
        payment_instrument_risk_score=0.20,
        billing_shipping_mismatch=True,     # Trigger (+0.10)
        entity_verification_confidence=0.80,
        disposable_email_or_domain=True,    # Trigger (+0.15)
        ground_truth_chargeback=0,
    )

    result = detector.evaluate_transaction(tx)
    assert result.risk_score == 0.50
    assert result.prediction == 0
    assert result.decision == DecisionAction.STEP_UP_REVIEW
    assert "MODERATE RISK" in result.explanation


# ==============================================================================
# 4. THRESHOLD BEHAVIOR
# ==============================================================================

def test_threshold_behavior_exact_boundary():
    config = DetectorConfig(decision_threshold=0.65, step_up_threshold=0.45)
    detector = ChargebackLossDetector(config=config)

    # 1. Strictly below decision threshold (Score 0.60 < 0.65)
    tx_below = TransactionRecord(
        transaction_id="TX-BELOW", order_amount=100.0, customer_id="C1", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=4, payment_instrument_risk_score=0.10, # 0.20 + 0.25 = 0.45
        billing_shipping_mismatch=False, entity_verification_confidence=0.10, disposable_email_or_domain=False, # +0.10 = 0.55
    )
    res_below = detector.evaluate_transaction(tx_below)
    assert res_below.risk_score == 0.55
    assert res_below.prediction == 0

    # 2. Exactly at decision threshold (Score 0.65 == 0.65)
    # Velocity (0.25) + Amount (0.20) + Risky Payment (0.20) = 0.65
    tx_exact = TransactionRecord(
        transaction_id="TX-EXACT", order_amount=250.0, customer_id="C2", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=4, payment_instrument_risk_score=0.75,
        billing_shipping_mismatch=False, entity_verification_confidence=0.90, disposable_email_or_domain=False,
    )
    res_exact = detector.evaluate_transaction(tx_exact)
    assert res_exact.risk_score == 0.65
    assert res_exact.prediction == 1
    assert res_exact.decision == DecisionAction.BLOCK_CHARGEBACK_RISK

    # 3. Above decision threshold (Score 0.75 > 0.65)
    # Velocity (0.25) + Amount (0.20) + Risky Payment (0.20) + Billing Mismatch (0.10) = 0.75
    tx_above = TransactionRecord(
        transaction_id="TX-ABOVE", order_amount=300.0, customer_id="C3", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=4, payment_instrument_risk_score=0.75,
        billing_shipping_mismatch=True, entity_verification_confidence=0.90, disposable_email_or_domain=False,
    )
    res_above = detector.evaluate_transaction(tx_above)
    assert res_above.risk_score == 0.75
    assert res_above.prediction == 1


# ==============================================================================
# 5. EXPLANATION GENERATION
# ==============================================================================

def test_explanation_generation_factual_and_grounded():
    detector = ChargebackLossDetector()
    tx = TransactionRecord(
        transaction_id="TX-EXP-001",
        order_amount=500.0,
        customer_id="CUST-004",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=4.0,
        velocity_1h=5,
        payment_instrument_risk_score=0.85,
        billing_shipping_mismatch=True,
        entity_verification_confidence=0.90,
        disposable_email_or_domain=False,
    )

    result = detector.evaluate_transaction(tx)
    assert "HIGH CHARGEBACK RISK" in result.explanation
    assert "HIGH_VELOCITY_BURST" in result.explanation
    assert "RISKY_PAYMENT_INSTRUMENT" in result.explanation
    assert "HIGH_ORDER_AMOUNT_ANOMALY" in result.explanation
    # Must not claim unobserved triggers
    assert "DISPOSABLE_DOMAIN_OR_EMAIL" not in result.explanation
    assert "UNVERIFIED_ENTITY_IDENTITY" not in result.explanation


# ==============================================================================
# 6. SIGNAL ATTRIBUTION
# ==============================================================================

def test_signal_attribution_details():
    detector = ChargebackLossDetector()
    tx = TransactionRecord(
        transaction_id="TX-ATTR-001",
        order_amount=200.0,
        customer_id="CUST-005",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=1.0,
        velocity_1h=6,
        payment_instrument_risk_score=0.10,
        billing_shipping_mismatch=True,
        entity_verification_confidence=0.95,
        disposable_email_or_domain=False,
    )

    result = detector.evaluate_transaction(tx)
    assert len(result.signal_attributions) == 2
    attr_names = [a.signal_name for a in result.signal_attributions]
    assert "HIGH_VELOCITY_BURST" in attr_names
    assert "BILLING_SHIPPING_DISCORDANCE" in attr_names

    burst_attr = next(a for a in result.signal_attributions if a.signal_name == "HIGH_VELOCITY_BURST")
    assert burst_attr.weight == 0.25
    assert burst_attr.contribution == 0.25
    assert burst_attr.observed_value == 6


# ==============================================================================
# 7. FALSE-POSITIVE CALCULATION
# ==============================================================================

def test_false_positive_metrics_calculation():
    detector = ChargebackLossDetector()
    # A single legitimate transaction misclassified as high risk
    legit_tx = TransactionRecord(
        transaction_id="TX-FP-001",
        order_amount=100.0,
        customer_id="CUST-FP",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=4.0,            # 0.20
        velocity_1h=5,                      # 0.25
        payment_instrument_risk_score=0.80, # 0.20 -> Total 0.65 (Flagged!)
        billing_shipping_mismatch=False,
        entity_verification_confidence=0.90,
        disposable_email_or_domain=False,
        ground_truth_chargeback=0,          # Actually legitimate
    )

    metrics = evaluate_detector(detector, [legit_tx])
    assert metrics.confusion_matrix.false_positives == 1
    assert metrics.confusion_matrix.true_positives == 0
    assert metrics.confusion_matrix.false_negatives == 0
    assert metrics.false_positive_rate == 1.0


# ==============================================================================
# 8. FALSE-NEGATIVE CALCULATION
# ==============================================================================

def test_false_negative_metrics_calculation():
    detector = ChargebackLossDetector()
    # A fraudulent chargeback missed by detector
    fraud_tx = TransactionRecord(
        transaction_id="TX-FN-001",
        order_amount=80.0,
        customer_id="CUST-FN",
        merchant_id="MERCH-001",
        amount_to_avg_ratio=1.1,
        velocity_1h=1,
        payment_instrument_risk_score=0.10,
        billing_shipping_mismatch=False,
        entity_verification_confidence=0.90,
        disposable_email_or_domain=False,
        ground_truth_chargeback=1,          # Actually fraud
    )

    metrics = evaluate_detector(detector, [fraud_tx])
    assert metrics.confusion_matrix.false_negatives == 1
    assert metrics.confusion_matrix.true_positives == 0
    assert metrics.false_negative_rate == 1.0
    assert metrics.recall == 0.0


# ==============================================================================
# 9. PRECISION CALCULATION
# ==============================================================================

def test_precision_calculation():
    detector = ChargebackLossDetector()
    tx_tp = TransactionRecord(
        transaction_id="TX-TP", order_amount=200.0, customer_id="C1", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=5, payment_instrument_risk_score=0.85,
        billing_shipping_mismatch=True, entity_verification_confidence=0.10, disposable_email_or_domain=True,
        ground_truth_chargeback=1,
    )
    tx_fp = TransactionRecord(
        transaction_id="TX-FP", order_amount=150.0, customer_id="C2", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=5, payment_instrument_risk_score=0.85,
        billing_shipping_mismatch=True, entity_verification_confidence=0.10, disposable_email_or_domain=True,
        ground_truth_chargeback=0,
    )

    metrics = evaluate_detector(detector, [tx_tp, tx_fp])
    # TP = 1, FP = 1 -> Precision = 1 / (1 + 1) = 0.50
    assert metrics.confusion_matrix.true_positives == 1
    assert metrics.confusion_matrix.false_positives == 1
    assert metrics.precision == 0.50


# ==============================================================================
# 10. RECALL CALCULATION
# ==============================================================================

def test_recall_calculation():
    detector = ChargebackLossDetector()
    tx_tp = TransactionRecord(
        transaction_id="TX-TP", order_amount=200.0, customer_id="C1", merchant_id="M1",
        amount_to_avg_ratio=4.0, velocity_1h=5, payment_instrument_risk_score=0.85,
        billing_shipping_mismatch=True, entity_verification_confidence=0.10, disposable_email_or_domain=True,
        ground_truth_chargeback=1,
    )
    tx_fn = TransactionRecord(
        transaction_id="TX-FN", order_amount=80.0, customer_id="C2", merchant_id="M1",
        amount_to_avg_ratio=1.0, velocity_1h=1, payment_instrument_risk_score=0.05,
        billing_shipping_mismatch=False, entity_verification_confidence=0.95, disposable_email_or_domain=False,
        ground_truth_chargeback=1,
    )

    metrics = evaluate_detector(detector, [tx_tp, tx_fn])
    # TP = 1, FN = 1 -> Recall = 1 / (1 + 1) = 0.50
    assert metrics.confusion_matrix.true_positives == 1
    assert metrics.confusion_matrix.false_negatives == 1
    assert metrics.recall == 0.50


# ==============================================================================
# 11. FALSE-POSITIVE COST CALCULATION
# ==============================================================================

def test_false_positive_cost_formula():
    cost_model = CostModel(lost_margin_rate=0.20, fixed_customer_friction_cost=15.0)
    # Order amount $500: Cost = ($500 * 0.20) + $15 = $100 + $15 = $115.0
    fp_cost = cost_model.calculate_false_positive_cost(500.0)
    assert fp_cost == 115.0


# ==============================================================================
# 12. FALSE-NEGATIVE COST CALCULATION
# ==============================================================================

def test_false_negative_cost_formula():
    cost_model = CostModel(chargeback_loss_rate=1.00, fixed_chargeback_fee=25.0)
    # Order amount $400: Cost = ($400 * 1.0) + $25 = $425.0
    fn_cost = cost_model.calculate_false_negative_cost(400.0)
    assert fn_cost == 425.0


# ==============================================================================
# 13. TOTAL FINANCIAL COST & NET SAVINGS CALCULATION
# ==============================================================================

def test_total_cost_and_net_savings_on_dataset():
    detector = ChargebackLossDetector()
    cost_model = CostModel(lost_margin_rate=0.15, fixed_customer_friction_cost=10.0, fixed_chargeback_fee=15.0)

    dataset = get_calibration_dataset()
    metrics = evaluate_detector(detector, dataset, cost_model=cost_model)

    assert metrics.confusion_matrix.total_samples == 40
    assert metrics.confusion_matrix.true_positives == 20
    assert metrics.confusion_matrix.true_negatives == 20
    assert metrics.confusion_matrix.false_positives == 0
    assert metrics.confusion_matrix.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.total_financial_loss == 0.0
    assert metrics.net_cost_savings == metrics.baseline_cost_without_detector
    assert metrics.net_cost_savings > 10000.0


# ==============================================================================
# 14. HELD-OUT DATASET SEPARATION
# ==============================================================================

def test_held_out_dataset_strict_separation():
    calib = get_calibration_dataset()
    test_set = get_held_out_test_dataset()

    calib_ids = {t.transaction_id for t in calib}
    test_ids = {t.transaction_id for t in test_set}

    # Verify zero ID overlap
    assert len(calib_ids.intersection(test_ids)) == 0
    assert len(calib) == 40
    assert len(test_set) == 60

    # Verify distinct customers
    calib_custs = {t.customer_id for t in calib}
    test_custs = {t.customer_id for t in test_set}
    assert len(calib_custs.intersection(test_custs)) == 0


# ==============================================================================
# 15. DETERMINISTIC REPEATED EVALUATION
# ==============================================================================

def test_deterministic_reproducible_evaluation():
    detector = ChargebackLossDetector()
    test_set = get_held_out_test_dataset()

    res1 = evaluate_detector(detector, test_set)
    res2 = evaluate_detector(detector, test_set)

    assert res1.precision == res2.precision
    assert res1.recall == res2.recall
    assert res1.f1_score == res2.f1_score
    assert res1.total_financial_loss == res2.total_financial_loss
    assert res1.confusion_matrix.true_positives == res2.confusion_matrix.true_positives
    assert res1.confusion_matrix.false_positives == res2.confusion_matrix.false_positives


# ==============================================================================
# 16. MALFORMED INPUT HANDLING
# ==============================================================================

def test_malformed_and_extreme_inputs():
    detector = ChargebackLossDetector()

    # Empty / None / Outlier attributes
    tx_malformed = TransactionRecord(
        transaction_id="TX-MALFORMED",
        order_amount=-500.0,                # Negative amount handled cleanly
        customer_id="",
        merchant_id="",
        amount_to_avg_ratio=-10.0,
        velocity_1h=-5,
        payment_instrument_risk_score=999.0, # Clamped to 1.0
        billing_shipping_mismatch=None,      # Handled safely
        entity_verification_confidence=-1.0, # Clamped to 0.0
        disposable_email_or_domain=None,
    )

    result = detector.evaluate_transaction(tx_malformed)
    assert result.transaction_id == "TX-MALFORMED"
    assert 0.0 <= result.risk_score <= 1.0
    assert result.decision in DecisionAction
    assert isinstance(result.explanation, str)


# ==============================================================================
# 17. BOUNDARY MATRIX TESTS (0.00, 0.44, 0.449999, 0.45, 0.649999, 0.65, 1.00)
# ==============================================================================

def test_decision_boundary_matrix():
    detector = ChargebackLossDetector()

    # Synthetic scores to test decision mapping
    test_cases = [
        (0.00, DecisionAction.APPROVE, 0),
        (0.44, DecisionAction.APPROVE, 0),
        (0.4499, DecisionAction.APPROVE, 0),
        (0.45, DecisionAction.STEP_UP_REVIEW, 0),
        (0.6499, DecisionAction.STEP_UP_REVIEW, 0),
        (0.65, DecisionAction.BLOCK_CHARGEBACK_RISK, 1),
        (1.00, DecisionAction.BLOCK_CHARGEBACK_RISK, 1),
    ]

    for score, expected_decision, expected_pred in test_cases:
        # Construct custom config where a single dummy rule matches exact score
        config = DetectorConfig(
            decision_threshold=0.65,
            step_up_threshold=0.45,
            rule_weights={"HIGH_VELOCITY_BURST": score},
            velocity_burst_threshold=1,
        )
        det = ChargebackLossDetector(config=config)
        tx = TransactionRecord(
            transaction_id=f"TX-SCORE-{score}", order_amount=100.0, customer_id="C", merchant_id="M",
            amount_to_avg_ratio=1.0, velocity_1h=1, payment_instrument_risk_score=0.0,
            billing_shipping_mismatch=False, entity_verification_confidence=1.0, disposable_email_or_domain=False,
        )
        res = det.evaluate_transaction(tx)
        assert res.risk_score == round(score, 4)
        assert res.decision == expected_decision
        assert res.prediction == expected_pred


# ==============================================================================
# 18. NAN AND INFINITY INPUT HANDLING
# ==============================================================================

def test_nan_and_infinity_inputs():
    detector = ChargebackLossDetector()
    tx_nan = TransactionRecord(
        transaction_id="TX-NAN",
        order_amount=float("nan"),
        customer_id="C-NAN",
        merchant_id="M-NAN",
        amount_to_avg_ratio=float("nan"),
        velocity_1h=5,
        payment_instrument_risk_score=float("nan"),
        billing_shipping_mismatch=False,
        entity_verification_confidence=float("nan"),
        disposable_email_or_domain=False,
    )
    res_nan = detector.evaluate_transaction(tx_nan)
    assert not math.isnan(res_nan.risk_score)
    assert 0.0 <= res_nan.risk_score <= 1.0

    tx_inf = TransactionRecord(
        transaction_id="TX-INF",
        order_amount=float("inf"),
        customer_id="C-INF",
        merchant_id="M-INF",
        amount_to_avg_ratio=float("inf"),
        velocity_1h=5,
        payment_instrument_risk_score=float("inf"),
        billing_shipping_mismatch=False,
        entity_verification_confidence=float("-inf"),
        disposable_email_or_domain=False,
    )
    res_inf = detector.evaluate_transaction(tx_inf)
    assert not math.isinf(res_inf.risk_score)
    assert 0.0 <= res_inf.risk_score <= 1.0


# ==============================================================================
# 19. EMPTY, ALL-POSITIVE, ALL-NEGATIVE EVALUATION
# ==============================================================================

def test_empty_dataset_evaluation():
    detector = ChargebackLossDetector()
    metrics = evaluate_detector(detector, [])
    assert metrics.confusion_matrix.total_samples == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1_score == 0.0
    assert metrics.total_financial_loss == 0.0


def test_all_positive_dataset_evaluation():
    detector = ChargebackLossDetector()
    tx_pos = TransactionRecord(
        transaction_id="TX-ALL-POS", order_amount=300.0, customer_id="C", merchant_id="M",
        amount_to_avg_ratio=5.0, velocity_1h=5, payment_instrument_risk_score=0.90,
        billing_shipping_mismatch=True, entity_verification_confidence=0.10, disposable_email_or_domain=True,
        ground_truth_chargeback=1,
    )
    metrics = evaluate_detector(detector, [tx_pos])
    assert metrics.confusion_matrix.true_positives == 1
    assert metrics.confusion_matrix.false_positives == 0
    assert metrics.confusion_matrix.false_negatives == 0
    assert metrics.confusion_matrix.true_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


def test_all_negative_dataset_evaluation():
    detector = ChargebackLossDetector()
    tx_neg = TransactionRecord(
        transaction_id="TX-ALL-NEG", order_amount=50.0, customer_id="C", merchant_id="M",
        amount_to_avg_ratio=1.0, velocity_1h=1, payment_instrument_risk_score=0.05,
        billing_shipping_mismatch=False, entity_verification_confidence=0.95, disposable_email_or_domain=False,
        ground_truth_chargeback=0,
    )
    metrics = evaluate_detector(detector, [tx_neg])
    assert metrics.confusion_matrix.true_positives == 0
    assert metrics.confusion_matrix.true_negatives == 1
    assert metrics.confusion_matrix.false_positives == 0
    assert metrics.confusion_matrix.false_negatives == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.false_positive_rate == 0.0


# ==============================================================================
# 20. COST MODEL EXTREME VALUES AND SERIALIZATION
# ==============================================================================

def test_cost_model_extreme_values():
    cm = CostModel(lost_margin_rate=0.15, fixed_customer_friction_cost=10.0, fixed_chargeback_fee=15.0)

    # 1. Zero amount
    assert cm.calculate_false_positive_cost(0.0) == 10.0
    assert cm.calculate_false_negative_cost(0.0) == 15.0

    # 2. Large amount
    assert cm.calculate_false_positive_cost(10_000_000.0) == 1_500_010.0
    assert cm.calculate_false_negative_cost(10_000_000.0) == 10_000_015.0

    # 3. Negative amount (safely treated as 0)
    assert cm.calculate_false_positive_cost(-50.0) == 10.0
    assert cm.calculate_false_negative_cost(-50.0) == 15.0

    # 4. Decimal precision
    assert round(cm.calculate_false_positive_cost(123.4567), 4) == round((123.4567 * 0.15) + 10.0, 4)


def test_dataclass_serialization():
    from dataclasses import asdict

    detector = ChargebackLossDetector()
    tx = TransactionRecord(
        transaction_id="TX-SER", order_amount=100.0, customer_id="C", merchant_id="M",
        amount_to_avg_ratio=1.0, velocity_1h=1, payment_instrument_risk_score=0.10,
        billing_shipping_mismatch=False, entity_verification_confidence=0.90, disposable_email_or_domain=False,
    )
    result = detector.evaluate_transaction(tx)
    res_dict = asdict(result)
    assert res_dict["transaction_id"] == "TX-SER"
    assert res_dict["decision"] == DecisionAction.APPROVE.value
    assert isinstance(res_dict["signal_attributions"], list)
    assert isinstance(res_dict["explanation"], str)

