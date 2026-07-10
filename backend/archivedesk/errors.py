from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import Response


def problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    category: str,
    message: str,
    retryable: bool = False,
    user_action: str | None = None,
    retry_at: str | None = None,
    details: dict[str, Any] | None = None,
) -> Response:
    request_id = getattr(request.state, "request_id", None)
    payload = {
        "error": {
            "code": code,
            "category": category,
            "message": message,
            "retryable": retryable,
            "user_action": user_action,
            "retry_at": retry_at,
            "request_id": request_id,
            "details": details or {},
        }
    }
    return Response(
        status_code=status_code,
        media_type="application/json",
        content=json.dumps(payload, ensure_ascii=False),
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def telegram_problem(message: str) -> tuple[int, str, str, bool, str | None]:
    lowered = message.casefold()
    mappings = (
        ("api id or api hash", 400, "API_CREDENTIALS_INVALID", "CONFIG", False, "EDIT_API_CREDENTIALS"),
        ("phone number is invalid", 400, "AUTH_PHONE_INVALID", "AUTH", False, "EDIT_PHONE"),
        ("verification code", 400, "AUTH_CODE_INVALID", "AUTH", False, "ENTER_CODE"),
        ("two-step verification", 400, "AUTH_PASSWORD_INVALID", "AUTH", False, "ENTER_PASSWORD"),
        ("login flow expired", 409, "AUTH_FLOW_EXPIRED", "AUTH", False, "REQUEST_NEW_CODE"),
        ("session has been revoked", 401, "SESSION_REVOKED", "AUTH", False, "LOGIN_AGAIN"),
        ("not installed", 500, "TELETHON_MISSING", "INTERNAL", False, None),
    )
    for fragment, status, code, category, retryable, action in mappings:
        if fragment in lowered:
            return status, code, category, retryable, action
    return 400, "TELEGRAM_REQUEST_FAILED", "TELEGRAM_PERMANENT", False, "RETRY_OR_ADJUST"
