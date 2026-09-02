from __future__ import annotations

import logging
import time
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from app.config import get_settings, odoo_image_for_version

logger = logging.getLogger(__name__)

LABEL_MOSH = "mock_odoo_sh"
LABEL_PROJECT = "project_id"
LABEL_BUILD = "build_id"


class DockerServiceError(Exception):
    pass


def get_client() -> docker.DockerClient:
    return docker.from_env()


def ensure_image(image: str) -> None:
    client = get_client()
    try:
        client.images.get(image)
    except ImageNotFound:
        logger.info("Pulling image %s", image)
        client.images.pull(image)
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def find_mosh_container(name: str) -> Container | None:
    client = get_client()
    try:
        c = client.containers.get(name)
    except NotFound:
        return None
    labels = c.labels or {}
    if labels.get(LABEL_MOSH) != "true":
        raise DockerServiceError(f"Refusing to touch unrelated container: {name}")
    return c


def stop_build_container(name: str | None) -> None:
    if not name:
        return
    c = find_mosh_container(name)
    if not c:
        return
    try:
        if c.status == "running":
            c.stop(timeout=20)
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def remove_build_container(name: str | None, force: bool = True) -> None:
    if not name:
        return
    c = find_mosh_container(name)
    if not c:
        return
    try:
        c.remove(force=force)
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def list_mosh_containers() -> list[Container]:
    client = get_client()
    return client.containers.list(all=True, filters={"label": f"{LABEL_MOSH}=true"})


def _parse_memory(value: str) -> int:
    v = (value or "1536m").strip().lower()
    if v.endswith("g"):
        return int(float(v[:-1]) * 1024**3)
    if v.endswith("m"):
        return int(float(v[:-1]) * 1024**2)
    return int(v)


def write_odoo_conf_file(
    conf_path,
    *,
    db_name: str,
    db_user: str,
    db_password: str,
    admin_passwd: str,
    addons_path: str = "/usr/lib/python3/dist-packages/odoo/addons",
    data_dir: str | None = None,
) -> None:
    """Write Odoo 19-compatible runtime config (CLI --admin-passwd is not supported)."""
    from pathlib import Path

    settings = get_settings()
    path = Path(conf_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[options]",
        f"admin_passwd = {admin_passwd}",
        "list_db = False",
        f"db_host = {settings.build_postgres_host}",
        f"db_port = {settings.build_postgres_port}",
        f"db_user = {db_user}",
        f"db_password = {db_password}",
        f"db_name = {db_name}",
        f"addons_path = {addons_path}",
        "http_interface = 0.0.0.0",
        "http_port = 8069",
        "proxy_mode = True",
        "without_demo = True",
    ]
    if data_dir:
        lines.append(f"data_dir = {data_dir}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_odoo_container(
    *,
    name: str,
    odoo_version: str,
    http_port: int,
    db_name: str,
    workspace_repo: str,
    addons_path: str,
    project_id: int,
    build_id: int,
    init_modules: str | None = None,
    stop_after_init: bool = False,
    admin_passwd: str,
) -> Container:
    """
    Option B: mount checked-out customer repo into official Odoo Community image.
    FIRST_REAL_ODOO_BUILD_ENGINE = Community runtime.
    """
    settings = get_settings()
    image = odoo_image_for_version(odoo_version)
    ensure_image(image)

    # Remove any previous same-name mosh container
    remove_build_container(name, force=True)

    cmd = [
        "--db_host",
        settings.build_postgres_host,
        "--db_port",
        str(settings.build_postgres_port),
        "--db_user",
        settings.build_postgres_user,
        "--db_password",
        settings.build_postgres_password,
        "--database",
        db_name,
        "--addons-path",
        addons_path,
        "--http-interface",
        "0.0.0.0",
        "--http-port",
        "8069",
        "--proxy-mode",
        "--without-demo=all",
        f"--admin-passwd={admin_passwd}",
    ]
    # Disable db manager listing
    cmd += ["--list-db=False"] if False else []  # Odoo uses config; use env below

    if init_modules:
        cmd += ["-i", init_modules]
    if stop_after_init:
        cmd += ["--stop-after-init"]

    host_config: dict[str, Any] = {
        "mem_limit": _parse_memory(settings.build_container_memory),
        "nano_cpus": int(settings.build_container_nano_cpus),
        "restart_policy": {"Name": "no"},
        "port_bindings": {"8069/tcp": ("127.0.0.1", int(http_port))},
        "binds": [f"{workspace_repo}:/mnt/extra-addons:ro"],
    }

    client = get_client()
    try:
        container = client.containers.run(
            image=image,
            name=name,
            command=cmd,
            detach=True,
            network=settings.build_docker_network,
            environment={
                "HOST": settings.build_postgres_host,
                "USER": settings.build_postgres_user,
                "PASSWORD": settings.build_postgres_password,
                "ODOO_RC": "/etc/odoo/odoo.conf",
            },
            labels={
                LABEL_MOSH: "true",
                LABEL_PROJECT: str(project_id),
                LABEL_BUILD: str(build_id),
                "mosh_db": db_name,
            },
            ports={"8069/tcp": ("127.0.0.1", int(http_port))},
            volumes={workspace_repo: {"bind": "/mnt/extra-addons", "mode": "ro"}},
            mem_limit=_parse_memory(settings.build_container_memory),
            nano_cpus=int(settings.build_container_nano_cpus),
            restart_policy={"Name": "no"},
            # Never privileged / never mount docker.sock
            privileged=False,
        )
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc
    return container


def container_logs(name: str | None, tail: int = 200) -> str:
    if not name:
        return ""
    c = find_mosh_container(name)
    if not c:
        return ""
    try:
        raw = c.logs(tail=tail)
        return raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except DockerException:
        return ""


def wait_http_ok(url: str, timeout_sec: int = 180) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
                code = getattr(resp, "status", 200)
                # Odoo may return 303 to login or 500 while warming up; any HTTP response means the server is up.
                if 200 <= code < 600:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code and 200 <= exc.code < 600:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(3)
    return False


def wait_odoo_healthy(*, container_name: str, http_port: int, timeout_sec: int = 180) -> bool:
    """Prefer in-network container URL; fall back to host-published port."""
    candidates = [
        f"http://{container_name}:8069/web/login",
        f"http://127.0.0.1:{http_port}/web/login",
    ]
    # Also try Docker bridge gateway for published ports when checking from a container.
    try:
        import socket

        gw = socket.gethostbyname("host.docker.internal")
        candidates.append(f"http://{gw}:{http_port}/web/login")
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for url in candidates:
            if wait_http_ok(url, timeout_sec=2):
                return True
        time.sleep(2)
    return False


def install_requirements_if_present(repo_path: str, odoo_version: str, log_append) -> None:
    """Install requirements.txt into a throwaway container (does not mutate control-api env)."""
    from pathlib import Path

    req = Path(repo_path) / "requirements.txt"
    if not req.exists():
        log_append("No requirements.txt — skipping dependency install")
        return
    image = odoo_image_for_version(odoo_version)
    ensure_image(image)
    client = get_client()
    log_append("Installing requirements.txt inside ephemeral container")
    try:
        # Install into the mounted repo's .mosh_pip (writable) then PYTHONPATH at runtime if needed.
        # For Community image, site-packages installs as root in ephemeral container targeting a volume.
        output = client.containers.run(
            image=image,
            entrypoint="bash",
            command=[
                "-lc",
                "pip3 install --break-system-packages -r /mnt/extra-addons/requirements.txt "
                "|| pip3 install -r /mnt/extra-addons/requirements.txt",
            ],
            volumes={repo_path: {"bind": "/mnt/extra-addons", "mode": "rw"}},
            network=get_settings().build_docker_network,
            remove=True,
            labels={LABEL_MOSH: "true", "mosh_ephemeral": "true"},
        )
        text = output.decode("utf-8", errors="replace") if isinstance(output, (bytes, bytearray)) else str(output)
        log_append(text)
    except DockerException as exc:
        raise DockerServiceError(f"requirements install failed: {exc}") from exc
