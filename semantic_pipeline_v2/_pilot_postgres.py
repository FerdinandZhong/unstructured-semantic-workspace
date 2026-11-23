"""PostgreSQL for test-owned V2 pilot data, with no external connection option."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class PilotDatabaseError(RuntimeError):
    """The local PostgreSQL pilot could not complete an operation."""


class PilotPostgres:
    def __init__(self, binary_directory: Path | None, root: Path):
        self._bin = binary_directory
        self._root = root

    def _run(self, program: str, *args: str, sql: str | None = None) -> str:
        executable = (
            self._bin / program if self._bin else shutil.which(program)
        )
        if not executable:
            raise PilotDatabaseError(f"PostgreSQL binary missing: {program}")
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("PG")
        }
        environment["LC_ALL"] = "C"
        try:
            result = subprocess.run(
                [str(executable), *args], capture_output=True, text=True,
                input=sql, env=environment, cwd=self._root, timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PilotDatabaseError(f"{program} failed: {error}") from error
        if result.returncode:
            raise PilotDatabaseError(f"{program} failed: {result.stderr.strip()}")
        return result.stdout

    def sql(self, script: str, **params: str | int) -> str:
        variables = [f"--set={key}={value}" for key, value in params.items()]
        return self._run(
            "psql", "-X", "-w", "-qAt", "--set=ON_ERROR_STOP=1",
            f"--host={self._root}", "--port=5432", "--dbname=postgres", "--username=f42_owner",
            *variables, sql=script,
        ).strip()

    @classmethod
    @contextmanager
    def start(cls, binary_directory: Path | None = None, *, data_directory: Path | None = None):
        if binary_directory:
            binary_directory = binary_directory.resolve()
        # Short path for PostgreSQL's Unix socket pathname limit.
        with tempfile.TemporaryDirectory(prefix="v2-f42-", dir="/tmp") as temporary:
            cluster = cls(binary_directory, Path(temporary))
            data = data_directory if data_directory is not None else cluster._root / "data"
            marker = data / ".v2-workspace-postgres"
            if data_directory is not None and data.exists():
                if data.is_symlink() or not marker.is_file() or marker.is_symlink():
                    raise PilotDatabaseError("Refusing to start an unowned PostgreSQL directory")
            else:
                cluster._run(
                    "initdb", "-D", str(data), "--no-locale", "--encoding=UTF8",
                    "--username=f42_owner", "--auth-local=trust", "--auth-host=reject",
                )
                if data_directory is not None:
                    marker.touch()
            if (data / "postmaster.pid").exists():
                raise PilotDatabaseError("PostgreSQL data directory already has a server PID")
            try:
                cluster._run(
                    "pg_ctl", "-D", str(data), "-l", "postgres.log", "-w", "-t", "15",
                    "-o", f"-c listen_addresses= -c unix_socket_directories={cluster._root} -p 5432",
                    "start",
                )
                yield cluster
            finally:
                if (data / "postmaster.pid").exists():
                    cluster._run("pg_ctl", "-D", str(data), "-w", "-m", "fast", "stop")
