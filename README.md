# Meet — Video Conferencing (React + FastAPI + SQLite)

Browser-to-browser video meetings using WebRTC. FastAPI handles auth, rooms, chat storage and WebSocket signaling. SQLite stores users, rooms, messages and sessions. Planning docs: `docs/PLAN.md`.

## Features
Register/login · create, join (code or link) and end meetings · audio/video mesh (default max 6 people) · mute/camera toggles · screen share · in-meeting chat with saved history · "your meetings" list · dark mode · copy invite link · skip link, focus states, loading/empty/error states, 404 page.

## Run (development)
```bash
# 1) Backend  (http://localhost:8000)
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then set SECRET_KEY (python -c "import secrets;print(secrets.token_urlsafe(48))")
uvicorn app.main:app --reload --port 8000

# 2) Frontend (http://localhost:5173)
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 in two different browsers (or one normal + one private window), register two users, create a meeting in one and join with the code in the other.

## Test
```bash
cd backend && python -m pytest -q
```
Covers auth, validation, rate limiting, room permissions, chat-history authorization, WebSocket auth, signaling relay, chat persistence, host-only end, security headers. There are **no automated browser/WebRTC tests**; test calls manually.

## Production notes
- Set `APP_ENV=production` and a strong `SECRET_KEY` (the app refuses to start without it). This also disables `/docs` and enables HSTS.
- Camera/mic need **HTTPS** (except on localhost). Put uvicorn behind nginx/Caddy with TLS, serve `frontend/dist`, and proxy `/api` and `/ws` (with WebSocket upgrade) to port 8000. Add a CSP on the frontend host, e.g. `default-src 'self'; connect-src 'self' wss:; media-src 'self' blob:; style-src 'self'; img-src 'self' data:`.
- Set `ALLOWED_ORIGINS` to your real site origin.
- **TURN server:** the app only uses a public STUN server. Users behind strict NATs/corporate firewalls will fail to connect without TURN (e.g. coturn). Add it to `ICE` in `frontend/src/pages/Room.jsx` using short-lived credentials generated server-side.
- Run **one** uvicorn worker: room membership and rate limits are kept in memory.

## Known limitations
- Mesh topology: bandwidth grows with each participant. Beyond ~6 people, use an SFU (LiveKit, mediasoup).
- JWT lives in `sessionStorage` (cleared when the tab closes) and is sent as a bearer header. HttpOnly cookies + CSRF protection would be stronger.
- Anyone logged in who has the meeting code can join (the code is the secret). There is no waiting room.
- No password reset, email verification, CAPTCHA, or MFA yet.
- Not implemented (not relevant here): cookie banner (no cookies), UTM tracking, print layout beyond hiding controls.
