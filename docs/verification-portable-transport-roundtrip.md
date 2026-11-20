# Portable transport manifest and separate-state roundtrip verification

## Executed scope

The probe ran on 2026-11-20 with two pinned Gitea 1.27.2 containers and two GitFerry worker
containers. GitFerry A and B had different SQLite named volumes and disjoint internal networks. A
could reach only Gitea A; B could reach only Gitea B. The harness copied only the explicit forward
and return transport directories. No SQLite file, Python object, credential file, or hidden mapping
was shared.

Interfaces exercised:

- local `transport-manifest.schema.json` Draft 2020-12 validation;
- exact-byte SHA-256 sidecar verification;
- SQLite schema migration 2 and transactional fact admission;
- existing durable `create_issue` and `update_issue` operations;
- Gitea REST issue GET, POST, and PATCH endpoints as functional user `issue-author`.

## Transported facts

The forward manifest carried its immutable ID, transfer-round ID, producer version, registered
sender/recipient installation markers, mode, creation time, selected repository and capabilities,
known limitations, qualified identity claim, A origin, A replica, original A comparison facts,
current selected values, and ruleset version. Native IDs were JSON strings.

The return added the exact forward manifest ID and digest, the confirmed B import facts, the later B
values, B replica mapping, and a second issue with its own B origin. Absence retained the explicit
`absence-does-not-delete` meaning.

Not transported: API credentials, connection authorization, approved identity bindings,
administrative rights, local operation status/events, executor leases, file locks, or conflict
resolutions. Admission did not perform a Gitea write.

## Observed roundtrip

Forward manifest `portable-forward-1` had digest
`3869a6fb01aee46034b62650787971a288b1af78592e5aab33309fc123ed319b` in the final recorded run. B
admitted it and created exactly one replica. A second B process received the same files, reported a
replay, reused the same origin and confirmed operation, and created no second object.

B changed the supported body field and created a new issue. Return manifest `portable-return-1`
had digest `76a90b1da524b2cef157fd35eec3e9c5ed7efb32ee2c98d80e7eec954ce89f41`.
Before application, a selected body change on A produced a visible drift block. Restoring the
selected A facts allowed the return even though the unselected control repository had changed.

The mapped A issue retained native A global ID `9` and issue index `8`; its body became “Changed on
B and returned through files”. The B-born issue kept its B origin and received native A global ID
`10` and issue index `9`. Both A results had native author `issue-author`. The B-born original
creation time was `2026-11-20T14:25:57Z`; its native A creation time was
`2026-11-20T14:26:01Z`, demonstrating preservation as separate origin data rather than restoration
of the native field.

A separate process replayed the return without another object. B then produced
`portable-return-repackaged` with the same origins under a new manifest ID; A admitted the new
envelope but reused the already confirmed local operations, again without duplication.

## Negative and restart cases

Automated tests passed for duplicate JSON keys, input and collection bounds, unknown schema
version, digest mismatch, path escape, wrong recipient, unknown or mismatched forward transfer,
conflicting immutable origin/replica facts, changed forward baseline, forged confirmed import,
manifest-ID/digest conflict, and attempts to import approval or success markers. SQLite v1→v2
migration retained existing data. Multi-object replay retained manifest order.

The existing real interruption probe still passed: a successful remote create followed by process
loss remains `outcome_unknown` and is not posted again. A unit case additionally confirmed that a
new manifest envelope for the same origin reports the existing block instead of bypassing it.

## Boundaries

Status: `passed` for the executed issue-only cases. This supports portable issue facts and the
bounded file roundtrip, not a general transfer engine.

- Transport-manifest v1 contains issues only; comments, attachments, reviews, and other types are
  not claimed.
- Only title, body, state, and sorted label names participate in the comparison ruleset.
- Regular Gitea create endpoints do not restore historical native creation times.
- SHA-256 detects byte changes but does not authenticate provenance; no signature exists.
- The result does not provide a general exactly-once guarantee across SQLite and Gitea.
- Permission dimensions beyond the separately tested Git code access remain unverified.
