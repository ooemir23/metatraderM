from test_trade_safety import client


def _sent_comment(c):
    return c.mt5.order_send.call_args[0][0]['comment']


def test_mevo_page_order_carries_mevo_comment(client):
    assert client.open_order('EURUSD', 'BUY', .01, comment='MevO', request_id='mevo-1')['success']
    assert _sent_comment(client).startswith('mevo:') and len(_sent_comment(client)) <= 31


def test_plain_web_order_keeps_vm_comment(client):
    assert client.open_order('EURUSD', 'BUY', .01, request_id='web-1')['success']
    assert _sent_comment(client).startswith('vm:')


def test_bot_mevo_position_ids_survive_history_trim(tmp_path):
    from types import SimpleNamespace as NS
    from app.mevo import MevO
    import inspect
    sig = inspect.signature(MevO.__init__)
    kwargs = {k: None for k in sig.parameters if k != 'self'}
    m = MevO.__new__(MevO)
    import threading
    m._lock = threading.RLock(); m.state = {}; m._path = str(tmp_path / 'm.json')
    m.mt5 = NS(login_id=1, server='s')
    assert m._remember_bot_ids([5, 6]) == [5, 6]
    assert m._remember_bot_ids([7]) == [5, 6, 7]      # older ids stay although the bot history no longer lists them
    assert m._remember_bot_ids([]) == [5, 6, 7]
