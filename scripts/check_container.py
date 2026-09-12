"""Exercise the Dokploy Compose stack on an isolated, disposable Docker database."""

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "fuellayer-ci-" + uuid.uuid4().hex[:10]
POSTGRES = PROJECT + "-postgres"
REVISION = os.environ.get("RELEASE_SHA", "a" * 40)


def docker(*args: str, capture: bool = False, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args], text=True, capture_output=capture, check=check, cwd=ROOT
    )
    return result.stdout.strip() if capture else ""


def main() -> None:
    # Do not load local .env files or service credentials into this disposable stack.
    env = {
        **os.environ,
        "RELEASE_SHA": REVISION,
        "DATABASE_URL": f"postgresql://fuellayer_ci:ci-only@{POSTGRES}:5432/fuellayer_ci",
        "CLERK_SECRET_KEY": "sk_test_container_fixture",
        "CLERK_WEBHOOK_SIGNING_SECRET": "whsec_container_fixture",
        "CLERK_JWT_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "GOOGLE_PLACES_API_KEY": "",
    }
    with tempfile.TemporaryDirectory(prefix=PROJECT) as temporary:
        override = Path(temporary) / "network.json"
        override.write_text(json.dumps({"networks": {"dokploy-network": {"name": PROJECT}}}))
        command = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            PROJECT,
            "-f",
            str(ROOT / "compose.deploy.yaml"),
            "-f",
            str(override),
        ]

        def compose(*args: str, check: bool = True) -> None:
            subprocess.run([*command, *args], env=env, cwd=ROOT, check=check)

        try:
            docker("network", "create", PROJECT)
            docker(
                "run",
                "-d",
                "--name",
                POSTGRES,
                "--network",
                PROJECT,
                "-e",
                "POSTGRES_DB=fuellayer_ci",
                "-e",
                "POSTGRES_USER=fuellayer_ci",
                "-e",
                "POSTGRES_PASSWORD=ci-only",
                "postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
            )
            for _ in range(60):
                result = subprocess.run(
                    ["docker", "exec", POSTGRES, "pg_isready", "-U", "fuellayer_ci"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Disposable PostgreSQL did not start")
            compose("up", "--build", "--wait", "--wait-timeout", "180", "api", "worker")
            compose(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "ENVIRONMENT=test",
                "migrate",
                "python",
                "scripts/check_ci_database.py",
            )
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "\n".join(
                    [
                        "import json, os, urllib.request",
                        "assert os.getuid() == 10001",
                        "base = 'http://127.0.0.1:8000/api/v1'",
                        "ready = json.load(urllib.request.urlopen(base + '/ready'))",
                        "assert ready['status'] == 'ready'",
                        "assert ready['revision'] == os.environ['RELEASE_SHA']",
                        "url = base + '/foods/search?q=riz&limit=1'",
                        "foods = json.load(urllib.request.urlopen(url))",
                        "assert len(foods['items']) == 1",
                        "print('Container: user, revision, catalogue and migrations passed.')",
                    ]
                ),
            )
            # Give the worker several polling cycles to expose import/startup failures.
            time.sleep(5)
            worker = subprocess.check_output(
                [*command, "ps", "-q", "worker"], env=env, text=True, cwd=ROOT
            ).strip()
            assert worker, "Worker is not running"
            state = json.loads(docker("inspect", worker, capture=True))[0]
            assert state["State"]["Running"] and state["RestartCount"] == 0
            docker("stop", POSTGRES)
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "\n".join(
                    [
                        "import urllib.request, urllib.error",
                        "base = 'http://127.0.0.1:8000/api/v1'",
                        "assert urllib.request.urlopen(base + '/health', timeout=10).status == 200",
                        "try:",
                        "    urllib.request.urlopen(base + '/ready', timeout=10)",
                        "except urllib.error.HTTPError as error:",
                        "    assert error.code == 503",
                        "else:",
                        "    raise AssertionError('Readiness accepted an unavailable database')",
                        "print('Container: database outage correctly makes readiness fail.')",
                    ]
                ),
            )
        except Exception:
            compose("logs", "--tail", "100", check=False)
            raise
        finally:
            compose("down", "--volumes", "--remove-orphans", check=False)
            docker("rm", "-f", "-v", POSTGRES, check=False)
            docker("network", "rm", PROJECT, check=False)


if __name__ == "__main__":
    main()
