-- Phase 5: session invalidation + audit trail (the two additional tables explained in the Phase 3 report)
CREATE TABLE IF NOT EXISTS SPC_PLATFORM.METADATA.USER_SESSIONS (
    SESSION_ID VARCHAR NOT NULL,                 -- JWT 'jti'
    USER_ID    VARCHAR NOT NULL,
    CREATED_AT TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    EXPIRES_AT TIMESTAMP_NTZ NOT NULL,
    REVOKED_AT TIMESTAMP_NTZ,
    IP_ADDRESS VARCHAR,
    CONSTRAINT PK_USER_SESSIONS PRIMARY KEY (SESSION_ID),
    CONSTRAINT FK_US_USER FOREIGN KEY (USER_ID) REFERENCES SPC_PLATFORM.METADATA.USERS (USER_ID)
);

CREATE TABLE IF NOT EXISTS SPC_PLATFORM.METADATA.AUDIT_LOGS (
    AUDIT_ID       VARCHAR NOT NULL DEFAULT UUID_STRING(),
    ACTOR_USER_ID  VARCHAR,
    ACTOR_USERNAME VARCHAR,
    ACTION         VARCHAR NOT NULL,             -- e.g. LOGIN, LOGIN_FAILED, USER_CREATED
    ENTITY_TYPE    VARCHAR,
    ENTITY_ID      VARCHAR,
    DETAILS        VARIANT,
    SUCCESS        BOOLEAN NOT NULL DEFAULT TRUE,
    IP_ADDRESS     VARCHAR,
    CREATED_AT     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_AUDIT_LOGS PRIMARY KEY (AUDIT_ID)
);

-- Built-in roles (reference data, not sample data). Idempotent.
MERGE INTO SPC_PLATFORM.METADATA.ROLES r
USING (
    SELECT 'ADMIN' AS N, 'Full access, including user and role administration' AS D, PARSE_JSON('["*"]') AS P
    UNION ALL
    SELECT 'ENGINEER', 'Upload data, run analyses, generate reports and manage plant structure',
           PARSE_JSON('["data:read","data:upload","analysis:run","reports:generate","plants:manage","assets:manage","tags:manage"]')
    UNION ALL
    SELECT 'VIEWER', 'Read-only access to data, results and reports', PARSE_JSON('["data:read","reports:generate"]')
) s ON r.ROLE_NAME = s.N
WHEN NOT MATCHED THEN INSERT (ROLE_NAME, DESCRIPTION, PERMISSIONS) VALUES (s.N, s.D, s.P);
