"""Tests for POST /api/v1/auth/verify."""


def _register(client, email="v@example.com"):
    r = client.post("/api/v1/auth/register", json={"email": email})
    assert r.status_code == 200


def test_verify_with_correct_code_returns_api_key(client, sent_codes):
    _register(client)
    code = sent_codes[-1][1]

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 200
    key = r.json()["api_key"]
    assert key.startswith("cp_")
    assert len(key) >= 40


def test_verify_accepts_lowercase_code(client, sent_codes):
    _register(client)
    code = sent_codes[-1][1].lower()

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 200


def test_verify_wrong_code_400(client, sent_codes):
    _register(client)

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": "WRONGCOD"}
    )
    assert r.status_code == 400


def test_verify_unknown_email_400(client, sent_codes):
    r = client.post(
        "/api/v1/auth/verify", json={"email": "nobody@example.com", "code": "ABCDEFGH"}
    )
    assert r.status_code == 400


def test_verify_code_is_single_use(client, sent_codes):
    _register(client)
    code = sent_codes[-1][1]

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 200
    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 400


def test_verify_expired_code_400(client, sent_codes, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "CODE_TTL_MINUTES", -1)
    _register(client)
    code = sent_codes[-1][1]

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 400


def test_verify_locks_code_after_too_many_attempts(client, sent_codes):
    _register(client)
    code = sent_codes[-1][1]

    for _ in range(10):
        r = client.post(
            "/api/v1/auth/verify", json={"email": "v@example.com", "code": "WRONGCOD"}
        )
        assert r.status_code in (400, 429)
    assert r.status_code == 429

    # Even the correct code is refused once the code is locked.
    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": code}
    )
    assert r.status_code == 429


def test_new_code_invalidates_previous(client, sent_codes):
    _register(client)
    first = sent_codes[-1][1]
    _register(client)
    second = sent_codes[-1][1]

    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": first}
    )
    assert r.status_code == 400
    r = client.post(
        "/api/v1/auth/verify", json={"email": "v@example.com", "code": second}
    )
    assert r.status_code == 200


def test_reverify_rotates_api_key(client, sent_codes, make_api_key):
    key1 = make_api_key("rotate@example.com")
    key2 = make_api_key("rotate@example.com")
    assert key1 != key2
