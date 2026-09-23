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
        db.execute('CREATE TABLE IF NOT EXISTS order_metadata (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS order_checks (id TEXT PRIMARY KEY, checked REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS trading_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        try:
            with db:
                yield db
        finally:
            db.close()

    def run(self, request_id, payload, send, *, metadata=None, queue_ms=None):
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
            if metadata:
                db.execute('INSERT INTO order_metadata VALUES (?, ?)', (request_id, json.dumps(metadata, allow_nan=False)))
        started = time.perf_counter()
        try:
            result = send()
        except Exception:
            result = {'success': False, 'uncertain': True,
                      'error': 'Emir sonucu doğrulanamadı; aynı emir yeniden gönderilmeyecek.'}
        result = {**result, 'request_id': request_id,
                  'execution_ms': round((time.perf_counter() - started) * 1000, 1)}
        if queue_ms is not None:
            result['queue_ms'] = queue_ms
        # If saving fails, the committed pending record still prevents a second send.
        with self._connect() as db:
            db.execute('UPDATE orders SET result=? WHERE id=?', (json.dumps(result, allow_nan=False), request_id))
        return result

    def set_trading_halted(self, halted):
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO trading_state VALUES (?, ?)',
                       ('new_orders_halted', '1' if halted else '0'))

    def trading_halted(self):
        with self._connect() as db:
            row = db.execute('SELECT value FROM trading_state WHERE key=?',
                             ('new_orders_halted',)).fetchone()
        return bool(row and row[0] == '1')

    def unresolved(self, limit=20):
        with self._connect() as db:
            rows = db.execute("SELECT o.id, o.created, o.result, m.data FROM orders o JOIN order_metadata m ON m.id=o.id LEFT JOIN order_checks c ON c.id=o.id WHERE o.created<? AND (o.result IS NULL OR json_extract(o.result, '$.uncertain')=1 OR json_extract(o.result, '$.pending')=1) ORDER BY COALESCE(c.checked, 0), o.created LIMIT ?", (time.time()-30, limit)).fetchall()
            for row in rows:
                db.execute('INSERT OR REPLACE INTO order_checks VALUES (?, ?)', (row[0], time.time()))
        return [dict(id=r[0], created=r[1], result=json.loads(r[2]) if r[2] else None, metadata=json.loads(r[3])) for r in rows]

    def resolve(self, request_id, result):
        with self._connect() as db:
            row = db.execute('SELECT result FROM orders WHERE id=?', (request_id,)).fetchone()
            if not row:
                return
            old = json.loads(row[0]) if row[0] else {}
            if row[0] and not (old.get('uncertain') or old.get('pending')):
                return
            merged = {**old, **result, 'request_id': request_id, 'reconciled': True, 'reconciled_at': time.time()}
            db.execute('UPDATE orders SET result=? WHERE id=?', (json.dumps(merged, allow_nan=False), request_id))

    def status(self, request_id):
        with self._connect() as db:
            row = db.execute('SELECT result FROM orders WHERE id=?', (request_id,)).fetchone()
        if not row:
            return {'found': False}
        return {'found': True, **(json.loads(row[0]) if row[0] else {'uncertain': True})}

    def latency(self):
        with self._connect() as db:
            rows = db.execute('SELECT result FROM orders WHERE result IS NOT NULL ORDER BY created DESC LIMIT 500').fetchall()
        results = [json.loads(r[0]) for r in rows]
        def stats(key):
            values = sorted(r[key] for r in results if isinstance(r.get(key), (int, float)))
            if not values:
                return {'count': 0, 'p50': None, 'p95': None, 'max': None}
            import math
            return {'count': len(values), 'p50': values[math.ceil(len(values)*.50)-1],
                    'p95': values[math.ceil(len(values)*.95)-1], 'max': values[-1]}
        return {'sample_limit': 500, 'execution_ms': stats('execution_ms'), 'queue_ms': stats('queue_ms'),
                'scope': 'Sunucu emir işleme süresi; broker gerçekleşme veya ağ gidiş dönüş süresi değildir.'}
