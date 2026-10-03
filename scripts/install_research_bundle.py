"""Install verified research source into a server checkout; preserves Docker config.

Run from an extracted bundle: python3 install_research_bundle.py --project-root PATH
Then rebuild only web-dashboard using the server's existing Compose configuration.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def install(bundle, project):
    bundle, project = Path(bundle).resolve(), Path(project).resolve()
    if not (project / 'docker-compose.yml').is_file():
        raise ValueError('Hedef bir MetaTraderM Compose kaynak dizini olmalı.')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    work = []
    # Verify EVERY file before changing any; divergent production code needs a merge.
    for item in manifest['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'app':
            raise ValueError('Geçersiz paket yolu.')
        source, target = bundle / relative, project / relative
        if target.resolve().is_relative_to(project) is False:
            raise ValueError('Hedef paket dizini dışında.')
        if sha(source) != item['new_sha256']:
            raise ValueError('Paket özeti uyuşmuyor: ' + str(relative))
        current = sha(target)
        if current == item['new_sha256']:
            continue
        if current != item['base_sha256']:
            raise ValueError('Sunucu kaynağı farklı; önce birleştirme gerekli: ' + str(relative))
        work.append((relative, source, target))
    if not work:
        return None
    backup = project / ('research-source-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    try:
        for relative, source, target in work:
            if target.exists():
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, old)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except Exception:
        # Undo this source installation if any copy fails.
        for relative, source, target in work:
            old = backup / relative
            if old.exists():
                shutil.copy2(old, target)
            elif sha(target) == sha(source):
                target.unlink()
        raise
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    args = parser.parse_args()
    try:
        backup = install(Path(__file__).parent, args.project_root)
        print('Kaynak kuruldu; yedek: ' + str(backup) if backup else 'Bu kaynak sürümü zaten kurulu.')
        print('Mevcut Compose ayarlarıyla yalnız web-dashboard hizmetini yeniden oluşturun.')
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
