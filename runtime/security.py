"""
nexql/runtime/security.py
─────────────────────────
Authentication, authorization, audit logging, and query-guard subsystem.

WHY SEPARATED FROM THE EXECUTOR:
  The original security_features.py was imported directly into
  execute_nexql() but also exported UI-facing helpers.  Those concerns
  are now cleanly split:

    • runtime/security.py  — AuthProvider, RBAC, audit log, rate limiter,
                             query-depth / complexity guards.
                             NO UI imports.  NO Tkinter.
    • ide/ panels          — display-only wrappers that call the runtime
                             security API via IPC.

  The executor depends on this module; the IDE does not import it directly.

PUBLIC API (injected into ExecutionContext):
  - check_field_auth(field_directives, user_role) -> AuthResult
  - check_depth(fields, max_depth)               -> DepthResult
  - check_complexity(doc, max_cost)              -> ComplexityResult
  - AuditLog.record(event)
  - RateLimiter.allow(role, cost)                -> bool
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Role hierarchy ───────────────────────────────────────────────────────────

_ROLE_LEVEL: dict[str, int] = {
    "admin": 4, "moderator": 3, "user": 2, "guest": 1, "anonymous": 0
}


def role_satisfies(user_role: str, required_role: str) -> bool:
    """Return True if *user_role* meets or exceeds *required_role*."""
    return _ROLE_LEVEL.get(user_role, 0) >= _ROLE_LEVEL.get(required_role, 0)


# ─── Auth result ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuthResult:
    allowed:    bool
    reason:     Optional[str] = None

    @classmethod
    def ok(cls) -> "AuthResult":
        return cls(allowed=True)

    @classmethod
    def denied(cls, reason: str) -> "AuthResult":
        return cls(allowed=False, reason=reason)


def check_field_auth(directives: list[dict], user_role: str) -> AuthResult:
    """Inspect @auth(role:X) directive on a field and evaluate access."""
    for d in directives or []:
        if d.get("name") == "auth":
            required = d.get("args", {}).get("role")
            if required and not role_satisfies(user_role, required):
                return AuthResult.denied(
                    f"Field requires role '{required}'; caller has '{user_role}'"
                )
    return AuthResult.ok()


# ─── Query depth / complexity guards ─────────────────────────────────────────

MAX_QUERY_DEPTH  = 8
MAX_QUERY_COST   = 2_000
MAX_FIELD_COUNT  = 500

@dataclass(frozen=True)
class DepthResult:
    ok:     bool
    depth:  int
    reason: Optional[str] = None


@dataclass(frozen=True)
class ComplexityResult:
    ok:   bool
    cost: int
    reason: Optional[str] = None


def check_depth(fields: list, max_depth: int = MAX_QUERY_DEPTH) -> DepthResult:
    """Measure maximum nesting depth of a field-selection list."""
    def _depth(nodes: list, current: int) -> int:
        if current > max_depth:
            return current
        deepest = current
        for n in nodes:
            children = n.fields if hasattr(n, "fields") else (n.get("fields") or [])
            if children:
                deepest = max(deepest, _depth(children, current + 1))
        return deepest

    d = _depth(fields, 1)
    if d > max_depth:
        return DepthResult(ok=False, depth=d,
                           reason=f"Query depth {d} exceeds maximum {max_depth}")
    return DepthResult(ok=True, depth=d)


def check_complexity(cost: int, max_cost: int = MAX_QUERY_COST) -> ComplexityResult:
    if cost > max_cost:
        return ComplexityResult(ok=False, cost=cost,
                                reason=f"Query cost {cost} exceeds maximum {max_cost}")
    return ComplexityResult(ok=True, cost=cost)


# ─── Rate limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter, per-role, thread-safe."""

    def __init__(self, limit_per_minute: int = 400) -> None:
        self.limit  = limit_per_minute
        self.window = 60
        self._lock   = threading.Lock()
        self._buckets: dict[str, tuple[int, int]] = {}   # role → (tokens, window_start)

    def allow(self, role: str, cost: int = 1) -> bool:
        now = int(time.time())
        with self._lock:
            tokens, start = self._buckets.get(role, (self.limit, now))
            if now - start >= self.window:
                tokens, start = self.limit, now
            if cost <= 0:
                return True
            if tokens >= cost:
                self._buckets[role] = (tokens - cost, start)
                return True
            self._buckets[role] = (tokens, start)
            return False

    def remaining(self, role: str) -> int:
        now = int(time.time())
        with self._lock:
            tokens, start = self._buckets.get(role, (self.limit, int(time.time())))
            if now - start >= self.window:
                return self.limit
            return max(0, tokens)


_DEFAULT_RATE_LIMITER: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _DEFAULT_RATE_LIMITER
    if _DEFAULT_RATE_LIMITER is None:
        _DEFAULT_RATE_LIMITER = RateLimiter()
    return _DEFAULT_RATE_LIMITER


# ─── Token-based authentication ───────────────────────────────────────────────

@dataclass
class _UserRecord:
    username:      str
    password_hash: str
    role:          str


class AuthProvider:
    """In-memory authentication provider (swap for JWT/OAuth in production)."""

    def __init__(self) -> None:
        self._users:  dict[str, _UserRecord] = {}
        self._tokens: dict[str, dict] = {}   # token → {username, role, created_at}

    def register(self, username: str, password: str, role: str = "user") -> bool:
        if username in self._users:
            return False
        ph = hashlib.sha256(password.encode()).hexdigest()
        self._users[username] = _UserRecord(username, ph, role)
        return True

    def authenticate(self, username: str, password: str) -> tuple[bool, str]:
        user = self._users.get(username)
        if not user:
            return False, "User not found"
        ph = hashlib.sha256(password.encode()).hexdigest()
        if user.password_hash != ph:
            return False, "Invalid password"
        token = hashlib.sha256(f"{username}:{time.time()}".encode()).hexdigest()
        self._tokens[token] = {"username": username, "role": user.role,
                                "created_at": time.time()}
        return True, token

    def validate_token(self, token: str) -> tuple[bool, Optional[str], str]:
        """Returns (valid, username, role)."""
        entry = self._tokens.get(token)
        if not entry:
            return False, None, "anonymous"
        return True, entry["username"], entry["role"]


_DEFAULT_AUTH: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    global _DEFAULT_AUTH
    if _DEFAULT_AUTH is None:
        _DEFAULT_AUTH = AuthProvider()
    return _DEFAULT_AUTH


# ─── Audit log ────────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    timestamp:  float
    user_role:  str
    method:     str
    target:     str
    success:    bool
    cost:       int
    error_code: Optional[str] = None
    query_hash: Optional[str] = None


class AuditLog:
    """Append-only audit log.  Persists to JSONL; in-memory ring-buffer for reads."""

    def __init__(self, log_path: Optional[Path] = None, max_memory: int = 500) -> None:
        self._path   = log_path or (Path.home() / ".piql" / "audit.jsonl")
        self._max    = max_memory
        self._buffer: list[AuditEvent] = []
        self._lock   = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._buffer.append(event)
            if len(self._buffer) > self._max:
                self._buffer = self._buffer[-self._max:]
        try:
            with self._path.open("a") as fh:
                fh.write(json.dumps({
                    "ts": event.timestamp, "role": event.user_role,
                    "method": event.method, "target": event.target,
                    "ok": event.success, "cost": event.cost,
                    "err": event.error_code, "qhash": event.query_hash,
                }) + "\n")
        except OSError:
            pass   # never let audit failure crash execution

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            events = self._buffer[-n:]
        return [{"ts": e.timestamp, "role": e.user_role, "method": e.method,
                 "target": e.target, "ok": e.success, "cost": e.cost,
                 "err": e.error_code} for e in reversed(events)]


_DEFAULT_AUDIT: Optional[AuditLog] = None


def get_audit_log() -> AuditLog:
    global _DEFAULT_AUDIT
    if _DEFAULT_AUDIT is None:
        _DEFAULT_AUDIT = AuditLog()
    return _DEFAULT_AUDIT
