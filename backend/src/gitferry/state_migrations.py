"""Ordered SQLite schema migrations for GitFerry control state."""

from __future__ import annotations

SCHEMA_VERSION = 16

MIGRATIONS: dict[int, str] = {
    1: """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE instances (
    instance_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    installation_marker TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (base_url, installation_marker)
);

CREATE TABLE realms (
    realm_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    external_marker TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE identity_bindings (
    binding_id TEXT PRIMARY KEY,
    source_realm_key TEXT NOT NULL REFERENCES realms(realm_key),
    source_login TEXT NOT NULL,
    source_external_id TEXT NOT NULL,
    source_ou_path TEXT NOT NULL,
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_account_id INTEGER NOT NULL,
    target_login TEXT NOT NULL,
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    created_at TEXT NOT NULL,
    UNIQUE (
        source_realm_key, source_login, source_external_id, source_ou_path,
        target_instance_key
    ),
    UNIQUE (target_instance_key, target_account_id)
);

CREATE TABLE object_origins (
    origin_id TEXT PRIMARY KEY,
    origin_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    origin_repository_scope TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (object_type IN ('issue', 'comment')),
    origin_global_id TEXT NOT NULL,
    origin_issue_index INTEGER,
    parent_origin_id TEXT REFERENCES object_origins(origin_id),
    origin_created_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (
        origin_instance_key, origin_repository_scope, object_type, origin_global_id
    )
);

CREATE TABLE object_mappings (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_repository_scope TEXT NOT NULL,
    target_object_type TEXT NOT NULL CHECK (target_object_type IN ('issue', 'comment')),
    target_global_id TEXT NOT NULL,
    target_issue_index INTEGER,
    target_native_created_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (origin_id, target_instance_key),
    UNIQUE (
        target_instance_key, target_repository_scope, target_object_type, target_global_id
    )
);

CREATE TABLE transfer_rounds (
    transfer_round_id TEXT PRIMARY KEY,
    origin_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    selection_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE baselines (
    baseline_id TEXT PRIMARY KEY,
    transfer_round_id TEXT NOT NULL REFERENCES transfer_rounds(transfer_round_id),
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    ruleset_name TEXT NOT NULL,
    ruleset_version INTEGER NOT NULL CHECK (ruleset_version > 0),
    baseline_json TEXT NOT NULL,
    desired_json TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    observation_revision INTEGER NOT NULL DEFAULT 1 CHECK (observation_revision > 0),
    origin_created_at TEXT,
    target_native_created_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (transfer_round_id, origin_id, ruleset_name, ruleset_version)
);

CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY,
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_repository_scope TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (
        operation_type IN ('create_issue', 'create_comment', 'update_issue')
    ),
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    baseline_id TEXT REFERENCES baselines(baseline_id),
    intent_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'prepared', 'executing', 'outcome_unknown', 'confirmed', 'conflict', 'failed'
        )
    ),
    executor_id TEXT,
    target_global_id TEXT,
    target_issue_index INTEGER,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE operation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES operations(operation_id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE conflicts (
    conflict_id TEXT PRIMARY KEY,
    operation_id TEXT REFERENCES operations(operation_id),
    conflict_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE executor_lease (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
""",
    2: """
CREATE TABLE transport_manifests (
    manifest_id TEXT PRIMARY KEY,
    digest_sha256 TEXT NOT NULL CHECK (length(digest_sha256) = 64),
    transfer_round_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('exported', 'received')),
    mode TEXT NOT NULL CHECK (mode IN ('forward', 'return')),
    sender_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    recipient_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    forward_manifest_id TEXT REFERENCES transport_manifests(manifest_id),
    forward_digest_sha256 TEXT,
    document_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        (mode = 'forward' AND forward_manifest_id IS NULL AND forward_digest_sha256 IS NULL)
        OR
        (mode = 'return' AND forward_manifest_id IS NOT NULL AND forward_digest_sha256 IS NOT NULL)
    )
);

CREATE TABLE transport_manifest_objects (
    manifest_id TEXT NOT NULL REFERENCES transport_manifests(manifest_id),
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    facts_json TEXT NOT NULL,
    PRIMARY KEY (manifest_id, origin_id)
);

CREATE TABLE transport_identity_claims (
    manifest_id TEXT NOT NULL REFERENCES transport_manifests(manifest_id),
    claim_index INTEGER NOT NULL CHECK (claim_index >= 0),
    realm_key TEXT NOT NULL,
    source_login TEXT NOT NULL,
    source_external_id TEXT NOT NULL,
    source_ou_path TEXT NOT NULL,
    related_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    related_account_id TEXT NOT NULL,
    related_login TEXT NOT NULL,
    claim_status TEXT NOT NULL CHECK (
        claim_status IN ('observed-not-approved', 'proposed')
    ),
    PRIMARY KEY (manifest_id, claim_index)
);

CREATE TABLE transport_import_observations (
    forward_manifest_id TEXT NOT NULL REFERENCES transport_manifests(manifest_id),
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    ruleset_name TEXT NOT NULL,
    ruleset_version INTEGER NOT NULL CHECK (ruleset_version > 0),
    observed_json TEXT NOT NULL,
    target_native_created_at TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (forward_manifest_id, origin_id)
);
""",
    3: """
CREATE TABLE operations_v3 (
    operation_id TEXT PRIMARY KEY,
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_repository_scope TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (
        operation_type IN (
            'create_issue', 'create_comment', 'update_issue', 'update_comment'
        )
    ),
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    baseline_id TEXT REFERENCES baselines(baseline_id),
    intent_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'prepared', 'blocked_parent', 'executing', 'outcome_unknown',
            'confirmed', 'conflict', 'failed'
        )
    ),
    executor_id TEXT,
    target_global_id TEXT,
    target_issue_index INTEGER,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO operations_v3 SELECT * FROM operations;
DROP TABLE operations;
ALTER TABLE operations_v3 RENAME TO operations;
""",
    4: """
CREATE TABLE object_origins_backup AS SELECT * FROM object_origins;
DROP TABLE object_origins;

CREATE TABLE object_origins (
    origin_id TEXT PRIMARY KEY,
    origin_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    origin_repository_scope TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (
        object_type IN ('issue', 'comment', 'organization', 'team', 'repository')
    ),
    origin_global_id TEXT NOT NULL,
    origin_issue_index INTEGER,
    parent_origin_id TEXT REFERENCES object_origins(origin_id),
    origin_created_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (
        origin_instance_key, origin_repository_scope, object_type, origin_global_id
    )
);

INSERT INTO object_origins SELECT * FROM object_origins_backup;
DROP TABLE object_origins_backup;

CREATE TABLE object_mappings_backup AS SELECT * FROM object_mappings;
DROP TABLE object_mappings;

CREATE TABLE object_mappings (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_repository_scope TEXT NOT NULL,
    target_object_type TEXT NOT NULL CHECK (
        target_object_type IN ('issue', 'comment', 'organization', 'team', 'repository')
    ),
    target_global_id TEXT NOT NULL,
    target_issue_index INTEGER,
    target_native_created_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (origin_id, target_instance_key),
    UNIQUE (
        target_instance_key, target_repository_scope, target_object_type, target_global_id
    )
);

INSERT INTO object_mappings SELECT * FROM object_mappings_backup;
DROP TABLE object_mappings_backup;

CREATE TABLE operations_backup AS SELECT * FROM operations;
DROP TABLE operations;

CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY,
    origin_id TEXT NOT NULL REFERENCES object_origins(origin_id),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_repository_scope TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (
        operation_type IN (
            'create_issue', 'create_comment', 'update_issue', 'update_comment',
            'create_organization', 'create_team', 'create_repository',
            'assign_team_repository', 'add_team_member', 'push_git_bundle'
        )
    ),
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    baseline_id TEXT REFERENCES baselines(baseline_id),
    intent_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'prepared', 'blocked_parent', 'executing', 'outcome_unknown',
            'confirmed', 'conflict', 'failed'
        )
    ),
    executor_id TEXT,
    target_global_id TEXT,
    target_issue_index INTEGER,
    error_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO operations SELECT * FROM operations_backup;
DROP TABLE operations_backup;
""",
    5: """
CREATE TABLE local_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    password_algorithm TEXT NOT NULL CHECK (password_algorithm = 'pbkdf2-sha256'),
    password_salt BLOB NOT NULL CHECK (length(password_salt) >= 16),
    password_hash BLOB NOT NULL CHECK (length(password_hash) = 32),
    password_iterations INTEGER NOT NULL CHECK (password_iterations >= 600000),
    password_changed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    disabled_at TEXT
);

CREATE TABLE auth_bootstrap (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    account_id TEXT NOT NULL UNIQUE REFERENCES local_accounts(account_id),
    completed_at TEXT NOT NULL
);

CREATE TABLE auth_sessions (
    session_id TEXT PRIMARY KEY CHECK (length(session_id) = 32),
    account_id TEXT NOT NULL REFERENCES local_accounts(account_id),
    verifier_hash BLOB NOT NULL CHECK (length(verifier_hash) = 32),
    csrf_hash BLOB NOT NULL CHECK (length(csrf_hash) = 32),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX auth_sessions_account_status
    ON auth_sessions(account_id, revoked_at, expires_at);

CREATE TABLE auth_login_limits (
    key_hash TEXT PRIMARY KEY CHECK (length(key_hash) = 64),
    window_started_at TEXT NOT NULL,
    failures INTEGER NOT NULL CHECK (failures >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE auth_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT REFERENCES local_accounts(account_id),
    username TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('bootstrap', 'login', 'logout', 'recovery')
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('succeeded', 'failed', 'rate_limited', 'revoked')
    ),
    created_at TEXT NOT NULL
);

CREATE INDEX auth_events_created_at ON auth_events(created_at);
""",
    6: """
CREATE TABLE connection_profiles (
    instance_key TEXT PRIMARY KEY REFERENCES instances(instance_key),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    base_url TEXT NOT NULL,
    credential_ref TEXT NOT NULL CHECK (length(credential_ref) BETWEEN 1 AND 64),
    ca_ref TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    locally_approved INTEGER NOT NULL CHECK (locally_approved IN (0, 1))
);
CREATE UNIQUE INDEX connection_profiles_active_url
    ON connection_profiles(base_url) WHERE enabled = 1;
CREATE TABLE connection_profile_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'retire', 'approve')),
    occurred_at TEXT NOT NULL
);
CREATE TABLE connection_checks (
    instance_key TEXT PRIMARY KEY REFERENCES connection_profiles(instance_key),
    profile_revision INTEGER NOT NULL,
    observed_identity TEXT NOT NULL,
    approved_identity TEXT,
    checked_at TEXT NOT NULL
);
""",
    7: """
CREATE TABLE transfer_plans (
    plan_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE CHECK (length(digest) = 64),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    profile_revision INTEGER NOT NULL CHECK (profile_revision >= 1),
    evidence_digest TEXT NOT NULL CHECK (length(evidence_digest) = 64),
    round_key TEXT NOT NULL,
    document_json TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN (
        'draft','queued','preflighting','running','confirmed','blocked','failed','cancelled'
    )),
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE plan_steps (
    plan_id TEXT NOT NULL REFERENCES transfer_plans(plan_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    operation_id TEXT NOT NULL REFERENCES operations(operation_id),
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (plan_id, step_index),
    UNIQUE (plan_id, operation_id)
);
CREATE TABLE plan_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES transfer_plans(plan_id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX transfer_plans_status ON transfer_plans(status, updated_at);
""",
    8: """
CREATE TABLE source_exports (
    export_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    source_identity_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'capturing', 'captured', 'failed')),
    candidate_digest TEXT,
    package_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((status = 'captured') = (package_digest IS NOT NULL))
);
""",
    9: """
CREATE TABLE package_receipts (
    package_id TEXT PRIMARY KEY,
    digest_sha256 TEXT NOT NULL CHECK (length(digest_sha256) = 64),
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    package_ref TEXT NOT NULL UNIQUE,
    document_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);
""",
    10: """
CREATE TABLE source_export_deliveries (
    export_id TEXT PRIMARY KEY REFERENCES source_exports(export_id),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
    candidate_digest TEXT CHECK (candidate_digest IS NULL OR length(candidate_digest) = 64),
    archive_sha256 TEXT CHECK (archive_sha256 IS NULL OR length(archive_sha256) = 64),
    archive_size INTEGER CHECK (archive_size >= 0),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((status = 'ready') = (archive_sha256 IS NOT NULL)),
    CHECK (status != 'ready' OR archive_size IS NOT NULL)
);
""",
    11: """
CREATE TABLE package_uploads (
    upload_id TEXT PRIMARY KEY,
    archive_size INTEGER NOT NULL CHECK (archive_size > 0),
    status TEXT NOT NULL CHECK (status IN ('receiving','validating','ready','failed')),
    archive_sha256 TEXT CHECK (archive_sha256 IS NULL OR length(archive_sha256) = 64),
    package_digest TEXT CHECK (package_digest IS NULL OR length(package_digest) = 64),
    summary_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status != 'ready' OR (archive_sha256 IS NOT NULL AND package_digest IS NOT NULL
                               AND summary_json IS NOT NULL))
);
""",
    12: """
CREATE TABLE plan_previews (
    plan_id TEXT NOT NULL REFERENCES transfer_plans(plan_id),
    generation INTEGER NOT NULL CHECK (generation > 0),
    plan_digest TEXT NOT NULL CHECK (length(plan_digest) = 64),
    status TEXT NOT NULL CHECK (status IN ('queued','checking','passed','blocked','failed')),
    reason TEXT,
    scope TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, generation),
    CHECK ((status IN ('blocked','failed')) = (reason IS NOT NULL))
);
CREATE UNIQUE INDEX plan_previews_active ON plan_previews(plan_id)
    WHERE status IN ('queued','checking');
""",
    13: """
CREATE TABLE identity_candidates (
    candidate_id TEXT PRIMARY KEY,
    realm_key TEXT NOT NULL REFERENCES realms(realm_key),
    source_login TEXT NOT NULL,
    source_external_id TEXT NOT NULL,
    source_ou_path TEXT NOT NULL,
    source_email TEXT,
    target_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    target_account_id INTEGER NOT NULL CHECK (target_account_id > 0),
    target_login TEXT NOT NULL,
    target_external_id TEXT,
    target_ou_path TEXT,
    target_native_id TEXT,
    authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
    profile_revision INTEGER NOT NULL CHECK (profile_revision > 0),
    prerequisites_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','rejected','approved','conflict')),
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (realm_key, source_login, source_external_id, source_ou_path,
            target_instance_key, target_account_id)
);

CREATE TABLE identity_decision_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES identity_candidates(candidate_id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    decision TEXT NOT NULL CHECK (decision IN ('pending','rejected','approved','conflict')),
    actor TEXT NOT NULL,
    reason TEXT,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (candidate_id, revision)
);

CREATE INDEX identity_candidates_status ON identity_candidates(status, updated_at);
CREATE INDEX identity_decision_events_candidate ON identity_decision_events(candidate_id, event_id);
""",
    14: """
ALTER TABLE identity_candidates ADD COLUMN target_account_evidence TEXT NOT NULL DEFAULT '{}';
""",
    15: """
CREATE TABLE directory_bootstraps (
    instance_key TEXT NOT NULL CHECK (length(instance_key) BETWEEN 1 AND 128)
      REFERENCES instances(instance_key),
    source_name TEXT NOT NULL CHECK (length(source_name) BETWEEN 1 AND 128),
    realm_key TEXT NOT NULL CHECK (length(realm_key) BETWEEN 1 AND 128)
      REFERENCES realms(realm_key),
    intent_digest TEXT NOT NULL CHECK (length(intent_digest)=64 AND
      intent_digest NOT GLOB '*[^0-9a-fA-F]*'),
    config_fingerprint TEXT NOT NULL CHECK (length(config_fingerprint)=64 AND
      config_fingerprint NOT GLOB '*[^0-9a-fA-F]*'),
    status TEXT NOT NULL CHECK (status IN
      ('prepared','executing','confirmed','outcome_unknown','conflict')),
    native_source_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (instance_key, source_name)
);
CREATE TABLE directory_bootstrap_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_key TEXT NOT NULL,
    source_name TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (instance_key, source_name)
      REFERENCES directory_bootstraps(instance_key, source_name)
);
CREATE INDEX directory_bootstrap_events_key
  ON directory_bootstrap_events(instance_key, source_name, event_id);

CREATE TABLE directory_configurations (
    configuration_id TEXT PRIMARY KEY,
    instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    source_name TEXT NOT NULL,
    directory_realm TEXT NOT NULL REFERENCES realms(realm_key),
    source_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    profile_revision INTEGER NOT NULL CHECK (profile_revision > 0),
    config_revision INTEGER NOT NULL CHECK (config_revision > 0),
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE (instance_key, source_name)
);
CREATE TABLE directory_configuration_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    configuration_id TEXT NOT NULL REFERENCES directory_configurations(configuration_id),
    config_revision INTEGER NOT NULL CHECK (config_revision > 0),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE (configuration_id, config_revision)
);
""",
    16: """
CREATE TABLE source_inventory_jobs (
    inventory_id TEXT PRIMARY KEY,
    source_instance_key TEXT NOT NULL REFERENCES instances(instance_key),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    request_json TEXT NOT NULL,
    source_identity_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','ready','blocked','failed')),
    document_json TEXT,
    document_digest TEXT CHECK (document_digest IS NULL OR length(document_digest) = 64),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((document_json IS NULL) = (document_digest IS NULL)),
    CHECK (status NOT IN ('ready','blocked') OR document_json IS NOT NULL),
    CHECK (status NOT IN ('queued','running') OR document_json IS NULL)
);
CREATE INDEX source_inventory_jobs_status ON source_inventory_jobs(status, created_at);
""",
}
