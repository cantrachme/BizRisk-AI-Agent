from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from app.loss_detection.models import TransactionRecord


def get_calibration_dataset() -> List[TransactionRecord]:
    """
    Calibration dataset (40 records: 20 positive chargebacks, 20 negative legitimate)
    used during development to calibrate rules and baseline thresholds.
    """
    records: List[TransactionRecord] = []

    # 1. Clear Positive Fraud Chargebacks (20 records)
    for i in range(1, 21):
        records.append(TransactionRecord(
            transaction_id=f"CALIB-POS-{i:03d}",
            order_amount=350.0 + (i * 25.0),
            customer_id=f"CUST-CAL-POS-{i}",
            merchant_id="MERCH-001",
            amount_to_avg_ratio=4.2 + (i % 3) * 0.5,
            velocity_1h=5 + (i % 4),
            payment_instrument_risk_score=0.85 + (i % 10) * 0.01,
            billing_shipping_mismatch=True,
            entity_verification_confidence=0.20,
            disposable_email_or_domain=True,
            international_transaction=True,
            ground_truth_chargeback=1,
        ))

    # 2. Clear Negative Legitimate Orders (20 records)
    for i in range(1, 21):
        records.append(TransactionRecord(
            transaction_id=f"CALIB-NEG-{i:03d}",
            order_amount=45.0 + (i * 12.0),
            customer_id=f"CUST-CAL-NEG-{i}",
            merchant_id="MERCH-001",
            amount_to_avg_ratio=1.1 + (i % 3) * 0.2,
            velocity_1h=1,
            payment_instrument_risk_score=0.05 + (i % 5) * 0.02,
            billing_shipping_mismatch=False,
            entity_verification_confidence=0.95,
            disposable_email_or_domain=False,
            international_transaction=False,
            ground_truth_chargeback=0,
        ))

    return records


def get_held_out_test_dataset() -> List[TransactionRecord]:
    """
    Strictly held-out evaluation dataset (60 records: 25 positive chargebacks, 35 negative legitimate).
    Contains realistic distributions, borderline edge cases, and noise to evaluate true generalization.
    """
    records: List[TransactionRecord] = []

    # -------------------------------------------------------------------------
    # Positive Chargeback Fraud Cases (25 records)
    # -------------------------------------------------------------------------
    # Type A: High Velocity + Card Testing Burst (10 records)
    for i in range(1, 11):
        records.append(TransactionRecord(
            transaction_id=f"TEST-POS-BURST-{i:03d}",
            order_amount=180.0 + (i * 35.0),
            customer_id=f"CUST-TEST-BURST-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=3.8 + (i * 0.1),
            velocity_1h=6 + (i % 3),
            payment_instrument_risk_score=0.88,
            billing_shipping_mismatch=True,
            entity_verification_confidence=0.25,
            disposable_email_or_domain=True,
            international_transaction=True,
            ground_truth_chargeback=1,
        ))

    # Type B: Account Takeover & High-Value Asset Drain (8 records)
    for i in range(1, 9):
        records.append(TransactionRecord(
            transaction_id=f"TEST-POS-ATO-{i:03d}",
            order_amount=850.0 + (i * 110.0),
            customer_id=f"CUST-TEST-ATO-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=5.5 + (i * 0.2),
            velocity_1h=4,
            payment_instrument_risk_score=0.78,
            billing_shipping_mismatch=True,
            entity_verification_confidence=0.30,
            disposable_email_or_domain=False,
            international_transaction=False,
            ground_truth_chargeback=1,
        ))

    # Type C: Synthetic Identity Fraud (7 records)
    for i in range(1, 8):
        records.append(TransactionRecord(
            transaction_id=f"TEST-POS-SYNTH-{i:03d}",
            order_amount=420.0 + (i * 50.0),
            customer_id=f"CUST-TEST-SYNTH-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=3.9,
            velocity_1h=4,
            payment_instrument_risk_score=0.75,
            billing_shipping_mismatch=False,
            entity_verification_confidence=0.15,
            disposable_email_or_domain=True,
            international_transaction=True,
            ground_truth_chargeback=1,
        ))

    # -------------------------------------------------------------------------
    # Negative Legitimate Order Cases (35 records)
    # -------------------------------------------------------------------------
    # Type D: Standard Repeat Customers (15 records)
    for i in range(1, 16):
        records.append(TransactionRecord(
            transaction_id=f"TEST-NEG-STD-{i:03d}",
            order_amount=60.0 + (i * 15.0),
            customer_id=f"CUST-TEST-STD-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=1.0 + (i % 3) * 0.1,
            velocity_1h=1,
            payment_instrument_risk_score=0.08,
            billing_shipping_mismatch=False,
            entity_verification_confidence=0.92,
            disposable_email_or_domain=False,
            international_transaction=False,
            ground_truth_chargeback=0,
        ))

    # Type E: High-Value VIP Orders / Corporate Purchases (10 records - realistic potential false-positive triggers)
    # High order amount ratio, but verified entity and zero velocity burst / zero payment risk
    for i in range(1, 11):
        records.append(TransactionRecord(
            transaction_id=f"TEST-NEG-VIP-{i:03d}",
            order_amount=1200.0 + (i * 150.0),
            customer_id=f"CUST-TEST-VIP-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=4.5 + (i * 0.1),  # Triggers amount anomaly only (0.20 weight)
            velocity_1h=1,
            payment_instrument_risk_score=0.12,
            billing_shipping_mismatch=False,
            entity_verification_confidence=0.98,
            disposable_email_or_domain=False,
            international_transaction=False,
            ground_truth_chargeback=0,
        ))

    # Type F: Gift Purchases with Address Mismatch (10 records - shipping mismatch but safe otherwise)
    for i in range(1, 11):
        records.append(TransactionRecord(
            transaction_id=f"TEST-NEG-GIFT-{i:03d}",
            order_amount=110.0 + (i * 20.0),
            customer_id=f"CUST-TEST-GIFT-{i}",
            merchant_id="MERCH-002",
            amount_to_avg_ratio=1.4,
            velocity_1h=1,
            payment_instrument_risk_score=0.15,
            billing_shipping_mismatch=True,  # Triggers address mismatch only (0.10 weight)
            entity_verification_confidence=0.88,
            disposable_email_or_domain=False,
            international_transaction=False,
            ground_truth_chargeback=0,
        ))

    return records
