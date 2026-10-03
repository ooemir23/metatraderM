import hashlib
import json
from pathlib import Path

import pytest
from scripts.install_research_bundle import install


def make_bundle(tmp_path):
    bundle, target = tmp_path / 'bundle', tmp_path / 'project'
    (bundle / 'app').mkdir(parents=True)
    (target / 'app').mkdir(parents=True)
    (target / 'docker-compose.yml').write_text('existing production settings')
    (target / 'app/main.py').write_text('old source')
    files = []
    for name, old, new in [('main.py', b'old source', b'updated source'),
                           ('strategy_research.py', None, b'research code')]:
        (bundle / 'app' / name).write_bytes(new)
        files.append({'path': 'app/' + name,
                      'base_sha256': hashlib.sha256(old).hexdigest() if old else None,
                      'new_sha256': hashlib.sha256(new).hexdigest()})
    (bundle / 'manifest.json').write_text(json.dumps({'files': files}))
    return bundle, target


def test_installer_checks_all_files_before_mutation(tmp_path):
    bundle, target = make_bundle(tmp_path)
    (target / 'app/strategy_research.py').write_text('independent production edit')
    with pytest.raises(ValueError, match='farklı'):
        install(bundle, target)
    assert (target / 'app/main.py').read_text() == 'old source'
    assert not list(target.glob('research-source-backup-*'))


def test_installer_backs_up_source_preserves_config_and_is_idempotent(tmp_path):
    bundle, target = make_bundle(tmp_path)
    backup = install(bundle, target)
    assert (backup / 'app/main.py').read_text() == 'old source'
    assert (target / 'app/main.py').read_text() == 'updated source'
    assert (target / 'docker-compose.yml').read_text() == 'existing production settings'
    assert install(bundle, target) is None


def test_tampered_bundle_is_rejected(tmp_path):
    bundle, target = make_bundle(tmp_path)
    (bundle / 'app/main.py').write_text('tampered source')
    with pytest.raises(ValueError, match='özeti'):
        install(bundle, target)
    assert (target / 'app/main.py').read_text() == 'old source'
