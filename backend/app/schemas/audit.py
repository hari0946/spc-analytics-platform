from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    audit_id: str
    actor_user_id: str | None
    actor_username: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict | list | None
    success: bool
    ip_address: str | None
    created_at: datetime
