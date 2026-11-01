"""Single-password sign-in for Glidepath.

Set GLIDEPATH_PASSWORD to require it. Sessions are signed cookies valid for 30
days, keyed by GLIDEPATH_SECRET (set a long random value when hosting; without
it a random key is made at start-up, so every restart signs you out).
Failed attempts are rate-limited per client and overall.
"""
import collections
import hashlib
import hmac
import os
import secrets
import threading
import time

PASSWORD = os.environ.get("GLIDEPATH_PASSWORD", "")
SECRET = (os.environ.get("GLIDEPATH_SECRET") or secrets.token_hex(32)).encode()
COOKIE = "glidepath_session"
MAX_AGE = 30 * 86400
WINDOW, PER_CLIENT, OVERALL = 15 * 60, 8, 30

enabled = bool(PASSWORD)
_fails = collections.defaultdict(collections.deque)
_lock = threading.Lock()


def _sign(msg):
    return hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()


def make_cookie(secure):
    exp = str(int(time.time()) + MAX_AGE)
    attrs = f"{COOKIE}={exp}.{_sign(exp)}; Path=/; Max-Age={MAX_AGE}; HttpOnly; SameSite=Lax"
    return attrs + ("; Secure" if secure else "")


def clear_cookie(secure):
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def valid_session(cookie_header):
    if not enabled:
        return True
    for part in (cookie_header or "").split(";"):
        name, _, val = part.strip().partition("=")
        if name == COOKIE and "." in val:
            exp, sig = val.split(".", 1)
            if exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp)):
                return True
    return False


def _prune(q, now):
    while q and now - q[0] > WINDOW:
        q.popleft()


def locked_out(client):
    now = time.time()
    with _lock:
        mine, every = _fails[client], _fails["*"]
        _prune(mine, now); _prune(every, now)
        return len(mine) >= PER_CLIENT or len(every) >= OVERALL


def check_password(client, attempt):
    """True if correct. Records failures for rate limiting."""
    ok = hmac.compare_digest(hashlib.sha256(attempt.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest())
    if not ok:
        now = time.time()
        with _lock:
            _fails[client].append(now)
            _fails["*"].append(now)
    return ok
