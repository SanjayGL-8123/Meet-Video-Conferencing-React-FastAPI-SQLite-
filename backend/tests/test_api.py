import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

PW = "correct-horse-battery"


def signup(client, prefix="user"):
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": name, "email": f"{name}@example.com", "password": PW})
    assert r.status_code == 201, r.text
    return name, r.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def make_room(client, token, name="Standup"):
    r = client.post("/api/rooms", json={"name": name}, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["code"]


# ---------- auth ----------
def test_register_login_me(client):
    name, token = signup(client)
    assert client.get("/api/me", headers=auth(token)).json()["username"] == name
    r = client.post("/api/auth/login", json={"identifier": name.upper(), "password": PW})
    assert r.status_code == 200 and r.json()["token"]


def test_duplicate_and_weak_inputs(client):
    name, _ = signup(client)
    dup = client.post("/api/auth/register", json={"username": name, "email": "x@example.com", "password": PW})
    assert dup.status_code == 409
    for bad in [
        {"username": "ab", "email": "a@b.co", "password": PW},
        {"username": "bad name!", "email": "a@b.co", "password": PW},
        {"username": "okname", "email": "not-an-email", "password": PW},
        {"username": "okname", "email": "a@b.co", "password": "short"},
        {"username": "okname", "email": "a@b.co", "password": "x" * 100},
    ]:
        assert client.post("/api/auth/register", json=bad).status_code == 422


def test_wrong_password_and_sql_injection_login(client):
    name, _ = signup(client)
    assert client.post("/api/auth/login", json={"identifier": name, "password": "nope-nope-nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"identifier": "' OR 1=1 --", "password": "whatever123"})
    assert r.status_code == 401


def test_auth_required_and_bad_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=auth("garbage.token.value")).status_code == 401
    assert client.post("/api/rooms", json={"name": "x"}).status_code == 401


def test_login_rate_limit(client):
    name, _ = signup(client)
    codes = [client.post("/api/auth/login", json={"identifier": name, "password": "wrong-wrong"}).status_code
             for _ in range(12)]
    assert 429 in codes


def test_security_headers(client):
    h = client.get("/api/me").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"


# ---------- rooms ----------
def test_room_lifecycle_and_host_only_end(client):
    _, host = signup(client, "host")
    _, other = signup(client, "other")
    code = make_room(client, host)
    assert client.get(f"/api/rooms/{code}", headers=auth(other)).json()["is_active"] is True
    assert client.delete(f"/api/rooms/{code}", headers=auth(other)).status_code == 403
    assert client.delete(f"/api/rooms/{code}", headers=auth(host)).status_code == 200
    assert client.get(f"/api/rooms/{code}", headers=auth(host)).json()["is_active"] is False
    assert any(r["code"] == code for r in client.get("/api/rooms", headers=auth(host)).json())


def test_room_validation_and_not_found(client):
    _, t = signup(client)
    assert client.post("/api/rooms", json={"name": "   "}, headers=auth(t)).status_code == 422
    assert client.get("/api/rooms/abc-defg-hij", headers=auth(t)).status_code == 404
    assert client.get("/api/rooms/../../etc/passwd", headers=auth(t)).status_code == 404


def test_messages_forbidden_for_non_members(client):
    _, host = signup(client, "host")
    _, stranger = signup(client, "stranger")
    code = make_room(client, host)
    assert client.get(f"/api/rooms/{code}/messages", headers=auth(stranger)).status_code == 403
    assert client.get(f"/api/rooms/{code}/messages", headers=auth(host)).status_code == 200


# ---------- websocket ----------
def test_ws_rejects_bad_token(client):
    _, host = signup(client)
    code = make_room(client, host)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        ws.send_json({"type": "auth", "token": "bad"})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_requires_auth_first(client):
    _, host = signup(client)
    code = make_room(client, host)
    with client.websocket_connect(f"/ws/rooms/{code}") as ws:
        ws.send_json({"type": "chat", "content": "hi"})
        assert ws.receive_json()["reason"] == "auth_required"


def test_ws_signal_relay_chat_history_and_end(client):
    _, ta = signup(client, "alice")
    _, tb = signup(client, "bob")
    code = make_room(client, ta)

    with client.websocket_connect(f"/ws/rooms/{code}") as a:
        a.send_json({"type": "auth", "token": ta})
        wa = a.receive_json()
        assert wa["type"] == "welcome" and wa["peers"] == [] and wa["is_host"] is True

        with client.websocket_connect(f"/ws/rooms/{code}") as b:
            b.send_json({"type": "auth", "token": tb})
            wb = b.receive_json()
            assert wb["is_host"] is False and [p["id"] for p in wb["peers"]] == [wa["peer_id"]]
            assert a.receive_json()["type"] == "peer-joined"

            b.send_json({"type": "signal", "to": wa["peer_id"], "data": {"sdp": {"type": "offer", "sdp": "x"}}})
            got = a.receive_json()
            assert got["type"] == "signal" and got["from"] == wb["peer_id"]

            a.send_json({"type": "signal", "to": "nope", "data": {"x": 1}})
            assert a.receive_json()["reason"] == "bad_signal"

            a.send_json({"type": "chat", "content": "hello team"})
            assert a.receive_json()["content"] == "hello team"
            assert b.receive_json()["content"] == "hello team"

            b.send_json({"type": "state", "audio": False, "video": True})
            st = a.receive_json()
            assert st["type"] == "state" and st["audio"] is False

            b.send_json({"type": "end"})
            assert b.receive_json()["reason"] == "forbidden"

            hist = client.get(f"/api/rooms/{code}/messages", headers=auth(tb))
            assert hist.status_code == 200 and hist.json()[-1]["content"] == "hello team"

            a.send_json({"type": "end"})
            assert b.receive_json()["type"] == "ended"

    assert client.get(f"/api/rooms/{code}", headers=auth(ta)).json()["is_active"] is False
    with client.websocket_connect(f"/ws/rooms/{code}") as late:
        late.send_json({"type": "auth", "token": tb})
        assert late.receive_json()["reason"] == "room_unavailable"
