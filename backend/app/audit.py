from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog


def audit(
    session: Session,
    user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    **details: Any,
) -> None:
    session.add(
        AuditLog(user_id=user_id, action=action, target_type=target_type, target_id=target_id, details=details or None)
    )
