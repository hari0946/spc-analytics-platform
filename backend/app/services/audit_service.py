import json
import logging
from datetime import datetime

from app.core import snowflake as sf
from app.schemas.audit import AuditLogOut
from app.schemas.common import PageParams

logger = logging.getLogger(__name__)
T = "SPC_PLATFORM.METADATA.AUDIT_LOGS"


def log(action: str, *, actor_user_id: str | None = None, actor_username: str | None = None,
        entity_type: str | None = None, entity_id: str | None = None, details: dict | None = None,
        success: bool = True, ip: str | None = None) -> None:
    """Write an audit record. Never raises: an audit failure must not break the business operation."""
    try:
        sf.execute(
            f"INSERT INTO {T} (ACTOR_USER_ID, ACTOR_USERNAME, ACTION, ENTITY_TYPE, ENTITY_ID, DETAILS, SUCCESS, IP_ADDRESS) "
            "SELECT %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, %s",
            (actor_user_id, actor_username, action, entity_type, entity_id,
             json.dumps(details) if details is not None else None, success, ip),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write audit log for action %s", action)


def list_logs(p: PageParams, action: str | None = None, actor: str | None = None,
              since: datetime | None = None, until: datetime | None = None) -> tuple[list[AuditLogOut], int]:
    where, args = [], []
    if action:
        where.append("ACTION = %s"); args.append(action.upper())
    if actor:
        where.append("LOWER(ACTOR_USERNAME) = %s"); args.append(actor.lower())
    if since:
        where.append("CREATED_AT >= %s"); args.append(since)
    if until:
        where.append("CREATED_AT <= %s"); args.append(until)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = sf.execute(f"SELECT COUNT(*) FROM {T} {clause}", args)[0][0]
    rows = sf.execute(
        f"SELECT AUDIT_ID, ACTOR_USER_ID, ACTOR_USERNAME, ACTION, ENTITY_TYPE, ENTITY_ID, DETAILS, SUCCESS, IP_ADDRESS, CREATED_AT "
        f"FROM {T} {clause} ORDER BY CREATED_AT DESC, AUDIT_ID LIMIT %s OFFSET %s",
        [*args, p.page_size, p.offset],
    )
    return [
        AuditLogOut(audit_id=r[0], actor_user_id=r[1], actor_username=r[2], action=r[3], entity_type=r[4],
                    entity_id=r[5], details=json.loads(r[6]) if r[6] else None, success=r[7],
                    ip_address=r[8], created_at=r[9])
        for r in rows
    ], total
