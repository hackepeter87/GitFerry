# ADR-0020: Align the Python contract with the documented development baseline

## Status
Accepted

## Context
The initial foundation metadata declared Python >=3.9 and selected 3.9 for Ruff/MyPy.
The first development README instead required Python 3.11+. Architecture and product
requirements did not promise a 3.9 runtime, and the qualified OCI image and hosted CI use
3.13.9. Local 3.9 test runs and transitive MyPy workarounds are historical evidence, not a
separate supported-release commitment. No stable release has been published.

The two updates in Dependabot PR #38 require Python >=3.10:
[pytest 9.0.3 metadata](https://github.com/pytest-dev/pytest/blob/9.0.3/pyproject.toml)
and [setuptools 84.1.0 metadata](https://github.com/pypa/setuptools/blob/v84.1.0/pyproject.toml).
Keeping >=3.9 would advertise an installation the build and test stack cannot satisfy.

## Decision
Use Python >=3.11, preserving the existing documented development prerequisite. Correct
package metadata, Ruff and MyPy together. Setup and doctor reject an older selected
interpreter with an explicit remediation before creating a virtual environment. A caller
can select an installed interpreter through `PYTHON_BOOTSTRAP`; existing environments
must be recreated using a supported interpreter. Setup does not install a system Python.

The required quality job installs and runs the complete backend suite on minimum Python
3.11 as well as its existing 3.13.9 execution. OCI remains on its existing immutable 3.13.9
base image. This does not claim qualification of every future Python release.

The higher Ruff target requires spelling-only modernization: `datetime.UTC`, built-in
`TimeoutError`, and explicit `zip(strict=False)` preserving existing truncation behavior.
No business rule or migration format changes. Historical 3.9 run reports stay unchanged.

## Dependency assessment
The selected alerts concern pytest's temporary directory handling and setuptools build/
legacy installation paths, not GitFerry's runtime authorization boundary. GitFerry uses
PEP 517 with `setuptools.build_meta`, no `setup_requires` or legacy egg installer. The
updates remove affected direct pins rather than dismissing the alerts. Upstream notes:
[pytest changelog](https://docs.pytest.org/en/stable/changelog.html#pytest-9-0-3-2026-04-07),
[setuptools history](https://setuptools.pypa.io/en/stable/history.html#v84-1-0).
Tests exercise installation, collection, existing fixtures and actual OCI/Gitea behavior;
GitHub alert closure is verified after integration, separately from local test success.

## Consequences
Python 3.9/3.10 users must select 3.11+ for source development; the existing OCI deployment
needs no host Python change. Application, manifest and SQLite versions remain independent.
The metadata correction is explicit, not a silent withdrawal of an owner-approved feature.
