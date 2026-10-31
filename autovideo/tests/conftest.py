"""Run frozen package coverage against its original portable file set."""
from pathlib import Path

import pytest

from g0.design_snapshot import build_snapshot


@pytest.fixture(scope="module", autouse=True)
def frozen_release_package(request, tmp_path_factory):
    module = request.module
    if Path(module.__file__).name != "test_final_release.py":
        yield
        return
    snapshot = tmp_path_factory.mktemp("design-package")
    build_snapshot(snapshot)
    original = module.ROOT
    module.ROOT = snapshot
    try:
        yield
    finally:
        module.ROOT = original
