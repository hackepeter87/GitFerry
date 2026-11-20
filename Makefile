PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

.PHONY: doctor setup test test-integration test-integration-diagnostics test-integration-failure-contract test-clean container-build

doctor:
	./scripts/doctor.sh

setup:
	python3 -m venv .venv
	.venv/bin/pip install --disable-pip-version-check '.[dev]'
	cd frontend && npm ci --ignore-scripts

test:
	PYTHONPATH=backend/src $(RUFF) check backend tests/integration
	PYTHONPATH=backend/src $(RUFF) format --check backend tests/integration
	PYTHONPATH=backend/src $(MYPY) backend/src
	PYTHONPATH=backend/src $(PYTEST) backend/tests -q
	cd frontend && npm run build

test-integration:
	./tests/integration/run.sh

test-integration-diagnostics:
	./tests/integration/capture_diagnostics.sh

test-integration-failure-contract:
	./tests/integration/failure_contract.sh

test-clean:
	./tests/integration/clean.sh

container-build:
	docker build -f containers/gitferry.Dockerfile -t gitferry:dev .
