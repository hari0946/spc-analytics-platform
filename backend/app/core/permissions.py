WILDCARD = "*"

USERS_MANAGE = "users:manage"
ROLES_MANAGE = "roles:manage"
AUDIT_READ = "audit:read"
PLANTS_MANAGE = "plants:manage"
ASSETS_MANAGE = "assets:manage"
TAGS_MANAGE = "tags:manage"
DATA_READ = "data:read"
DATA_UPLOAD = "data:upload"
ANALYSIS_RUN = "analysis:run"
REPORTS_GENERATE = "reports:generate"

ALL_PERMISSIONS = {
    WILDCARD, USERS_MANAGE, ROLES_MANAGE, AUDIT_READ, PLANTS_MANAGE, ASSETS_MANAGE,
    TAGS_MANAGE, DATA_READ, DATA_UPLOAD, ANALYSIS_RUN, REPORTS_GENERATE,
}

BUILTIN_ROLES = {"ADMIN", "ENGINEER", "VIEWER"}
DEFAULT_REGISTRATION_ROLE = "VIEWER"


def has_permission(granted: set[str] | list[str], needed: str) -> bool:
    return WILDCARD in granted or needed in granted
