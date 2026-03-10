from __future__ import annotations

from fastapi import Request, Response

from .db import SessionLocal
from .models import AuditLog

EXCLUDED_PATHS = {"/docs", "/openapi.json", "/redoc", "/favicon.ico"}


def should_audit(request: Request) -> bool:
    if request.url.path in EXCLUDED_PATHS:
        return False
    if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
        return False
    return True


async def audit_middleware(request: Request, call_next):
    response: Response = await call_next(request)

    if not should_audit(request):
        return response

    user = getattr(request.state, "authenticated_user", None)
    if user is None and request.url.path != "/auth/login":
        return response

    db = SessionLocal()
    try:
        entry = AuditLog(
            actor_user_id=getattr(user, "id", None),
            actor_role=getattr(user, "role", None),
            endpoint=request.url.path,
            method=request.method,
            patient_id=getattr(request.state, "audit_scope_patient_id", None),
            decision=getattr(request.state, "audit_decision", "allowed" if response.status_code < 400 else "denied"),
            reason=getattr(request.state, "audit_reason", "authorized" if response.status_code < 400 else "request_failed"),
            http_status=response.status_code,
        )
        db.add(entry)
        db.commit()
    finally:
        db.close()
    return response
