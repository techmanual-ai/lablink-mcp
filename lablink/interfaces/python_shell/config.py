"""python_shell driver config.

PythonShellDriverConfig inherits only DriverConfig — the subprocess interface
has no network-style auth. Credentials and vendor SDK keys are expected to be
set in the environment before LabLink starts.

python_path points to any Python interpreter the user controls: a conda env,
a project venv, or the system Python. The driver spawns a long-lived subprocess
in that environment, so vendor SDKs (nidaqmx, picosdk, etc.) that only install
there become accessible from agent sessions.

Both filesystem path fields (python_path, working_dir) are tilde-expanded by
lablink/config.py at load time — TOML does not auto-expand tildes.
"""

from dataclasses import dataclass

from lablink.base import DriverConfig


@dataclass(kw_only=True)
class PythonShellDriverConfig(DriverConfig):
    """Config for a persistent Python interpreter subprocess.

    python_path: path to the interpreter to spawn. Examples:
        ~/miniconda3/envs/labwork/bin/python
        ~/project/.venv/bin/python
        /usr/bin/python3

    working_dir: optional working directory for the subprocess. The subprocess
        inherits the LabLink server's environment; cwd only affects relative
        path resolution inside user code.
    """

    python_path: str = ""
    working_dir: str | None = None
