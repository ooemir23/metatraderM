"""Pull a restricted SSH backup, encrypt it locally, and drill a clean restore."""
import argparse
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.maintenance import STATE_FILES, restore_backup, verify_backup


def run(host, key, recipient, destination, gnupg_home, keep=30):
    destination = Path(destination).expanduser()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    final = destination / f'mt5-state-{stamp}.tar.gpg'
    pending = destination / f'.pending-{stamp}.gpg'
    if final.exists() or pending.exists():
        raise FileExistsError(final)
    ssh = subprocess.Popen(['ssh', '-T', '-i', str(key), '-o', 'BatchMode=yes',
                            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
                            f'root@{host}', 'backup'], stdout=subprocess.PIPE)
    try:
        with pending.open('xb') as encrypted:
            os.chmod(pending, 0o600)
            gpg = subprocess.run(['gpg', '--homedir', str(gnupg_home), '--batch', '--yes', '--trust-model', 'always',
                                  '--encrypt', '--recipient', recipient],
                                 stdin=ssh.stdout, stdout=encrypted, check=False)
        ssh.stdout.close()
        if ssh.wait(timeout=180) or gpg.returncode:
            raise RuntimeError('SSH export or encryption failed')
        with tempfile.TemporaryDirectory(prefix='mt5-restore-drill-') as temp:
            temp = Path(temp)
            decrypt = subprocess.Popen(['gpg', '--homedir', str(gnupg_home), '--batch', '--pinentry-mode', 'loopback',
                                       '--passphrase', '', '--decrypt', str(pending)],
                                       stdout=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=decrypt.stdout, mode='r|') as archive:
                    backup = None
                    seen = set()
                    for member in archive:
                        parts = Path(member.name).parts
                        if len(parts) == 1 and parts[0].startswith('backup-') and member.isdir():
                            backup = temp / parts[0]
                            backup.mkdir(mode=0o700)
                            continue
                        if (backup is None or len(parts) != 2 or parts[0] != backup.name or
                                parts[1] not in (*STATE_FILES, 'manifest.json') or
                                not member.isfile() or parts[1] in seen):
                            raise ValueError('Unexpected backup archive member')
                        seen.add(parts[1])
                        with (backup / parts[1]).open('xb') as output:
                            os.chmod(backup / parts[1], 0o600)
                            shutil.copyfileobj(archive.extractfile(member), output)
                decrypt.stdout.close()
                if decrypt.wait(timeout=30):
                    raise RuntimeError('Backup decryption failed')
                if backup is None:
                    raise ValueError('Backup directory missing')
                verify_backup(backup)
                restored = restore_backup(backup, temp / 'restored')
                if set(path.name for path in restored.iterdir()) != set(verify_backup(backup)['files']):
                    raise ValueError('Restore drill did not reproduce manifest files')
            finally:
                if decrypt.poll() is None:
                    decrypt.kill()
                    decrypt.wait()
        pending.rename(final)
        for old in sorted(destination.glob('mt5-state-*.tar.gpg'), reverse=True)[max(1, keep):]:
            old.unlink()
        return final
    finally:
        if ssh.poll() is None:
            ssh.kill()
            ssh.wait()
        pending.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--recipient', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--gnupg-home', required=True)
    args = parser.parse_args()
    print(run(args.host, args.key, args.recipient, args.destination, args.gnupg_home))
