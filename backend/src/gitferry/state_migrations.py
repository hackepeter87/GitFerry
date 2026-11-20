"""Ordered SQLite schema migrations for GitFerry control state."""

from __future__ import annotations

SCHEMA_VERSION = 4

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
}
