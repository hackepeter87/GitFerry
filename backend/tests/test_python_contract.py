import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_python_floor_matches_tools_and_ci() -> None:
    metadata = (ROOT / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.11"' in metadata
    assert 'target-version = "py311"' in metadata
    assert 'python_version = "3.11"' in metadata
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'python-version: "3.11"' in workflow
    assert 'python-version: "3.13.9"' in workflow
    assert "gitferry-minimum-python/bin/pytest backend/tests -q" in workflow
    assert re.search(r"pytest==9\.0\.3", metadata)
    assert re.search(r"setuptools==84\.1\.0", metadata)


def test_setup_rejects_old_python_before_environment_mutation(capsys) -> None:
    spec = importlib.util.spec_from_file_location(
        "python_contract", ROOT / "scripts/check_python.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main((3, 9)) == 1
    assert module.main((3, 10)) == 1
    assert module.main((3, 11)) == 0
    assert module.main((3, 13)) == 0
    assert "PYTHON_BOOTSTRAP" in capsys.readouterr().err
    makefile = (ROOT / "Makefile").read_text()
    assert makefile.index("$(PYTHON_BOOTSTRAP) scripts/check_python.py") < makefile.index(
        "$(PYTHON_BOOTSTRAP) -m venv .venv"
    )
