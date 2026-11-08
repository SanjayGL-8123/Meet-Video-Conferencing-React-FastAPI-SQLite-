import json
import uuid
import pytest
from starlette.websockets import WebSocketDisconnect
from app.main import app, limiters
from app.security import create_token, decode_token, RateLimiter

PW = "test-password-123"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def signup(client, prefix="audit"):
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": name, "email": f"{name}@example.com", "password": PW})
    assert r.status_code == 201, r.text
    return name, r.json()["token"]


def make_room(client, token, name="Security Audit Room"):
    r = client.post("/api/rooms", json={"name": name}, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["code"]


# ------------------ STAGE 3 & 8: Headers & Secrets ------------------
def test_all_security_headers_present(client):
    r = client.get("/api/me")
    headers = r.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "permissions-policy" in headers
    assert "camera=()" in headers["permissions-policy"]


def test_cors_preflight(client):
    # Preflight OPTIONS request
    r = client.options("/api/rooms", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization,Content-Type"
    })
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ------------------ STAGE 4: Authentication & Rate Limiting ------------------
def test_case_insensitive_username_email_login(client):
    name, _ = signup(client, "mixedcase")
    # Login with all uppercase username
    r1 = client.post("/api/auth/login", json={"identifier": name.upper(), "password": PW})
    assert r1.status_code == 200
    assert r1.json()["token"]

    # Login with all uppercase email
    r2 = client.post("/api/auth/login", json={"identifier": f"{name.upper()}@EXAMPLE.COM", "password": PW})
    assert r2.status_code == 200
    assert r2.json()["token"]


def test_ip_based_login_rate_limiting(client):
    # Attacking multiple different usernames from the same IP (credential spraying)
    for l in limiters:
        l.reset()
    responses = []
    for i in range(35):
        r = client.post("/api/auth/login", json={"identifier": f"victim_{i}", "password": "wrong-password"})
        responses.append(r.status_code)
    # Beyond 30 attempts, IP rate limiter must block with 429
    assert 429 in responses


def test_tampered_jwt_token(client):
    name, token = signup(client)
    # Corrupt the payload part of the token
    parts = token.split(".")
    tampered = f"{parts[0]}.eyJhZG1pbiI6dHJ1ZX0.{parts[2]}"
    r = client.get("/api/me", headers=auth(tampered))
    assert r.status_code == 401


# ------------------ STAGE 5: Authorization & IDOR ------------------
def test_user_cannot_see_other_user_rooms_in_my_rooms(client):
    _, token_a = signup(client, "alice")
    _, token_b = signup(client, "bob")

    code_a = make_room(client, token_a, "Alice Secret Room")
    code_b = make_room(client, token_b, "Bob Room")

    rooms_a = client.get("/api/rooms", headers=auth(token_a)).json()
    rooms_b = client.get("/api/rooms", headers=auth(token_b)).json()

    codes_a = [r["code"] for r in rooms_a]
    codes_b = [r["code"] for r in rooms_b]

    assert code_a in codes_a
    assert code_a not in codes_b
    assert code_b in codes_b
    assert code_b not in codes_a


def test_case_insensitive_room_lookup_and_join(client):
    _, host_token = signup(client, "host")
    code = make_room(client, host_token, "Room Case Test")

    # Fetch with uppercase
    r = client.get(f"/api/rooms/{code.upper()}", headers=auth(host_token))
    assert r.status_code == 200
    assert r.json()["code"] == code

    # Join via WebSocket with uppercase
    with client.websocket_connect(f"/ws/rooms/{code.upper()}") as ws:
        ws.send_json({"type": "auth", "token": host_token})
        w = ws.receive_json()
        assert w["type"] == "welcome"


def test_idempotent_delete_room(client):
    _, host_token = signup(client, "host")
    code = make_room(client, host_token, "Idempotent End Room")

    # First delete
    r1 = client.delete(f"/api/rooms/{code}", headers=auth(host_token))
    assert r1.status_code == 200

    # Second delete on already ended room
    r2 = client.delete(f"/api/rooms/{code}", headers=auth(host_token))
    assert r2.status_code == 200


# ------------------ STAGE 6: Input Validation & Injection ------------------
def test_xss_payload_in_room_name(client):
    _, token = signup(client)
    xss_name = "<script>alert(document.cookie)</script>"
    r = client.post("/api/rooms", json={"name": xss_name}, headers=auth(token))
    assert r.status_code == 201
    assert r.json()["name"] == xss_name


def test_sql_injection_in_room_endpoints(client):
    _, token = signup(client)
    # Attempt SQL injection via URL path parameter
    r = client.get("/api/rooms/' OR 1=1 --", headers=auth(token))
    assert r.status_code == 404

    r2 = client.get("/api/rooms/' UNION SELECT * FROM users --", headers=auth(token))
    assert r2.status_code == 404


# ------------------ STAGE 8 & 16: WebSocket Protocol & Edge Cases ------------------
def test_ws_payload_too_large(client):
    _, token = signup(client)
    code = make_room(client, token)

    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        # Send payload > 64KB
        huge_payload = json.dumps({"type": "auth", "token": "x" * (65 * 1024)})
        ws.send_text(huge_payload)
        res = ws.receive_json()
        assert res["type"] == "error"
        assert res["reason"] == "payload_too_large"


def test_ws_rate_limiting_spammer(client):
    _, token = signup(client)
    code = make_room(client, token)

    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "welcome"

        # Spam 110 messages rapidly
        got_rate_limited = False
        for i in range(110):
            ws.send_json({"type": "signal", "to": "nobody", "data": {"test": i}})
            msg = ws.receive_json()
            if msg.get("type") == "error" and msg.get("reason") == "rate_limited":
                got_rate_limited = True
                break
        assert got_rate_limited is True


def test_ws_non_dict_and_invalid_state_handled_safely(client):
    _, token = signup(client)
    code = make_room(client, token)

    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "welcome"

        # Send non-dict JSON (array)
        ws.send_text("[1, 2, 3]")
        # Send state with invalid types
        ws.send_json({"type": "state", "audio": "not-a-bool", "video": None})
        # Normal chat should still work safely
        ws.send_json({"type": "chat", "content": "still alive"})
        chat_resp = ws.receive_json()
        assert chat_resp["type"] == "chat"
        assert chat_resp["content"] == "still alive"
