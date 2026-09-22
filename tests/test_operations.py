import json
import sqlite3
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from app.order_journal import OrderJournal
from app.mt5_bridge import reconcile_open
from app.maintenance import create_backup, restore_backup, verify_backup


def broker():
    mt = Mock()
    mt.account_info.return_value = NS(login=7, server='demo')
    mt.orders_get.return_value = []
    mt.history_orders_get.return_value = [NS(ticket=42, comment='vm:unique', symbol='EURUSD', magic=123460,
        type=0, time_setup=time.time(), volume_initial=.02, volume_current=0, state=4)]
    meta = {'account': [7, 'demo'], 'tag': 'vm:unique', 'order': {'symbol': 'EURUSD', 'magic': 123460, 'type': 0, 'volume': .02}}
    return mt, meta


def test_lost_response_restart_reconcile_never_resends(tmp_path):
    path = tmp_path/'orders.sqlite3'
    journal = OrderJournal(path)
    mt, meta = broker()
    calls = []
    def send():
        calls.append(1)  # broker accepted before the connection dropped
        raise ConnectionError('response lost')
    assert journal.run('id', {'lot': .02}, send, metadata=meta)['uncertain']
    restarted = OrderJournal(path)
    assert restarted.run('id', {'lot': .02}, send)['uncertain']
    result = reconcile_open(mt, meta, time.time()-1)
    restarted.resolve('id', result)
    replay = restarted.run('id', {'lot': .02}, send)
    assert replay['success'] and replay['reconciled'] and replay['ticket'] == 42
    assert len(calls) == 1
    mt.order_send.assert_not_called()


@pytest.mark.parametrize('field,value', [('comment','broker rewrote comment'), ('symbol','GBPUSD'), ('magic',1), ('type',1), ('volume_initial',.03)])
def test_ambiguous_evidence_is_not_a_match(field, value):
    mt, meta = broker()
    setattr(mt.history_orders_get.return_value[0], field, value)
    assert reconcile_open(mt, meta, time.time()) is None


def test_empty_missing_duplicate_and_other_account_are_not_evidence():
    mt, meta = broker()
    row = mt.history_orders_get.return_value[0]
    for rows in [[], None, [row, NS(**{**vars(row), 'ticket':43})]]:
        mt.history_orders_get.return_value = rows
        assert reconcile_open(mt, meta, time.time()) is None
    mt.account_info.return_value.server = 'other'
    assert reconcile_open(mt, meta, time.time()) is None


@pytest.mark.parametrize('state,remaining,success,pending,partial', [(1,.02,True,True,False),(3,.01,True,True,True),(2,.01,True,False,True),(2,.02,False,False,False),(5,.02,False,False,False),(6,.02,False,False,False)])
def test_broker_states(state,remaining,success,pending,partial):
    mt, meta = broker()
    row=mt.history_orders_get.return_value[0]; row.state=state; row.volume_current=remaining
    r=reconcile_open(mt,meta,time.time())
    assert (r['success'],r['pending'],r['partial']) == (success,pending,partial)


def test_backup_restore_preserves_at_most_once(tmp_path):
    root=tmp_path/'state';root.mkdir()
    journal=OrderJournal(root/'orders.sqlite3')
    journal.run('sent', {}, lambda: {'success':True,'ticket':4})
    (root/'credentials.json').write_text('{"login":7}')
    backup=create_backup(root,tmp_path/'backups')
    verify_backup(backup)
    restored=restore_backup(backup,tmp_path/'restored')
    replay=OrderJournal(restored/'orders.sqlite3').run('sent',{},lambda:pytest.fail('resent'))
    assert replay['ticket']==4
    assert (restored/'credentials.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):restore_backup(backup,restored)
    (backup/'credentials.json').write_text('{}')
    with pytest.raises(ValueError):verify_backup(backup)


def test_latency_excludes_replays(tmp_path):
    journal=OrderJournal(tmp_path/'j.db')
    for i in range(10):journal.run(str(i),{},lambda:{'success':True},queue_ms=i)
    journal.run('0',{},lambda:pytest.fail('resent'))
    stats=journal.latency()
    assert stats['execution_ms']['count']==10
    assert stats['queue_ms']['p95']==9


def test_old_journal_schema_and_crash_recovery(tmp_path):
    p=tmp_path/'j.db'
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE orders (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT,created REAL NOT NULL)')
    j=OrderJournal(p)
    with pytest.raises(KeyboardInterrupt):j.run('crash',{},lambda:(_ for _ in ()).throw(KeyboardInterrupt()),metadata={'tag':'x'})
    assert OrderJournal(p).run('crash',{},lambda:pytest.fail('resent'))['uncertain']
    with sqlite3.connect(p) as db:db.execute('UPDATE orders SET created=0')
    assert j.unresolved()[0]['id']=='crash'


def test_reconciliation_queue_does_not_starve_old_requests(tmp_path):
    j=OrderJournal(tmp_path/'j.db')
    for i in range(25):j.run(str(i),{},lambda:{'uncertain':True},metadata={'tag':str(i)})
    with sqlite3.connect(j.path) as db:db.execute('UPDATE orders SET created=0')
    first={r['id'] for r in j.unresolved()}
    second={r['id'] for r in j.unresolved()}
    assert len(first|second)==25


def test_backup_retention_and_replay_of_unresolved(tmp_path):
    root=tmp_path/'state';root.mkdir()
    j=OrderJournal(root/'orders.sqlite3')
    j.run('lost',{},lambda:{'success':False,'uncertain':True})
    for _ in range(3):create_backup(root,tmp_path/'backups',keep=2)
    snapshots=list((tmp_path/'backups').glob('backup-*'))
    assert len(snapshots)==2
    recovered=restore_backup(snapshots[0],tmp_path/'recovered')
    assert OrderJournal(recovered/'orders.sqlite3').run('lost',{},lambda:pytest.fail('resent'))['uncertain']


def test_actual_rpc_disconnect_then_new_connection(tmp_path):
    """A real TCP/RPyC response loss, with a broker stub (no trading account)."""
    import threading
    import rpyc
    from rpyc.utils.server import ThreadedServer
    accepted=[]
    class BrokerService(rpyc.Service):
        def exposed_submit(self):
            accepted.append(42)
            self._conn.close()
        def exposed_history(self):
            return len(accepted)
    server=ThreadedServer(BrokerService,hostname='127.0.0.1',port=0,auto_register=False)
    thread=threading.Thread(target=server.start,daemon=True);thread.start()
    c=None
    try:
        c=rpyc.connect('127.0.0.1',server.port,config={'sync_request_timeout':2})
        j=OrderJournal(tmp_path/'j.db')
        assert j.run('rpc-lost',{},lambda:c.root.submit())['uncertain']
        c=rpyc.connect('127.0.0.1',server.port,config={'sync_request_timeout':2})
        assert c.root.history()==1
        assert OrderJournal(j.path).run('rpc-lost',{},lambda:c.root.submit())['uncertain']
        assert c.root.history()==1
    finally:
        if c:c.close()
        server.close();thread.join(2)
