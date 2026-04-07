from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import get_current_admin, get_store
from app.routers import web
from app.schemas.auth import PaymentStatus, UserAccessStatus, UserRole, UserSession
from app.utils.rate_limit import _COUNTS


class FakeRegisterStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[object, str | None]] = []

    async def create_paid_student_registration(self, payload, *, request_id: str | None = None):
        self.calls.append((payload, request_id))
        if self.error is not None:
            raise self.error
        return UserSession(
            user_id="student-1",
            email=payload.email,
            full_name=payload.full_name,
            role=UserRole.STUDENT,
            payment_status=PaymentStatus.UNCONFIRMED,
            access_status=UserAccessStatus.PENDING_CREDENTIALS,
        )


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


def test_successful_registration_unlocks_payment_step(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")

    store = FakeRegisterStore()
    client = TestClient(build_app(store))

    response = client.post("/app/register", data=registration_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert "payment_ready=yes" in response.headers["location"]
    assert "registered_email=john%40example.com" in response.headers["location"]
    assert len(store.calls) == 1

    unlocked = client.get(response.headers["location"])

    assert unlocked.status_code == 200
    assert "Registration Submitted" in unlocked.text
    assert "Open Payment Page" in unlocked.text
    assert "Registered email: <strong>john@example.com</strong>" in unlocked.text
    assert "Submit Registration" not in unlocked.text


def test_existing_unconfirmed_candidate_is_redirected_to_payment(monkeypatch):
    _COUNTS.clear()
    monkeypatch.setattr(settings, "allow_open_registration", True)
    monkeypatch.setattr(settings, "payu_payment_url", "https://test.payu.in/_payment")
    monkeypatch.setattr(settings, "payu_certificate_fee", "100.00")

    store = FakeRegisterStore(
        ValueError(
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
    assert "Open Payment Page" in unlocked.text


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
