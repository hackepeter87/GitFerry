from pathlib import Path

import pytest
from gitferry.packages import UnsafePackagePath, resolve_package_member


@pytest.mark.parametrize(
    "member", ["../escape", "/absolute", "safe/../../escape", "safe\\..\\escape"]
)
def test_package_path_escape_is_rejected(tmp_path: Path, member: str) -> None:
    with pytest.raises(UnsafePackagePath):
        resolve_package_member(tmp_path, member)


def test_package_member_resolves_below_owned_root(tmp_path: Path) -> None:
    resolved = resolve_package_member(tmp_path, "repositories/org/repo.bundle")
    assert resolved == tmp_path / "repositories" / "org" / "repo.bundle"
