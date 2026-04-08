from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping


def normalize_amount(value: str) -> str:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("payment amount must be a valid decimal value") from exc
    if amount <= 0:
        raise ValueError("payment amount must be greater than zero")
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def generate_payment_hash(
    *,
    key: str,
    salt: str,
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    udf1: str = "",
    udf2: str = "",
    udf3: str = "",
    udf4: str = "",
    udf5: str = "",
) -> str:
    hash_string = (
        f"{key}|{txnid}|{amount}|{productinfo}|{firstname}|{email}|"
        f"{udf1}|{udf2}|{udf3}|{udf4}|{udf5}||||||{salt}"
    )
    return hashlib.sha512(hash_string.encode("utf-8")).hexdigest().lower()


def verify_payment_response_hash(payload: Mapping[str, object], *, key: str, salt: str) -> bool:
    response_hash = str(payload.get("hash", "")).strip().lower()
    status = str(payload.get("status", "")).strip()
    txnid = str(payload.get("txnid", "")).strip()
    amount = str(payload.get("amount", "")).strip()
    productinfo = str(payload.get("productinfo", "")).strip()
    firstname = str(payload.get("firstname", "")).strip()
    email = str(payload.get("email", "")).strip()
    udf1 = str(payload.get("udf1", "")).strip()
    udf2 = str(payload.get("udf2", "")).strip()
    udf3 = str(payload.get("udf3", "")).strip()
    udf4 = str(payload.get("udf4", "")).strip()
    udf5 = str(payload.get("udf5", "")).strip()
    additional_charges = str(payload.get("additional_charges", "")).strip() or str(
        payload.get("additionalCharges", "")
    ).strip()
    base_string = (
        f"{salt}|{status}||||||{udf5}|{udf4}|{udf3}|{udf2}|{udf1}|"
        f"{email}|{firstname}|{productinfo}|{amount}|{txnid}|{key}"
    )
    if additional_charges:
        base_string = f"{additional_charges}|{base_string}"
    expected_hash = hashlib.sha512(base_string.encode("utf-8")).hexdigest().lower()
    return bool(response_hash) and expected_hash == response_hash
