"""HMS tenant provisioning helpers.

Responsible for:
1. Configuring HMS addons_path and volume mounts for tenant containers.
2. Installing HMS modules post-provision via a one-off Odoo container.

Fail-closed: if HMS modules are not available on the host, returns no custom
config and the tenant falls back to generic Odoo.
"""

from __future__ import annotations

import logging
from pathlib import Path

import docker

from app.config import get_settings

logger = logging.getLogger(__name__)

# HMS module technical names (installation order matters)
HMS_MODULES = ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix"]

# Container path where HMS modules are mounted
HMS_CONTAINER_MOUNT = "/mnt/hms-addons"


def prepare_hms_tenant_config(settings, sol) -> dict:
    """Prepare addons_path and extra volumes for HMS tenants.

    Returns dict with:
      - addons_path: string including HMS container mount
      - extra_volumes: dict of additional Docker volume binds

    Returns empty dict if HMS modules are not available.
    """
    host_path = getattr(settings, "hms_modules_host_path", "") or ""
    if not host_path:
        logger.info("HMS modules host path not configured; falling back to generic tenant")
        return {}

    host_path_obj = Path(host_path)
    if not host_path_obj.is_dir():
        logger.warning("HMS modules host path does not exist: %s", host_path)
        return {}

    # Verify HMS modules are present
    missing = [m for m in HMS_MODULES if not (host_path_obj / m).is_dir()]
    if missing:
        logger.warning("HMS modules missing from host path %s: %s", host_path, missing)
        return {}

    addons_path = f"/usr/lib/python3/dist-packages/odoo/addons,{HMS_CONTAINER_MOUNT}"
    extra_volumes = {
        host_path: {"bind": HMS_CONTAINER_MOUNT, "mode": "ro"},
    }

    logger.info("HMS tenant config prepared: addons_path=%s, host=%s", addons_path, host_path)
    return {
        "addons_path": addons_path,
        "extra_volumes": extra_volumes,
    }


def install_hms_modules_post_provision(
    *,
    db_name: str,
    modules: list[str],
    container_name: str,
    http_port: int,
) -> bool:
    """Install HMS modules via a one-off Odoo container.

    Uses the same DB, same filestore as the tenant container.
    Runs `odoo -d <db> --db_user=<u> --db_password=<p> --addons-path=<path> -i <modules> --stop-after-init --without-demo=all`.

    Returns True on success, False on failure.
    """
    settings = get_settings()
    mod_list = ",".join(modules)
    install_name = f"{container_name}-hms-install"[:128]

    # Read credentials from the tenant's runtime conf
    tenant_code = _tenant_code_from_db(db_name)
    runtime_host = Path(settings.tenant_host_root) / tenant_code / "runtime"
    filestore_host = Path(settings.tenant_host_root) / tenant_code / "filestore"
    conf_path = runtime_host / "odoo.conf"

    # Parse credentials from conf
    db_user = None
    db_password = None
    try:
        conf_text = conf_path.read_text()
        for line in conf_text.splitlines():
            line = line.strip()
            if line.startswith("db_user") and "=" in line:
                db_user = line.split("=", 1)[1].strip()
            elif line.startswith("db_password") and "=" in line:
                db_password = line.split("=", 1)[1].strip()
    except Exception as exc:
        logger.error("Failed to read tenant conf for %s: %s", db_name, exc)
        return False

    if not db_user or not db_password:
        logger.error("Missing db_user or db_password in conf for %s", db_name)
        return False

    # Use addons_path that includes HMS mount
    addons_path = f"/usr/lib/python3/dist-packages/odoo/addons,{HMS_CONTAINER_MOUNT}"

    logger.info("Installing HMS modules for %s: %s", db_name, mod_list)

    client = docker.from_env()
    try:
        # Remove any previous install container
        try:
            old = client.containers.get(install_name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        cmd = [
            "--db_host", settings.build_postgres_host,
            "--db_port", str(settings.build_postgres_port),
            "--db_user", db_user,
            "--db_password", db_password,
            "--database", db_name,
            "--addons-path", addons_path,
            "--http-interface", "0.0.0.0",
            "--http-port", "8069",
            "--proxy-mode",
            "-i", mod_list,
            "--stop-after-init",
            "--without-demo=all",
        ]

        # Mount HMS modules path from host settings
        hms_host_path = getattr(settings, "hms_modules_host_path", "") or ""
        volumes = {
            str(filestore_host): {"bind": "/var/lib/odoo", "mode": "rw"},
        }
        if hms_host_path and Path(hms_host_path).is_dir():
            volumes[hms_host_path] = {"bind": HMS_CONTAINER_MOUNT, "mode": "ro"}

        container = client.containers.run(
            image=settings.odoo19_image,
            name=install_name,
            command=cmd,
            detach=False,
            network=settings.build_docker_network,
            remove=True,
            environment={
                "HOST": settings.build_postgres_host,
            },
            volumes=volumes,
            labels={
                "mock_odoo_sh": "true",
                "mosh_hms_install": "true",
                "tenant_container": container_name,
            },
            mem_limit=1536 * 1024 * 1024,
        )
        logger.info("HMS modules installed successfully for %s", db_name)
        return True

    except docker.errors.ContainerError as exc:
        logger.error("HMS module install container failed for %s: %s", db_name, exc)
        return False
    except docker.errors.ImageNotFound:
        logger.error("Odoo image not found for HMS module install: %s", settings.odoo19_image)
        return False
    except docker.errors.APIError as exc:
        logger.error("Docker API error during HMS module install for %s: %s", db_name, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error during HMS module install for %s: %s", db_name, exc)
        return False


def _tenant_code_from_db(db_name: str) -> str:
    """Extract tenant code from database name (mosh_tnt_<code>)."""
    prefix = "mosh_tnt_"
    if db_name.startswith(prefix):
        return db_name[len(prefix):]
    return db_name


def verify_hms_modules_installed(db_name: str) -> bool:
    """Check if HMS modules are installed in the database."""
    import psycopg2

    settings = get_settings()
    try:
        conn = psycopg2.connect(
            host=settings.build_postgres_host,
            port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user,
            password=settings.build_postgres_admin_password,
            dbname=db_name,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM ir_module_module WHERE name = ANY(%s) AND state = 'installed'",
                    [HMS_MODULES],
                )
                installed = {row[0] for row in cur.fetchall()}
                return all(m in installed for m in HMS_MODULES)
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to verify HMS modules for %s: %s", db_name, exc)
        return False
