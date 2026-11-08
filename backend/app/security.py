import collections
import threading
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from . import config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# Used so unknown-user logins cost the same time as known-user logins.
DUMMY_HASH = hash_password("dummy-password-for-timing")


def create_token(user_id: int, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "usr": username, "iat": now,
               "exp": now + timedelta(minutes=config.TOKEN_MINUTES)}
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str):
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"],
                          options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None


class RateLimiter:
    """Sliding-window limiter, in-memory (single process)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 2000:
                self._prune(now)
            return True

    def _prune(self, now: float):
        stale = [k for k, q in self.hits.items() if not q or now - q[-1] > self.window]
        for k in stale:
            del self.hits[k]

    def reset(self):
        with self.lock:
            self.hits.clear()
