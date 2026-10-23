"""Botta "MevO'yu kullan" (2026-10-23, kullanıcı isteği): bot MevO fırsatlarını (DeepSeek, app.mevo) açar.
MT5 EA'dan farklıdır; kullanıcı onayladı. Token maliyeti için: yalnız pozisyon yokken sorulur, mum başına en çok bir kez."""
import pytest

from app.mt5_bridge import HMA_MAGIC
from tests.test_bot_auto_trade_e2e import FakeMT5, POINT, live_bot, market


def run(answer, start=450, end=470, mode='MEVO'):
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    bot.entry_mode = mode
    calls = []

    def provider(sym, tf):
        calls.append(client.now)
        px = rows[client.now]['close']
        a = answer(client.now) if callable(answer) else answer
        if a is None:
            return None
        return {'symbol': sym, 'action': a['action'], 'confidence': a.get('confidence', 80), 'entry': px,
                'sl': px - 200 * POINT if a['action'] == 'BUY' else px + 200 * POINT,
                'tp': px + 500 * POINT if a['action'] == 'BUY' else px - 500 * POINT,
                'warnings': a.get('warnings', []), 'reason': 'test'}
    bot.mevo_provider = provider
    for now in range(start, end):
        client.now = now
        bot._check_strategy_once()
    return client, bot, calls


def test_opens_mevo_opportunity_with_mevo_levels_and_asks_only_while_flat():
    client, _, calls = run({'action': 'BUY'}, start=450, end=470)
    assert len(client.opened) == 1 and client.opened[0]['side'] == 'BUY' and client.opened[0]['magic'] == HMA_MAGIC
    assert (client.opened[0]['sl_points'], client.opened[0]['tp_points']) == (200, 500)
    assert len(calls) == 1                      # pozisyon açıkken MevO'ya (token) sorulmaz


def test_low_confidence_and_loss_warnings_block_entry_but_plain_warnings_do_not():
    assert run({'action': 'BUY', 'confidence': 40})[0].opened == []
    assert run({'action': 'SELL', 'warnings': ['Şu an saat; geçmişte (bot) bu sebeple 2 kez toplam -5 zarar edildi.']})[0].opened == []
    client, _, _ = run({'action': 'SELL', 'warnings': ['Manuel kârlı işlemlerin bu saatte açılmadı.']})
    assert [o['side'] for o in client.opened] == ['SELL']


def test_no_opportunity_means_no_order_and_crossovers_do_not_trade():
    client, _, _ = run(None, start=450, end=650)
    assert client.opened == []


def test_provider_failure_is_retried_and_cooldown_limits_reentries():
    state = {'n': 0}

    def flaky(now):
        state['n'] += 1
        if state['n'] == 1:
            raise RuntimeError('MevO başka bir istek işliyor')
        return {'action': 'BUY'}
    client, _, _ = run(flaky, start=450, end=454)
    assert len(client.opened) == 1              # ilk hata sonraki döngüde tekrar denendi


def test_mevo_entry_rejects_m1_for_token_cost():
    client = FakeMT5(market())
    bot = live_bot(client)
    with pytest.raises(ValueError, match='M5'):
        bot.validated_config({'entry_mode': 'MEVO', 'timeframe_minutes': 1})
