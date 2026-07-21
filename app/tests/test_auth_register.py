"""Tests for POST /api/v1/auth/register."""

import re


def test_register_sends_code_and_returns_generic_message(client, sent_codes):
    r = client.post("/api/v1/auth/register", json={"email": "someone@example.com"})
    assert r.status_code == 200
    assert "email" in r.json()["message"].lower()

    assert len(sent_codes) == 1
    to, code = sent_codes[0]
    assert to == "someone@example.com"
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", code), code


def test_register_normalizes_email(client, sent_codes):
    r = client.post("/api/v1/auth/register", json={"email": "  Someone@Example.COM "})
    assert r.status_code == 200
    assert sent_codes[0][0] == "someone@example.com"


def test_register_rejects_invalid_email(client, sent_codes):
    r = client.post("/api/v1/auth/register", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert sent_codes == []


def test_register_throttles_after_three_codes_per_hour(client, sent_codes):
    for _ in range(3):
        r = client.post("/api/v1/auth/register", json={"email": "hot@example.com"})
        assert r.status_code == 200

    r = client.post("/api/v1/auth/register", json={"email": "hot@example.com"})
    assert r.status_code == 429
    assert len(sent_codes) == 3


def test_register_delivery_failure_returns_502(client, monkeypatch):
    import auth
    from mailer import EmailDeliveryError

    def boom(email: str, code: str) -> None:
        raise EmailDeliveryError("sandbox says no")

    monkeypatch.setattr(auth, "send_verification_code", boom)

    r = client.post("/api/v1/auth/register", json={"email": "someone@example.com"})
    assert r.status_code == 502
    assert "email" in r.json()["detail"].lower()
