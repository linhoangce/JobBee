"""Profile round-trip tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_get_profile_lazy_creates_user() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] >= 1
    assert isinstance(body["name"], str)
    assert isinstance(body["target_roles"], list)


def test_put_profile_partial_update() -> None:
    with TestClient(app) as client:
        # Seed
        client.get("/api/profile")
        resp = client.put(
            "/api/profile",
            json={
                "name": "Lin",
                "headline": "Senior software engineer",
                "target_roles": ["Software engineer", "Platform engineer"],
                "work_modes": ["remote", "hybrid"],
                "skills": ["python", "typescript"],
                "links": {"linkedin": "https://www.linkedin.com/in/lin/"},
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Lin"
    assert body["headline"] == "Senior software engineer"
    assert body["target_roles"] == ["Software engineer", "Platform engineer"]
    assert body["work_modes"] == ["remote", "hybrid"]
    assert "linkedin" in body["links"]
    assert body["is_complete"] is True


def test_put_profile_mark_onboarded_stamps_timestamp() -> None:
    with TestClient(app) as client:
        client.put("/api/profile", json={"name": "Lin"})
        resp = client.put("/api/profile", json={"mark_onboarded": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarded_at"] is not None


def test_put_profile_application_defaults() -> None:
    with TestClient(app) as client:
        client.get("/api/profile")
        resp = client.put(
            "/api/profile",
            json={
                "application_defaults": {
                    "phone": "+1 416 555 0100",
                    "phone_device_type": "Mobile",
                    "country_phone_code": "Canada (+1)",
                    "address_line1": "123 Main St",
                    "address_line2": "Unit 4",
                    "city": "Toronto",
                    "province": "ON",
                    "postal_code": "M5V 1A1",
                    "country": "Canada",
                    "work_authorization": "yes_no_restrictions",
                    "requires_sponsorship": "no",
                    "referral_source": "LinkedIn Job Posting",
                },
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["application_defaults"]["phone"] == "+1 416 555 0100"
    assert body["application_defaults"]["phone_device_type"] == "Mobile"
    assert body["application_defaults"]["country_phone_code"] == "Canada (+1)"
    assert body["application_defaults"]["address_line1"] == "123 Main St"
    assert body["application_defaults"]["address_line2"] == "Unit 4"
    assert body["application_defaults"]["city"] == "Toronto"
    assert body["application_defaults"]["province"] == "ON"
    assert body["application_defaults"]["postal_code"] == "M5V 1A1"
    assert body["application_defaults"]["country"] == "Canada"
    assert body["application_defaults"]["requires_sponsorship"] == "no"
    assert body["application_defaults"]["referral_source"] == "LinkedIn Job Posting"


def test_put_profile_rejects_invalid_work_mode() -> None:
    with TestClient(app) as client:
        resp = client.put("/api/profile", json={"work_modes": ["telepathy"]})
    assert resp.status_code == 422
