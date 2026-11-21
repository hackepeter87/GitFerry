PYTHON_BOOTSTRAP ?= python3
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
GITFERRY_VERSION := 0.2.0rc1
GITFERRY_SOURCE_REVISION := $(shell git rev-parse HEAD)

.PHONY: doctor setup test test-browser test-integration test-integration-diagnostics test-integration-failure-contract test-clean container-build

doctor:
	PYTHON_BOOTSTRAP=$(PYTHON_BOOTSTRAP) ./scripts/doctor.sh

setup:
	$(PYTHON_BOOTSTRAP) scripts/check_python.py
	$(PYTHON_BOOTSTRAP) -m venv .venv
	.venv/bin/pip install --disable-pip-version-check '.[dev]'
	cd frontend && npm ci --ignore-scripts

test:
	PYTHONPATH=backend/src $(RUFF) check backend tests/integration
	PYTHONPATH=backend/src $(RUFF) format --check backend tests/integration
	PYTHONPATH=backend/src $(MYPY) backend/src
	PYTHONPATH=backend/src $(PYTEST) backend/tests -q
	cd frontend && npm run build

test-browser:
	cd frontend && npm run test:e2e

test-integration:
	./tests/integration/run.sh

test-integration-diagnostics:
	./tests/integration/capture_diagnostics.sh

test-integration-failure-contract:
	./tests/integration/failure_contract.sh

test-clean:
	./tests/integration/clean.sh

container-build:
	docker build \
		--build-arg GITFERRY_VERSION=$(GITFERRY_VERSION) \
		--build-arg GITFERRY_SOURCE_REVISION=$(GITFERRY_SOURCE_REVISION) \
		-f containers/gitferry.Dockerfile -t gitferry:dev .
