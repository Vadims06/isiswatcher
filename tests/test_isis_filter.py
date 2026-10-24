import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("docker") is None, reason="the filter is loaded into a kernel from a privileged container")
def test_filter_verdicts():
    result = subprocess.run([str(REPO_ROOT / "filter" / "test" / "run.sh")], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
