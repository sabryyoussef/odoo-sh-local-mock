"""Review-only HC3.6 bootstrap. --apply-approved requires separate operator approval."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3

ROOT = Path('/opt/projects/active/odoo-sh-local-mock')
BUNDLE = ROOT / 'docs/hc36-preparation'
TARGET = ROOT / 'data-hc36/control.db'
SCHEMA_SHA256 = 'b5ecbfe5db49930fd536464cabe9d53aa80d1f82bf22b7511cce16289d0b9312'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def forbidden(*args, **kwargs):
    raise RuntimeError('Network access prohibited in local schema preparation')


def inputs():
    require(ROOT.resolve() == ROOT, 'Repository path changed or contains symlinks')
    manifest = json.loads((BUNDLE / 'manifest.json').read_text())
    sql = (BUNDLE / 'schema.sql').read_bytes()
    require(hashlib.sha256(sql).hexdigest() == SCHEMA_SHA256 == manifest['schema_sha256'],
            'Reviewed schema hash mismatch')
    require(hashlib.sha256((ROOT / 'control-api/app/models.py').read_bytes()).hexdigest()
            == manifest['models_sha256'], 'Model changed: regenerate and review the plan')
    require(manifest['database_path'] == str(TARGET), 'Unexpected target')
    require(os.environ.get('APP_ENV') == manifest['app_env'] == 'test', 'APP_ENV must be test')
    require(os.environ.get('APP_NAME') == manifest['app_identity'] == 'hc3-6-lab',
            'APP_NAME must be hc3-6-lab')
    require(os.environ.get('DATABASE_URL') == 'sqlite:///' + str(TARGET), 'Unexpected DATABASE_URL')
    return manifest, sql.decode('utf-8')


def verify(manifest):
    require(TARGET.is_file() and not TARGET.is_symlink(), 'Expected regular database missing')
    require(TARGET.parent.resolve() == TARGET.parent, 'Target directory is a symlink')
    require(TARGET.stat().st_mode & 0o777 == 0o600, 'Database mode must be 0600')
    require(TARGET.parent.stat().st_mode & 0o777 == 0o700, 'Directory mode must be 0700')
    for suffix in ('-wal', '-shm', '-journal'):
        require(not Path(str(TARGET) + suffix).exists(), 'Unexpected SQLite sidecar: stop for review')
    with closing(sqlite3.connect(TARGET.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        require(connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'Integrity failure')
        actual = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(actual == set(manifest['tables']), 'Unexpected table set')
        for table, columns in manifest['tables'].items():
            require(table.replace('_', '').isalnum(), 'Invalid table name')
            actual_columns = [r[1] for r in connection.execute('PRAGMA table_info("' + table + '")')]
            require(actual_columns == columns, 'Schema mismatch: ' + table)
            require(connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] == 0,
                    'Nonempty table: ' + table)
        indexes = {r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        require(indexes == set(manifest['indexes']), 'Index set mismatch')
        require(connection.execute('PRAGMA foreign_key_check').fetchall() == [], 'Foreign-key failure')
        require(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0] == 0,
                'Unexpected trigger/view')
        connection.rollback()
    print('HC3_6_LOCAL_SCHEMA_PREPARED: seven tables, all empty; no execution readiness claimed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check-plan', 'apply-approved', 'verify'))
    args = parser.parse_args()
    socket.socket.connect = forbidden
    socket.create_connection = forbidden
    manifest, sql = inputs()
    if args.action == 'verify':
        verify(manifest)
        return
    require(not os.path.lexists(TARGET.parent), 'Target directory already exists: no overwrite or automatic reuse')
    if args.action == 'check-plan':
        print('PLAN_VALIDATED_ONLY: target absent; reviewed hashes match; no database opened or created')
        return
    os.umask(0o077)
    TARGET.parent.mkdir(mode=0o700)  # exclusive: fail if it appeared after the check
    descriptor = os.open(TARGET, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(TARGET.as_uri() + '?mode=rw', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript('BEGIN IMMEDIATE;\n' + sql + '\nCOMMIT;')
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    verify(manifest)


if __name__ == '__main__':
    main()
