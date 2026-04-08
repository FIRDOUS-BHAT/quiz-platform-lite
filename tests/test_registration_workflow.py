from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_current_admin, get_store
from app.routers import web
from app.schemas.auth import PaymentStatus, UserAccessStatus, UserRole, UserSession
from app.utils.rate_limit import _COUNTS


class FakeRegisterStore:
    def __init__(
        self,
        registration_error: Exception | None = None,
        payment_start_error: Exception | None = None,
        payment_callback_result: dict[str, str | bool] | Exception | None = None,
    ) -> None:
        self.registration_error = registration_error
        self.payment_start_error = payment_start_error
        self.payment_callback_result = payment_callback_result
        self.registration_calls: list[tuple[object, str | None]] = []
        self.payment_start_calls: list[tuple[str, str, str, str, str | None]] = []
        self.payment_callback_calls: list[tuple[dict[str, str], bool, str | None]] = []

    async def create_paid_student_registration(self, payload, *, request_id: str | None = None):
        self.registration_calls.append((payload, request_id))
        if self.registration_error is not None:
            raise self.registration_error
        return UserSession(
            user_id="student-1",
            email=payload.email,
            full_name=payload.full_name,
            role=UserRole.STUDENT,
            payment_status=PaymentStatus.UNCONFIRMED,
            access_status=UserAccessStatus.PENDING_CREDENTIALS,
        )

    async def initiate_payu_payment(
        self,
        *,
        email: str,
        amount: str,
        product_info: str,
        callback_url: str,
        request_id: str | None = None,
    ):
        self.payment_start_calls.append((email, amount, product_info, callback_url, request_id))
        if self.payment_start_error is not None:
            raise self.payment_start_error
        return {
            "payment_id": "payment-1",
            "provider_txn_id": "payu_payment_txn",
            "amount": amount,
            "product_info": product_info,
            "full_name": "John Doe",
            "email": email,
            "mobile_number": "919876543210",
            "callback_url": callback_url,
        }

    async def finalize_payu_payment(
        self,
        payload: dict[str, str],
        *,
        verified: bool,
        request_id: str | None = None,
    ):
        self.payment_callback_calls.append((payload, verified, request_id))
        if isinstance(self.payment_callback_result, Exception):
            raise self.payment_callback_result
        if self.payment_callback_result is not None:
            return self.payment_callback_result
        return {
            "payment_id": "payment-1",
            "provider_txn_id": payload["txnid"],
            "registered_email": payload.get("email", "john@example.com"),
            "status": "success",
            "verified": verified,
            "user_payment_status": "confirmed",
        }


class FakeAdminStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, PaymentStatus, str | None, str | None]] = []

    async def update_student_payment_status(
        self,
        user_id: str,
        payment_status: PaymentStatus,
        *,
        actor_user_id: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        self.calls.append((user_id, payment_status, actor_user_id, request_id))
        return True


def build_app(store, *, admin_user: UserSession | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(web.router)
    app.dependency_overrides[get_store] = lambda: store
    if admin_user is not None:
        app.dependency_overrides[get_current_admin] = lambda: admin_user
    return app


def registration_payload() -> dict[str, str]:
    return {
        "full_name": "John Doe",
        "father_name": "R. K. Doe",
        "mother_name": "Mary-Anne Doe",
        "mobile_number": "+91 98765 43210",
        "email": "JOHN@example.com",
    }


def test_register_page_hides_payment_until_submission(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")

    client = TestClient(build_app(FakeRegisterStore()))

    response = client.get("/app/register")

    assert response.status_code == 200
    assert "Available After Registration" in response.text
    assert "Open Payment Page" not in response.text
    assert "Submit Registration" in response.text


def test_root_redirects_to_registration_page(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)

    client = TestClient(build_app(FakeRegisterStore()))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app/register"


def test_successful_registration_unlocks_payment_step(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")
    monkeypatch.setattr(settings, "payu_merchant_key", "merchant-key")
    monkeypatch.setattr(settings, "payu_merchant_salt", "merchant-salt")

    store = FakeRegisterStore()
    client = TestClient(build_app(store))

    response = client.post("/app/register", data=registration_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert "payment_ready=yes" in response.headers["location"]
    assert "registered_email=john%40example.com" in response.headers["location"]
    assert len(store.registration_calls) == 1

    unlocked = client.get(response.headers["location"])

    assert unlocked.status_code == 200
    assert "Registration Submitted" in unlocked.text
    assert "Continue to Secure Payment" in unlocked.text
    assert "Registered email: <strong>john@example.com</strong>" in unlocked.text
    assert "Submit Registration" not in unlocked.text


def test_existing_unconfirmed_candidate_is_redirected_to_payment(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")
    monkeypatch.setattr(settings, "payu_merchant_key", "merchant-key")
    monkeypatch.setattr(settings, "payu_merchant_salt", "merchant-salt")

    store = FakeRegisterStore(
        registration_error=ValueError(
            "A registration with this email is already on file. "
            "Complete the payment to confirm your candidature."
        )
    )
    client = TestClient(build_app(store))

    response = client.post("/app/register", data=registration_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert "payment_ready=yes" in response.headers["location"]

    unlocked = client.get(response.headers["location"])

    assert "Registration Submitted" in unlocked.text
    assert "Continue to Secure Payment" in unlocked.text


def test_payment_start_renders_autosubmit_form(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")
    monkeypatch.setattr(settings, "payu_product_info", "Quiz Registration")
    monkeypatch.setattr(settings, "payu_merchant_key", "merchant-key")
    monkeypatch.setattr(settings, "payu_merchant_salt", "merchant-salt")
    monkeypatch.setattr(settings, "public_base_url", "https://quiz.example.com")

    store = FakeRegisterStore()
    client = TestClient(build_app(store))

    response = client.post("/app/register/payment/start", data={"registered_email": "john@example.com"})

    assert response.status_code == 200
    assert 'id="payu-payment-form"' in response.text
    assert 'action="https://test.payu.in/_payment"' in response.text
    assert 'name="txnid" value="payu_payment_txn"' in response.text
    assert 'name="surl" value="https://quiz.example.com/app/payments/payu/callback"' in response.text
    assert store.payment_start_calls == [
        (
            "john@example.com",
            "100.00",
            "Quiz Registration",
            "https://quiz.example.com/app/payments/payu/callback",
            None,
        )
    ]


def test_payu_callback_redirects_on_verified_success(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "payu_merchant_key", "merchant-key")
    monkeypatch.setattr(settings, "payu_merchant_salt", "merchant-salt")
    monkeypatch.setattr(web, "verify_payment_response_hash", lambda payload, *, key, salt: True)

    store = FakeRegisterStore()
    client = TestClient(build_app(store))

    response = client.post(
        "/app/payments/payu/callback",
        data={
            "txnid": "payu_payment_txn",
            "status": "success",
            "email": "john@example.com",
            "firstname": "John Doe",
            "amount": "100.00",
            "productinfo": "Quiz Registration",
            "hash": "callback-hash",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "payment_result=success" in response.headers["location"]
    assert "registered_email=john%40example.com" in response.headers["location"]
    assert store.payment_callback_calls[0][1] is True


def test_payu_callback_redirects_on_tampered_response(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "payu_merchant_key", "merchant-key")
    monkeypatch.setattr(settings, "payu_merchant_salt", "merchant-salt")
    monkeypatch.setattr(web, "verify_payment_response_hash", lambda payload, *, key, salt: False)

    store = FakeRegisterStore(
        payment_callback_result={
            "payment_id": "payment-1",
            "provider_txn_id": "payu_payment_txn",
            "registered_email": "john@example.com",
            "status": "tampered",
            "verified": False,
            "user_payment_status": "unconfirmed",
        }
    )
    client = TestClient(build_app(store))

    response = client.post(
        "/app/payments/payu/callback",
        data={
            "txnid": "payu_payment_txn",
            "status": "success",
            "email": "john@example.com",
            "firstname": "John Doe",
            "amount": "100.00",
            "productinfo": "Quiz Registration",
            "hash": "bad-hash",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "payment_result=tampered" in response.headers["location"]
    assert store.payment_callback_calls[0][1] is False


def test_admin_payment_status_route_passes_actor_context(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)

    store = FakeAdminStore()
    admin_user = UserSession(
        user_id="admin-1",
        email="admin@example.com",
        full_name="Platform Admin",
        role=UserRole.ADMIN,
        payment_status=PaymentStatus.CONFIRMED,
        access_status=UserAccessStatus.ACTIVE,
    )
    client = TestClient(build_app(store, admin_user=admin_user))

    response = client.post(
        "/app/admin/students/student-1/payment-status",
        data={"payment_status": "confirmed", "next_url": "/app/admin/students/student-1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/admin/students/student-1")
    assert store.calls == [("student-1", PaymentStatus.CONFIRMED, "admin-1", None)]
