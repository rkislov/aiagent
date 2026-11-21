import shutil
from pathlib import Path

import pytest

from superagent.service import Platform


@pytest.fixture
def platform(tmp_path):
    source = Path(__file__).resolve().parents[1]
    for folder in ("skills", "agents"):
        shutil.copytree(source / folder, tmp_path / folder)
    service = Platform(tmp_path)
    yield service
    service.close()
