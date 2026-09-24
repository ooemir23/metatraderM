"""Verified state backups; restore only into an empty directory while the app is stopped."""
import argparse
import hashlib
import json
import os
import shutil
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from app.order_journal import state_path

STATE_FILES = ('orders.sqlite3', 'credentials.json', 'ai_memory.json', 'trading_totp_secret')


def verify_backup(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    files = manifest['files']
    if not files or set(files) - set(STATE_FILES):
        raise ValueError('Invalid backup manifest')
    for name, digest in files.items():
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Backup checksum mismatch: ' + name)
    if 'orders.sqlite3' in files:
        with sqlite3.connect(f'file:{directory / "orders.sqlite3"}?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Journal integrity check failed')
    return manifest


def create_backup(root=None, destination=None, keep=14):
    root = Path(root) if root else state_path('orders.sqlite3').parent
    destination = Path(destination or os.getenv('BACKUP_DIR', str(root / 'backups')))
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = Path(tempfile.mkdtemp(prefix='.pending-', dir=destination))
    try:
        files = {}
        for name in STATE_FILES:
            source, target = root / name, temp / name
            if not source.exists():
                continue
            if name.endswith('.sqlite3'):
                with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
                    src.backup(dst)
            else:
                # Config writers use atomic replacement, so each file is complete.
                data = source.read_bytes()
                if name == 'trading_totp_secret':
                    if not re.fullmatch(rb'[A-Z2-7]{32}', data.strip()):
                        raise ValueError('Invalid TOTP secret')
                else:
                    json.loads(data)
                target.write_bytes(data)
            target.chmod(0o600)
            files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        if not files:
            raise ValueError('No state files to back up')
        manifest = {'created': time.time(), 'files': files}
        (temp / 'manifest.json').write_text(json.dumps(manifest))
        (temp / 'manifest.json').chmod(0o600)
        verify_backup(temp)
        final = destination / ('backup-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + temp.name[-6:])
        temp.rename(final)
        for old in sorted(destination.glob('backup-*'), reverse=True)[max(1, keep):]:
            if old.is_dir() and not old.is_symlink():
                verify_backup(old)
                shutil.rmtree(old)
        return final
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def restore_backup(source, destination):
    source, destination = Path(source), Path(destination)
    manifest = verify_backup(source)
    if destination.exists():
        raise ValueError('Restore destination must not exist; never overwrite a live/newer order journal')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.restore-', dir=destination.parent))
    try:
        for name in manifest['files']:
            shutil.copy2(source / name, staging / name)
            (staging / name).chmod(0o600)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'verify', 'restore'])
    parser.add_argument('--source')
    parser.add_argument('--destination')
    args = parser.parse_args()
    if args.action == 'backup':
        print(create_backup(args.source, args.destination))
    elif args.action == 'verify':
        verify_backup(args.source)
        print('Backup verified')
    else:
        print(restore_backup(args.source, args.destination))
