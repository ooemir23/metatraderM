"""End-to-end: the running bot buys and sells by itself on every new crossover, like the MT5 EA.

The real StrategyBot loop body (_check_strategy_once) is driven candle by candle against a fake MT5
that keeps positions; the expected trades come from the scanner's own crossover formula.
Settings are the live ones: XAUUSD H1, AUTO_TRADE, HMA14 x EMA34, ATR risk (2x stop, 3x target),
close opposite, one position per direction.
"""
import math
import random

from app import signal_engine as se
from app.mt5_bridge import HMA_MAGIC
from app.strategy_bot import StrategyBot
from app.strategy_research import Settings, indicators

POINT = 0.01
H1 = 3600


class FakeMT5:
    """Minimal MT5 client: candles up to `now`, positions, order/close journal."""

    def __init__(self, rows):
        self.rows, self.now = rows, 0
        self.login_id, self.server, self.account_type = 7, 'Tickmill-Demo', 'DEMO'
        self.max_order_lots, self.max_total_lots, self.tick_clock_offset = 1.0, 1.0, 10800
        self.positions, self.opened, self.closed, self.request_ids = [], [], [], []
        self.ticket = 1000

    # data
    def ensure_connected(self): return True
    def get_rates(self, symbol, timeframe, count):
        visible = self.rows[:self.now + 1]          # the last row is the forming candle
        return [dict(r) for r in visible[-count:]]
    def get_positions(self, fresh=False): return [dict(p) for p in self.positions]
    def get_symbol_spec(self, symbol): return {'point': POINT, 'digits': 2}
    def get_symbol_price(self, symbol): return {'spread': 30, 'point': POINT, 'bid': self.rows[self.now]['close']}
    def has_unresolved_bot_order(self, symbol): return False
    def get_history(self, days=7): return []

    # trading
    def open_order(self, symbol, side, lot, *, sl_points=0, tp_points=0, comment='', magic=0, request_id=None, **kw):
        assert request_id not in self.request_ids, 'one order per signal'
        self.request_ids.append(request_id)
        self.ticket += 1
        price = self.rows[self.now]['open']
        self.positions.append({'ticket': self.ticket, 'identifier': self.ticket, 'symbol': symbol, 'type': side,
                               'magic': magic, 'volume': lot, 'price_open': price})
        self.opened.append({'candle': self.now, 'side': side, 'lot': lot, 'sl_points': sl_points,
                            'tp_points': tp_points, 'magic': magic, 'comment': comment})
        return {'success': True, 'ticket': self.ticket, 'price': price}

    def close_position(self, ticket, expected_magic=None, **kw):
        pos = next(p for p in self.positions if p['ticket'] == ticket)
        assert pos['magic'] == expected_magic == HMA_MAGIC
        self.positions.remove(pos)
        self.closed.append({'candle': self.now, 'ticket': ticket, 'side': pos['type']})
        return {'success': True}


def market(n=650, seed=21):
    rnd = random.Random(seed)
    p, rows = 4100.0, []
    for i in range(n):
        o = p
        p += rnd.gauss(0, 4) + 3.0 * math.sin(i / 9)    # trending legs -> several crossovers
        rows.append(dict(time=1_790_000_000 + i * H1, open=o, high=max(o, p) + 1.5, low=min(o, p) - 1.5,
                         close=p, tick_volume=500))
    return rows


def live_bot(client):
    bot = StrategyBot(client)
    bot.symbol, bot.symbols, bot.timeframe_minutes = 'XAUUSD', ['XAUUSD'], 60
    bot.hma_period, bot.second_ma_type, bot.second_ma_period = 14, 'EMA', 34
    bot.execution_mode, bot.lot_size, bot.lot_mode = 'AUTO_TRADE', 1.0, 'FIXED'
    bot.risk_mode, bot.atr_stop_multiplier, bot.atr_target_multiplier = 'ATR', 2.0, 3.0
    bot.use_stop_loss = bot.use_take_profit = True
    bot.close_opposite = bot.single_position = True
    # These tests pin the pure HMA_Crossover_EA behaviour; the user's start/copy options are tested separately.
    bot.trend_entry_on_start = bot.copy_manual_trades = False
    bot.is_running = True
    return bot


def run(start=450):
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    for now in range(start, len(rows)):            # one loop pass per new candle (the bot polls every 0.5 s)
        client.now = now
        for _ in range(2):                          # a second pass on the same candle must do nothing
            bot._check_strategy_once()
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    crosses = [(i, se.cross_at(fast, slow, i)) for i in range(len(rows) - 1) if se.cross_at(fast, slow, i)]
    return rows, client, bot, crosses


def test_bot_opens_on_every_new_crossover_and_reverses():
    rows, client, bot, crosses = run()
    start = 450
    # Crossovers on candles that closed while the bot was running (candle i closes when i+1 opens).
    expected = [(i, side) for i, side in crosses if i >= start]
    assert len(expected) >= 4, 'test market must contain several crossovers'
    # Each one -> exactly one automatic order, sent right after that candle closed, in its direction.
    assert [(o['candle'] - 1, o['side']) for o in client.opened] == expected
    assert all(o['lot'] == 1.0 and o['magic'] == HMA_MAGIC and o['comment'].startswith('HMA Bot') for o in client.opened)
    # Reverse signal closes the previous position first; never two positions at once.
    assert len(client.closed) == len(expected) - 1
    for close, order in zip(client.closed, client.opened[1:]):
        assert close['candle'] == order['candle'] and close['side'] != order['side']
    assert len(client.positions) == 1 and client.positions[0]['type'] == expected[-1][1]
    # Every automatic order is in the bot's sent-signal list (that is what plays the bot sound).
    auto = [h for h in bot.signal_history if h['source'] == 'AUTO']
    assert len(auto) == len(expected) and all(h['success'] for h in auto)


def test_atr_stop_and_target_match_the_signal_candle():
    rows, client, _, crosses = run()
    study = indicators(rows, Settings(hma_period=14, kama_period=34, atr_period=14))
    for order in client.opened:
        i = order['candle'] - 1                      # signal candle
        atr = indicators(rows[:i + 1], Settings(hma_period=14, kama_period=34, atr_period=14))['atr'][-1]
        assert order['sl_points'] == math.ceil(atr * 2.0 / POINT)
        assert order['tp_points'] == math.ceil(atr * 3.0 / POINT)
    assert study['atr'][-1] > 0


def test_no_trade_while_a_trend_just_continues():
    rows, client, bot, crosses = run()
    traded = {o['candle'] - 1 for o in client.opened}
    cross_candles = {i for i, _ in crosses}
    assert traded <= cross_candles, 'the bot only trades on crossover candles, never inside a trend'
    assert bot.symbol_status['XAUUSD']['state'] in ('WAITING_CROSS', 'ORDER_OPENED')


def test_signal_only_mode_catches_but_never_trades():
    rows = market()
    client = FakeMT5(rows)
    bot = live_bot(client)
    bot.execution_mode = 'SIGNAL_ONLY'
    for now in range(450, len(rows)):
        client.now = now
        bot._check_strategy_once()
    assert client.opened == [] and client.closed == []
    assert bot.pending_signal_actions, 'caught signals wait for approval'


def test_crossover_closed_before_start_is_not_traded_like_ea_oninit():
    # HMA_Crossover_EA OnInit sets m_lastBarTime to the current bar: a crossover on a candle that
    # closed before the EA started is never traded; the next crossover is.
    rows = market()
    closes = [r['close'] for r in rows]
    fast, slow = se.hma_series(closes, 14), se.ema_series(closes, 34)
    crosses = [i for i in range(450, len(rows) - 1) if se.cross_at(fast, slow, i)]
    first, second = crosses[0], crosses[1]
    client = FakeMT5(rows)
    bot = live_bot(client)
    client.now = first + 1                 # candle `first` (a crossover) has just closed; bot starts now
    bot._check_strategy_once()
    assert client.opened == [], 'old crossover at start is not traded'
    for now in range(first + 2, second + 2):
        client.now = now
        bot._check_strategy_once()
    assert [o['candle'] - 1 for o in client.opened] == [second]
