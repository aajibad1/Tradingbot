-- core-api relational schema (Cloud SQL Postgres in prod).
-- db.py create_all() generates the equivalent for SQLite dev/test; this file is
-- the canonical Postgres migration (apply once per environment).

CREATE TABLE IF NOT EXISTS tenants (
    id                 VARCHAR(64)  PRIMARY KEY,          -- matches shared.tenant.validate_tenant
    display_name       VARCHAR(200) NOT NULL DEFAULT '',
    market             VARCHAR(16),                       -- 'africa' | 'global' (set at onboarding)
    region             VARCHAR(8),                        -- ISO country code (set at onboarding)
    onboarding_status  VARCHAR(24)  NOT NULL DEFAULT 'account_created',  -- see OnboardingStatus
    kyc_status         VARCHAR(16)  NOT NULL DEFAULT 'none',             -- none|pending|verified|rejected
    live_enabled       BOOLEAN      NOT NULL DEFAULT FALSE,      -- paper until explicitly promoted
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS onboarding_events (
    id           BIGSERIAL    PRIMARY KEY,
    tenant_id    VARCHAR(64)  NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action       VARCHAR(64)  NOT NULL,                  -- e.g. 'region.selected', 'policy.evaluated'
    from_status  VARCHAR(24),
    to_status    VARCHAR(24),
    detail       VARCHAR(500) NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_onboarding_events_tenant ON onboarding_events(tenant_id);

CREATE TABLE IF NOT EXISTS users (
    id          VARCHAR(128) PRIMARY KEY,                 -- Firebase uid
    email       VARCHAR(320),
    tenant_id   VARCHAR(64)  NOT NULL REFERENCES tenants(id),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id  VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role     VARCHAR(16)  NOT NULL,                       -- owner|admin|viewer
    PRIMARY KEY (user_id, role)
);
