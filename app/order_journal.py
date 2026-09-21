"""Durable at-most-once submission. Unknown outcomes are never submitted again."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def state_path(name):
    root = Path(os.getenv('TRADING_STATE_DIR', '/config' if Path('/config').is_dir()
                          else str(Path.home() / '.local/state/metatraderm')))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / name


class OrderJournal:
    def __init__(self, path=None):
        self.path = path

    @contextmanager
    def _connect(self):
        path = self.path or state_path('orders.sqlite3')
        db = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT, created REAL NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def run(self, request_id, payload, send):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint, result FROM orders WHERE id=?', (request_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    return {'success': False, 'error': 'Aynı emir kimliği farklı işlem için kullanılamaz.', 'conflict': True}
                result = json.loads(row[1]) if row[1] else {
                    'success': False, 'uncertain': True,
                    'error': 'Emir işleniyor veya sonucu belirsiz; broker durumunu kontrol edin.'}
                return {**result, 'replayed': True, 'request_id': request_id}
            db.execute('INSERT INTO orders VALUES (?, ?, NULL, ?)', (request_id, fingerprint, time.time()))
        started = time.perf_counter()
        try:
            result = send()
        except Exception:
            result = {'success': False, 'uncertain': True,
                      'error': 'Emir sonucu doğrulanamadı; aynı emir yeniden gönderilmeyecek.'}
        result = {**result, 'request_id': request_id,
                  'execution_ms': round((time.perf_counter() - started) * 1000, 1)}
        # If saving fails, the committed pending record still prevents a second send.
        with self._connect() as db:
            db.execute('UPDATE orders SET result=? WHERE id=?', (json.dumps(result, allow_nan=False), request_id))
        return result
