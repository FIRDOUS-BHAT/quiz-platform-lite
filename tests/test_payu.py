from __future__ import annotations

import hashlib

from app.services.payu import generate_payment_hash, normalize_amount, verify_payment_response_hash


def test_normalize_amount_rounds_and_formats_two_decimals():
    assert normalize_amount("100") == "100.00"
    assert normalize_amount("99.235") == "99.24"


def test_generate_payment_hash_matches_payu_sequence():
    key = "merchant-key"
    salt = "merchant-salt"
    hash_string = "|".join(
        [
            "merchant-key",
            "payu_payment_txn",
            "100.00",
            "Quiz Registration",
            "John Doe",
            "john@example.com",
            "payment-1",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "merchant-salt",
        ]
    )

    assert generate_payment_hash(
        key=key,
        salt=salt,
        txnid="payu_payment_txn",
        amount="100.00",
        productinfo="Quiz Registration",
        firstname="John Doe",
        email="john@example.com",
        udf1="payment-1",
    ) == hashlib.sha512(hash_string.encode("utf-8")).hexdigest().lower()


def test_verify_payment_response_hash_accepts_valid_payload():
    key = "merchant-key"
    salt = "merchant-salt"
    payload = {
        "status": "success",
        "txnid": "payu_payment_txn",
        "amount": "100.00",
        "productinfo": "Quiz Registration",
        "firstname": "John Doe",
        "email": "john@example.com",
        "udf1": "payment-1",
    }
    response_string = "|".join(
        [
            "merchant-salt",
            "success",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "payment-1",
            "john@example.com",
            "John Doe",
            "Quiz Registration",
            "100.00",
            "payu_payment_txn",
            "merchant-key",
        ]
    )
    payload["hash"] = hashlib.sha512(response_string.encode("utf-8")).hexdigest().lower()

    assert verify_payment_response_hash(payload, key=key, salt=salt) is True


def test_verify_payment_response_hash_supports_additional_charges():
    key = "merchant-key"
    salt = "merchant-salt"
    payload = {
        "status": "success",
        "txnid": "payu_payment_txn",
        "amount": "100.00",
        "productinfo": "Quiz Registration",
        "firstname": "John Doe",
        "email": "john@example.com",
        "udf1": "payment-1",
        "additional_charges": "12.00",
    }
    response_string = "|".join(
        [
            "12.00",
            "merchant-salt",
            "success",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "payment-1",
            "john@example.com",
            "John Doe",
            "Quiz Registration",
            "100.00",
            "payu_payment_txn",
            "merchant-key",
        ]
    )
    payload["hash"] = hashlib.sha512(response_string.encode("utf-8")).hexdigest().lower()

    assert verify_payment_response_hash(payload, key=key, salt=salt) is True
