"""Tests for API-key enforcement on entry creation."""

import os
import sqlite3

ENTRY = {"title": "Test entry", "content": "Some test content.", "tags": ["test-suite"]}


def test_post_entry_without_key_401(client):
    r = client.post("/api/v1/entries", json=ENTRY)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    assert "/api/v1/auth/register" in r.json()["detail"]


def test_post_entry_with_invalid_key_401(client):
    r = client.post(
        "/api/v1/entries", json=ENTRY, headers={"Authorization": "Bearer cp_nope"}
    )
    assert r.status_code == 401


def test_post_entry_with_valid_key_creates_entry(client, make_api_key):
    key = make_api_key()
    r = client.post(
        "/api/v1/entries", json=ENTRY, headers={"Authorization": f"Bearer {key}"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == ENTRY["title"]
    # Account linkage stays private.
    assert "account_id" not in body
    assert "email" not in body

    r = client.get(f"/api/v1/entries/{body['id']}")
    assert r.status_code == 200


def test_post_entry_with_x_api_key_header(client, make_api_key):
    key = make_api_key()
    r = client.post("/api/v1/entries", json=ENTRY, headers={"X-API-Key": key})
    assert r.status_code == 201


def test_post_entry_with_rotated_out_key_401(client, make_api_key):
    old_key = make_api_key("rotate@example.com")
    make_api_key("rotate@example.com")  # rotation revokes old_key

    r = client.post(
        "/api/v1/entries", json=ENTRY, headers={"Authorization": f"Bearer {old_key}"}
    )
    assert r.status_code == 401


def test_get_endpoints_remain_public(client, make_api_key):
    key = make_api_key()
    client.post("/api/v1/entries", json=ENTRY, headers={"X-API-Key": key})

    assert client.get("/api/v1/recent").status_code == 200
    assert client.get("/api/v1/entries").status_code == 200
    assert client.get("/api/v1/entries/random").status_code == 200


def test_entry_records_account_id(client, make_api_key):
    key = make_api_key("linked@example.com")
    r = client.post(
        "/api/v1/entries", json=ENTRY, headers={"Authorization": f"Bearer {key}"}
    )
    assert r.status_code == 201
    entry_id = r.json()["id"]

    conn = sqlite3.connect(os.environ["CLAUDEPEDIA_DB"])
    row = conn.execute(
        "SELECT a.email FROM entries e JOIN accounts a ON a.id = e.account_id "
        "WHERE e.id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()
    assert row == ("linked@example.com",)
