"""Kâr hedefi / zarar sınırı (2026-10-23, kullanıcı isteği; MT5 EA'da yok): bot bu çalıştırmadaki net sonuç
(kapanan bot pozisyonları + açık bot pozisyonlarının anlık K/Z'si) sınıra ulaşınca durur."""
import time
from unittest.mock import Mock

from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot


def make(profit=0, loss=0, closed=0.0, floating=0.0):
    mt5 = Mock(tick_clock_offset=0)
    now = int(time.time())
    # bir kapalı bot pozisyonu: açılış + kapanış deal'i
    mt5.get_history.return_value = [
        {'position_id': 1, 'magic': HMA_MAGIC, 'entry': 0, 'profit': 0.0, 'timestamp': now - 100, 'symbol': 'X'},
        {'position_id': 1, 'magic': HMA_MAGIC, 'entry': 1, 'profit': closed, 'timestamp': now - 10, 'symbol': 'X', 'position_type': 'BUY'}]
    mt5.get_positions.return_value = [
        {'ticket': 2, 'identifier': 2, 'magic': HMA_MAGIC, 'profit': floating, 'swap': 0.0, 'commission': 0.0},
        {'ticket': 3, 'identifier': 3, 'magic': 0, 'profit': -9999.0, 'swap': 0.0, 'commission': 0.0}]   # manuel: sayılmaz
    bot = StrategyBot(mt5)
    bot.stop_profit_amount, bot.stop_loss_amount = profit, loss
    bot.is_running, bot.started_at = True, now - 3600
    bot.stop = Mock(side_effect=lambda: setattr(bot, 'is_running', False))
    return bot


def test_stops_at_profit_target_counting_closed_and_floating_bot_positions_only():
    bot = make(profit=100, closed=60.0, floating=40.0)
    assert bot.check_session_target(force=True) == 100.0
    bot.stop.assert_called_once()
    assert bot.runtime_state == 'PROFIT_TARGET'


def test_stops_at_loss_limit_and_ignores_manual_positions():
    bot = make(loss=50, closed=-20.0, floating=-35.0)
    assert bot.check_session_target(force=True) == -55.0
    assert bot.runtime_state == 'LOSS_LIMIT' and not bot.is_running


def test_keeps_running_below_limits_and_when_disabled():
    bot = make(profit=100, loss=100, closed=10.0, floating=-20.0)
    assert bot.check_session_target(force=True) == -10.0 and bot.is_running
    off = make(closed=1e6)
    assert off.check_session_target(force=True) is None and off.is_running


def test_settings_validate_and_roundtrip():
    bot = StrategyBot(Mock())
    assert bot.validated_config({'stop_profit_amount': 250, 'stop_loss_amount': 100})['stop_profit_amount'] == 250
    for bad in (-1, float('nan'), True, 'x'):
        try:
            bot.validated_config({'stop_loss_amount': bad})
        except ValueError:
            continue
        raise AssertionError(bad)
    assert bot.get_status()['stop_loss_amount'] == 0.0
