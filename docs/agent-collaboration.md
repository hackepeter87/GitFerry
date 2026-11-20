# Codex collaboration model

GitFerry uses project-scoped Codex configuration so milestone work can be split without relying on
personal settings. The main agent remains accountable for the complete result; subagents handle
bounded work whose coordination and review cost is lower than doing it in the main thread.

## Client baseline and configured roles

The repository configuration was checked with Codex CLI 0.166.0. The active desktop agent host for
the verification run reported 0.171.0-alpha.13; these are separate installed entry points and must
not be reported as one version. `codex debug models` is the authoritative availability check for
the signed-in CLI; the bundled catalog alone is insufficient.

| Role | Model | Reasoning | Responsibility |
|---|---|---:|---|
| Main | `gpt-6-astra` | `medium` | Requirements, planning, architecture, difficult implementation and debugging, integration, milestone acceptance |
| Explorer | `gpt-5.6-luna` | `medium` | Read-only, targeted repository evidence and existing patterns |
| Mechanic | `gpt-5.6-luna` | `medium` | Narrow pattern-driven edits with explicit files and acceptance checks |
| Reviewer | `gpt-5.6-sol` | `high` | Targeted review of correctness, regressions, failure paths, security boundaries and tests |
| Unnamed subagent default | `gpt-5.6-luna` | `medium` | Safety net against inheriting the main model accidentally |

`gpt-6-luna` was considered. Codex 0.166.0 contains it in the bundled catalog, but the active
catalog did not make it available for this account/client at the time of configuration. The
configured economical model is therefore `gpt-5.6-luna`, which the active catalog describes as
fast and affordable. If it becomes unavailable, use `gpt-5.6-terra` with `medium` reasoning as
the named fallback and report that the role moved to a balanced, higher-capability class. Do not
fall back through inheritance. For a reviewer that needs frontier-level judgment, the main agent
may explicitly select `gpt-6-astra`; that escalation must be visible in the task report.

## Delegation contract

Delegate when a task has a concrete boundary, useful independent work, and verifiable output.
Good candidates are targeted exploration, fixture or documentation updates derived from confirmed
behavior, focused tests, and repetitive implementation following an established pattern. Keep
architecture decisions, unclear root-cause analysis, overlapping edits, integration, and milestone
acceptance with the main agent.

Each delegated task states the file scope, relevant constraints, expected result, checks, and the
required concise return format. The main agent sequences dependent tasks and caps concurrency at
three subagents. One failed attempt may receive a targeted correction. After two failures on the
same problem, the main agent reassesses instead of repeating the delegation.

Fully determined changes belong in existing formatters, scripts, or codemods. A subagent is only
economical when its separate context, execution, review and possible rework cost less than handling
the work directly.

## Activation and verification

Codex loads `.codex/config.toml` and `.codex/agents/*.toml` only for trusted projects. Start a new
Codex chat or CLI session from the repository root after changing these files. Existing sessions
may keep the configuration with which they started.

Check the setup with:

```text
codex --version
codex debug models | jq -r '.models[] | [.slug, .default_reasoning_level] | @tsv'
codex doctor --json
codex --strict-config exec --ephemeral "Delegate a read-only repository lookup to the explorer and report its model."
```

The first three commands confirm the client, active model catalog and accepted configuration. A
role file records the configured choice; it does not prove which model served a particular run.
For runtime confirmation, inspect the subagent activity in the app or `/subagents` in the CLI and
record the model shown for that agent thread. If the client surface does not expose model identity,
mark runtime model verification as unavailable rather than inferring it from the role name.

Custom agent instructions can require read-only behavior, but they do not create a separate
read-only sandbox. Subagents inherit the parent's sandbox, approval mode, provider and MCP servers.
The main agent therefore supplies explicit file ownership and reviews the resulting diff.

No token or cost number is claimed unless the client exposes it for the actual run. Economy is
assessed from the whole completed milestone: model tier, parallel time saved, coordination, review,
failed attempts and rework.

## Verification record: 2026-11-20

- `codex --version` reported CLI 0.166.0; the desktop subagent rollout metadata reported host
  0.171.0-alpha.13.
- `codex debug models` listed `gpt-6-astra`, `gpt-5.6-luna`, `gpt-5.6-sol` and
  `gpt-5.6-terra`. It did not list `gpt-6-luna`, although `codex debug models --bundled` did.
- `codex doctor --json` parsed the project layer and reported the configured main model as
  `gpt-6-astra`.
- A bounded read-only explorer probe was explicitly started with `gpt-5.6-luna` and `medium`
  reasoning. The client activity metadata confirmed both runtime values; the agent returned only
  the requested repository evidence and made no edits.
- The probe confirms that the economical runtime selection is available and honored when explicitly
  selected. It does not prove that this already-running parent session reloaded the new named-role
  file. Confirm role-file selection in a new trusted-project session through the subagent activity
  view before treating profile activation as runtime-verified.
- A strict non-interactive CLI start confirmed project configuration loading. Full named-profile
  activation still requires the documented new trusted-project session and runtime activity check;
  do not infer it solely from the parsed TOML files.
