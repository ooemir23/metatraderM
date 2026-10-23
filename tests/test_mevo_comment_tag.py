from test_trade_safety import client


def _sent_comment(c):
    return c.mt5.order_send.call_args[0][0]['comment']


def test_mevo_page_order_carries_mevo_comment(client):
    assert client.open_order('EURUSD', 'BUY', .01, comment='MevO', request_id='mevo-1')['success']
    assert _sent_comment(client).startswith('mevo:') and len(_sent_comment(client)) <= 31


def test_plain_web_order_keeps_vm_comment(client):
    assert client.open_order('EURUSD', 'BUY', .01, request_id='web-1')['success']
    assert _sent_comment(client).startswith('vm:')
