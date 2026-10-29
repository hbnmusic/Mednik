"""Single-password sign-in for hosted deployments (MEDNIK_APP_PASSWORD).

When the password is set, every page and API call needs a signed session cookie; /login issues it. The cookie
is an expiry timestamp plus an HMAC keyed by the password, so sessions survive server restarts (a free host
that sleeps) and changing the password signs everyone out. Failed attempts are slowed and rate-limited per
client address. When the password is unset (local use, tests) nothing is enforced.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import get_settings

COOKIE = "mednik_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/login", "/logout", "/healthz", "/favicon.ico"}
MAX_FAILURES, WINDOW_S = 5, 15 * 60

router = APIRouter()
_failures: dict[str, deque[float]] = defaultdict(deque)


def _key(password: str) -> bytes:
    return hashlib.sha256(f"mednik-session|{password}".encode()).digest()


def make_token(password: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(_key(password), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(password: str, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < (now or time.time()):
        return False
    good = hmac.new(_key(password), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else "/"


async def require_login(request: Request, call_next):
    password = get_settings().app_password
    if not password or request.url.path in OPEN_PATHS:
        return await call_next(request)
    if valid_token(password, request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign-in required"}, status_code=401)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={target}", status_code=303)


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>mednik · sign in</title><style>
:root{{--bg:#f6f6f4;--card:#fff;--ink:#1b1d1f;--muted:#5d636b;--line:#d9dbde;--accent:#1f5fbf;--err:#b42318}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121416;--card:#1b1e21;--ink:#e8eaed;--muted:#a0a6ad;--line:#33383d;--accent:#6ea2f0;--err:#f0857a}}}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}}
form{{width:100%;max-width:340px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:28px}}
h1{{margin:0 0 4px;font-size:20px;letter-spacing:.2px}}p{{margin:0 0 20px;color:var(--muted);font-size:13px}}
label{{display:block;font-size:13px;margin-bottom:6px;color:var(--muted)}}
input{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}}
input:focus{{outline:2px solid var(--accent);outline-offset:1px}}
button{{margin-top:16px;width:100%;padding:10px;border:0;border-radius:6px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:var(--err);font-size:13px;margin:12px 0 0}}</style></head><body>
<form method="post" action="/login"><h1>mednik</h1><p>Medical affairs strategic intelligence</p>
<label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="{next}"><button type="submit">Sign in</button>{error}</form></body></html>"""


def _page(nxt: str, error: str = "", status: int = 200) -> HTMLResponse:
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(_PAGE.format(next=html.escape(_safe_next(nxt), quote=True), error=err), status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.get("/login", include_in_schema=False)
def login_form(next: str = "/") -> Response:  # noqa: A002
    if not get_settings().app_password:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _page(next)


@router.post("/login", include_in_schema=False)
async def login(request: Request, password: str = Form(""), next: str = Form("/")) -> Response:  # noqa: A002
    expected = get_settings().app_password
    if not expected:
        return RedirectResponse(_safe_next(next), status_code=303)
    ip, now = _client(request), time.time()
    q = _failures[ip]
    while q and q[0] < now - WINDOW_S:
        q.popleft()
    if len(q) >= MAX_FAILURES:
        return _page(next, "Too many attempts. Try again in 15 minutes.", 429)
    if not hmac.compare_digest(password.encode(), expected.encode()):
        q.append(now)
        await asyncio.sleep(1.0)
        return _page(next, "Incorrect password.", 401)
    q.clear()
    resp = RedirectResponse(_safe_next(next), status_code=303)
    resp.set_cookie(COOKIE, make_token(expected), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", path="/")
    return resp


@router.get("/logout", include_in_schema=False)
def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
