from __future__ import annotations

from django.core.cache import cache

_FAIL_LIMIT = 5
_FAIL_WINDOW = 10 * 60


def _fail_key(token: str, ip: str) -> str:
    return f"share-gate-fail:{token}:{ip or 'unknown'}"


def gate_blocked(token: str, ip: str) -> bool:
    try:
        n = int(cache.get(_fail_key(token, ip), 0) or 0)
    except Exception:
        return False
    return n >= _FAIL_LIMIT


def record_gate_failure(token: str, ip: str) -> int:
    key = _fail_key(token, ip)
    try:
        cache.add(key, 0, timeout=_FAIL_WINDOW)
        return int(cache.incr(key))
    except Exception:
        return 1


def clear_gate_failures(token: str, ip: str) -> None:
    try:
        cache.delete(_fail_key(token, ip))
    except Exception:
        pass
