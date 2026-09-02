"""Docker runtime for isolated tenant Odoo instances."""

from __future__ import annotations

import logging
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound

from app.config import get_settings, odoo_image_for_version
from app.services.docker_service import DockerServiceError, ensure_image, wait_odoo_healthy, write_odoo_conf_file

logger = logging.getLogger(__name__)

LABEL_TENANT = "mosh_tenant"
LABEL_TENANT_ID = "tenant_id"
LABEL_DEPLOYMENT_JOB = "deployment_job_id"
LABEL_PROVISIONING_JOB = "provisioning_job_id"


def find_tenant_container(name: str) -> docker.models.containers.Container | None:
    client = docker.from_env()
    try:
        c = client.containers.get(name)
    except NotFound:
        return None
    labels = c.labels or {}
    if labels.get("mock_odoo_sh") != "true" or labels.get(LABEL_TENANT) != "true":
        raise DockerServiceError(f"Refusing to touch unrelated container: {name}")
    return c


def remove_tenant_container(name: str | None) -> None:
    if not name:
        return
    c = find_tenant_container(name)
    if not c:
        return
    try:
        if c.status == "running":
            c.stop(timeout=20)
        c.remove(force=True)
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def run_tenant_odoo_container(
    *,
    name: str,
    tenant_id: int,
    provisioning_job_id: int = 0,
    deployment_job_id: int | None = None,
    odoo_version: str,
    http_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    filestore_container_path: str,
    filestore_host_path: str,
    admin_passwd: str,
) -> docker.models.containers.Container:
    settings = get_settings()
    image = odoo_image_for_version(odoo_version)
    ensure_image(image)
    remove_tenant_container(name)

    runtime_container = Path(filestore_container_path).parent / "runtime"
    runtime_host = Path(filestore_host_path).parent / "runtime"
    runtime_container.mkdir(parents=True, exist_ok=True)
    write_odoo_conf_file(
        runtime_container / "odoo.conf",
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        admin_passwd=admin_passwd,
        data_dir="/var/lib/odoo",
    )

    cmd = ["-c", "/mnt/runtime/odoo.conf"]

    client = docker.from_env()
    try:
        container = client.containers.run(
            image=image,
            name=name,
            command=cmd,
            detach=True,
            network=settings.build_docker_network,
            environment={
                "HOST": settings.build_postgres_host,
                "USER": db_user,
                "PASSWORD": db_password,
            },
            labels={
                "mock_odoo_sh": "true",
                LABEL_TENANT: "true",
                LABEL_TENANT_ID: str(tenant_id),
                LABEL_PROVISIONING_JOB: str(provisioning_job_id),
                LABEL_DEPLOYMENT_JOB: str(deployment_job_id or ""),
                "mosh_db": db_name,
            },
            ports={"8069/tcp": ("127.0.0.1", int(http_port))},
            volumes={
                filestore_host_path: {"bind": "/var/lib/odoo", "mode": "rw"},
                str(runtime_host): {"bind": "/mnt/runtime", "mode": "ro"},
            },
            mem_limit=1536 * 1024 * 1024,
            nano_cpus=int(settings.build_container_nano_cpus),
            restart_policy={"Name": "no"},
            privileged=False,
        )
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc
    return container


def wait_tenant_healthy(container_name: str, http_port: int, timeout_sec: int = 180) -> bool:
    return wait_odoo_healthy(container_name=container_name, http_port=http_port, timeout_sec=timeout_sec)


def stop_tenant_container(name: str | None) -> None:
    """Stop runtime in place. Does not remove the container, database, role, or filestore."""
    if not name:
        return
    c = find_tenant_container(name)
    if not c:
        return
    try:
        c.reload()
        if c.status == "running":
            c.stop(timeout=20)
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def start_existing_tenant_container(name: str | None) -> None:
    if not name:
        raise DockerServiceError("tenant container name missing")
    c = find_tenant_container(name)
    if not c:
        raise DockerServiceError(f"tenant container not found: {name}")
    try:
        c.reload()
        if c.status != "running":
            c.start()
    except DockerException as exc:
        raise DockerServiceError(str(exc)) from exc


def tenant_container_is_running(name: str | None) -> bool:
    if not name:
        return False
    c = find_tenant_container(name)
    if not c:
        return False
    try:
        c.reload()
    except DockerException:
        return False
    return c.status == "running"
