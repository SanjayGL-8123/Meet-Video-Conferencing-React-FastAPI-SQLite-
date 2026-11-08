from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from . import config
from .db import db, init_db
from .security import (DUMMY_HASH, RateLimiter, create_token, decode_token,
                       hash_password, verify_password)

log = logging.getLogger("meet")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
CODE_RE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
MAX_WS_BYTES = 64 * 1024

login_ip_limiter = RateLimiter(30, 60)
login_user_limiter = RateLimiter(10, 60)
login_limiter = login_user_limiter  # backward-compatible alias
register_limiter = RateLimiter(5, 60)
room_limiter = RateLimiter(20, 60)
chat_limiter = RateLimiter(30, 10)
ws_msg_limiter = RateLimiter(100, 10)
limiters = [login_ip_limiter, login_user_limiter, register_limiter, room_limiter, chat_limiter, ws_msg_limiter]

PROD = config.ENV == "production"


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="Meet", lifespan=lifespan,
              docs_url=None if PROD else "/docs", redoc_url=None,
              openapi_url=None if PROD else "/openapi.json")

app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Permissions-Policy"] = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    if not request.url.path.startswith(("/docs", "/openapi")):
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if PROD:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(_request, exc):
    msgs = []
    for e in exc.errors():
        msg = str(e.get("msg", "Invalid input")).removeprefix("Value error, ")
        loc = [str(x) for x in e.get("loc", []) if x != "body"]
        msgs.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return JSONResponse({"detail": "; ".join(msgs)[:300]}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(_request, exc):
    log.exception("Unhandled error")
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    if host in ("127.0.0.1", "::1", "localhost"):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return host


# ---------- models ----------
class RegisterIn(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("username")
    @classmethod
    def _u(cls, v):
        v = v.strip()
        if not USERNAME_RE.match(v):
            raise ValueError("Username must be 3-30 letters, digits or underscores")
        return v

    @field_validator("email")
    @classmethod
    def _e(cls, v):
        v = v.strip().lower()
        if len(v) > 254 or not EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _p(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v.encode()) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v


class LoginIn(BaseModel):
    identifier: str = Field(max_length=254)
    password: str = Field(max_length=200)


class RoomIn(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        v = " ".join(v.split())
        if not 1 <= len(v) <= 80:
            raise ValueError("Meeting name must be 1-80 characters")
        return v


# ---------- helpers ----------
def load_user(uid: int):
    with db() as c:
        return c.execute("SELECT id, username, email FROM users WHERE id=?", (uid,)).fetchone()


bearer = HTTPBearer(auto_error=False)


def current_user(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    claims = decode_token(cred.credentials) if cred else None
    user = None
    if claims:
        try:
            user = load_user(int(claims["sub"]))
        except (ValueError, KeyError):
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def fetch_room(code: str):
    if not isinstance(code, str):
        return None
    code = code.strip().lower()
    if not CODE_RE.match(code):
        return None
    with db() as c:
        return c.execute(
            "SELECT r.id, r.code, r.name, r.host_id, r.is_active, r.created_at, u.username AS host "
            "FROM rooms r JOIN users u ON u.id = r.host_id WHERE r.code=?", (code,)).fetchone()


def room_public(r):
    return {"code": r["code"], "name": r["name"], "host": r["host"],
            "is_active": bool(r["is_active"]), "created_at": r["created_at"]}


def new_code() -> str:
    letters = "abcdefghijkmnpqrstuvwxyz"
    pick = lambda n: "".join(secrets.choice(letters) for _ in range(n))
    return f"{pick(3)}-{pick(4)}-{pick(3)}"


# ---------- auth ----------
@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn, request: Request):
    if not register_limiter.allow(client_ip(request)):
        raise HTTPException(429, "Too many attempts, try again later")
    try:
        with db() as c:
            uid = c.execute("INSERT INTO users(username,email,password_hash) VALUES(?,?,?)",
                            (body.username, body.email, hash_password(body.password))).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username or email already in use")
    return {"token": create_token(uid, body.username),
            "user": {"id": uid, "username": body.username, "email": body.email}}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    ident = body.identifier.strip().lower()
    ip = client_ip(request)
    if not login_ip_limiter.allow(ip) or not login_user_limiter.allow(ident):
        raise HTTPException(429, "Too many attempts, try again later")
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=? OR email=?", (ident, ident)).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        raise HTTPException(401, "Invalid credentials")
    return {"token": create_token(row["id"], row["username"]),
            "user": {"id": row["id"], "username": row["username"], "email": row["email"]}}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"id": user["id"], "username": user["username"], "email": user["email"]}


# ---------- rooms ----------
@app.post("/api/rooms", status_code=201)
def create_room(body: RoomIn, request: Request, user=Depends(current_user)):
    if not room_limiter.allow(f"u{user['id']}"):
        raise HTTPException(429, "Too many meetings created, slow down")
    for _ in range(5):
        code = new_code()
        try:
            with db() as c:
                c.execute("INSERT INTO rooms(code,name,host_id) VALUES(?,?,?)", (code, body.name, user["id"]))
            return room_public(fetch_room(code))
        except sqlite3.IntegrityError:
            continue
    raise HTTPException(500, "Could not create meeting")


@app.get("/api/rooms")
def my_rooms(user=Depends(current_user)):
    with db() as c:
        rows = c.execute(
            "SELECT r.code, r.name, u.username AS host, r.is_active, r.created_at FROM rooms r "
            "JOIN users u ON u.id=r.host_id WHERE r.host_id=? "
            "ORDER BY r.is_active DESC, r.id DESC LIMIT 50", (user["id"],)).fetchall()
    return [room_public(r) for r in rows]


@app.get("/api/rooms/{code}")
def get_room(code: str, user=Depends(current_user)):
    room = fetch_room(code)
    if not room:
        raise HTTPException(404, "Meeting not found")
    return room_public(room)


@app.get("/api/rooms/{code}/messages")
def room_messages(code: str, user=Depends(current_user)):
    room = fetch_room(code)
    if not room:
        raise HTTPException(404, "Meeting not found")
    with db() as c:
        member = room["host_id"] == user["id"] or c.execute(
            "SELECT 1 FROM sessions WHERE room_id=? AND user_id=? LIMIT 1",
            (room["id"], user["id"])).fetchone()
        if not member:
            raise HTTPException(403, "Join the meeting to see its chat")
        rows = c.execute(
            "SELECT m.id, m.user_id, u.username AS user, m.content, m.created_at FROM messages m "
            "JOIN users u ON u.id=m.user_id WHERE m.room_id=? ORDER BY m.id DESC LIMIT 100",
            (room["id"],)).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.delete("/api/rooms/{code}")
async def delete_room(code: str, user=Depends(current_user)):
    code = code.strip().lower()
    room = fetch_room(code)
    if not room:
        raise HTTPException(404, "Meeting not found")
    if room["host_id"] != user["id"]:
        raise HTTPException(403, "Only the host can end the meeting")
    if not room["is_active"]:
        return {"ok": True}
    await end_room(code)
    return {"ok": True}


# ---------- realtime ----------
@dataclass
class Peer:
    id: str
    user_id: int
    username: str
    ws: WebSocket
    audio: bool = True
    video: bool = True

    def info(self):
        return {"id": self.id, "username": self.username, "audio": self.audio, "video": self.video}


class RoomManager:
    def __init__(self):
        self.rooms: dict[str, dict[str, Peer]] = {}

    def peers(self, code):
        return self.rooms.setdefault(code, {})

    async def send(self, peer: Peer, msg):
        try:
            await asyncio.wait_for(peer.ws.send_json(msg), timeout=5.0)
        except Exception:
            pass

    async def broadcast(self, code, msg, exclude=None):
        for p in list(self.rooms.get(code, {}).values()):
            if p.id != exclude:
                await self.send(p, msg)


manager = RoomManager()


async def end_room(code: str):
    code = code.strip().lower()
    with db() as c:
        c.execute("UPDATE rooms SET is_active=0, ended_at=datetime('now') WHERE code=? AND is_active=1", (code,))
    await manager.broadcast(code, {"type": "ended"})
    for p in list(manager.rooms.get(code, {}).values()):
        try:
            await p.ws.close(code=1000)
        except Exception:
            pass


@app.websocket("/ws/rooms/{code}")
async def room_ws(ws: WebSocket, code: str):
    code = code.strip().lower()
    origin = ws.headers.get("origin")
    if origin and origin not in config.ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()

    async def fail(reason):
        try:
            await ws.send_json({"type": "error", "reason": reason})
            await ws.close(code=1008)
        except Exception:
            pass

    try:  # first message must be auth, within 10 s
        first_raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        if len(first_raw) > MAX_WS_BYTES:
            return await fail("payload_too_large")
        first = json.loads(first_raw)
    except Exception:
        return await fail("auth_required")
    if not isinstance(first, dict) or first.get("type") != "auth":
        return await fail("auth_required")
    claims = decode_token(str(first.get("token", "")))
    try:
        user = load_user(int(claims["sub"])) if claims else None
    except (ValueError, KeyError):
        user = None
    if not user:
        return await fail("unauthorized")
    room = fetch_room(code)
    if not room or not room["is_active"]:
        return await fail("room_unavailable")
    peers = manager.peers(code)
    if len(peers) >= config.MAX_PEERS:
        return await fail("room_full")

    peer = Peer(uuid.uuid4().hex[:12], user["id"], user["username"], ws)
    existing = [p.info() for p in peers.values()]
    peers[peer.id] = peer
    with db() as c:
        sid = c.execute("INSERT INTO sessions(room_id,user_id) VALUES(?,?)", (room["id"], user["id"])).lastrowid

    try:
        await manager.send(peer, {"type": "welcome", "peer_id": peer.id,
                                  "is_host": room["host_id"] == user["id"], "peers": existing})
        await manager.broadcast(code, {"type": "peer-joined", "peer": peer.info()}, exclude=peer.id)

        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_WS_BYTES:
                await ws.close(code=1009)
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if not ws_msg_limiter.allow(peer.id):
                await manager.send(peer, {"type": "error", "reason": "rate_limited"})
                continue
            kind = msg.get("type")

            if kind == "signal":
                target = peers.get(str(msg.get("to", "")))
                data = msg.get("data")
                if target is None or target.id == peer.id or not isinstance(data, dict):
                    await manager.send(peer, {"type": "error", "reason": "bad_signal"})
                    continue
                await manager.send(target, {"type": "signal", "from": peer.id, "data": data})

            elif kind == "chat":
                content = msg.get("content")
                if not isinstance(content, str):
                    continue
                text = content.strip()
                if not text or len(text) > 2000:
                    continue
                if not chat_limiter.allow(str(user["id"])):
                    await manager.send(peer, {"type": "error", "reason": "rate_limited"})
                    continue
                with db() as c:
                    mid = c.execute("INSERT INTO messages(room_id,user_id,content) VALUES(?,?,?)",
                                    (room["id"], user["id"], text)).lastrowid
                    created = c.execute("SELECT created_at FROM messages WHERE id=?", (mid,)).fetchone()[0]
                await manager.broadcast(code, {"type": "chat", "id": mid, "user_id": user["id"],
                                               "user": user["username"], "content": text,
                                               "created_at": created})

            elif kind == "state":
                if "audio" in msg and isinstance(msg["audio"], bool):
                    peer.audio = msg["audio"]
                if "video" in msg and isinstance(msg["video"], bool):
                    peer.video = msg["video"]
                await manager.broadcast(code, {"type": "state", "peer_id": peer.id,
                                               "audio": peer.audio, "video": peer.video},
                                        exclude=peer.id)

            elif kind == "end":
                if room["host_id"] == user["id"]:
                    await end_room(code)
                    break
                await manager.send(peer, {"type": "error", "reason": "forbidden"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket error")
    finally:
        peers.pop(peer.id, None)
        if not peers:
            manager.rooms.pop(code, None)
        with db() as c:
            c.execute("UPDATE sessions SET left_at=datetime('now') WHERE id=?", (sid,))
        await manager.broadcast(code, {"type": "peer-left", "peer_id": peer.id})
