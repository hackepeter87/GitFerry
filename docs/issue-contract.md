# Implementation issue contract

Implementation issues are the public scope contract for one coherent user outcome. The issue
form requires the outcome, scope, exclusions, observable acceptance criteria, tests/evidence,
compatibility impact, dependencies, work kind, target release, and evidence level.

The dependency section must contain explicit `Depends on:` and `Blocks:` lines; use `None` when
there is no relationship. The body also includes `Target release`, `Work kind`, `Failure and
recovery`, and `Coherent execution boundary` headings. The evidence section must declare
`Evidence required: ...` (or `none`) and matching `evidence:…` labels. The
private GitHub Project supplies operational fields and must contain `Status`, `Priority`, `Type`
(or the normalized `Work Type` field),
`Area`, `Target release`, `Risk`, and `Needs Decision`. Public labels must include one each of
`type:…`, `area:…`, `priority:…`, and `risk:…`.

Run the credential-free audit against one saved JSON snapshot or a JSON list of snapshots:

```text
PYTHONPATH=backend/src .venv/bin/python scripts/issue_audit.py issue.json
```

The snapshot is `{ "issue": { ... }, "project": { ... } }`, or a nonempty list of those
objects. Labels may be strings or GitHub name objects. Project fields may be a name/value map
or an array. `Evidence required: none` needs no evidence label. Supported evidence flags are
`browser`, `recovery`, `directory`, `capacity`, and `immutable-artifact`.

For a reproducible read-only audit of the actual GitHub state:

```sh
gh api --paginate --slurp 'repos/hackepeter87/GitFerry/issues?state=all&per_page=100' > issues.json
gh project item-list 3 --owner hackepeter87 --limit 1000 --format json > project.json
.venv/bin/python scripts/issue_audit.py --github-issues issues.json --github-project project.json
```

Keep these exports and the resulting report as run evidence, not committed live status.
The importer ignores PRs, audits Issues #8–#36 and #47/#48 plus every open milestone issue,
and rejects a truncated Project export. Missing Project rows fail the audit. A snapshot is
not proof of current GitHub state after later edits; refresh it before final acceptance.
The audit makes no network calls or mutations and emits issue numbers and field-level findings,
not bodies, credentials or private URLs. Exit zero means the structural contract passed; it
does not certify semantic adequacy, dependency acyclicity or product completion. A separate
review must inspect the scope/dependency graph and actual evidence.

## Delivery classification and evidence

`Work kind` matches the Project Work Type: Feature, Bug, Maintenance, Release or Evidence.
The execution boundary explicitly distinguishes a coherent implementation, parent capability,
milestone acceptance or research/evidence task. Unresolved API/directory capabilities retain a
named research/decision gate, alternatives and an owning issue; labels never waive product scope.
A milestone acceptance item references its child outcomes and cannot substitute for them.

Tests and evidence list exact currently available commands and the new observations needed for
that issue. Do not imply that a generic integration pass already proves an unimplemented class.
The source/commit, CI run, review and artifact acceptance links belong in Issues/PRs and Actions;
requirements, supported behavior and limitations belong in repository docs. The delivery index
in [tasks/plan.md](../tasks/plan.md) links actual PRs without duplicating live Project status.
