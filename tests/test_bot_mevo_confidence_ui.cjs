// Tests for MevO confidence toggle and threshold settings in Bot UI & API
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const nodes = new Map();
  const notices = [];
  const sent = [];

  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set(id === 'bot-settings-modal' ? ['hidden'] : []);
      nodes.set(id, {
        value: '',
        checked: false,
        innerText: '',
        textContent: '',
        hidden: false,
        style: {},
        dataset: {},
        classList: {
          contains: c => classes.has(c),
          add: c => classes.add(c),
          remove: c => classes.delete(c),
          toggle: () => {},
        },
      });
    }
    return nodes.get(id);
  };

  const status = {
    is_running: false,
    symbol: 'XAUUSD',
    timeframe_minutes: 60,
    hma_period: 14,
    second_ma_type: 'EMA',
    second_ma_period: 34,
    lot_size: 0.01,
    sl_points: 200,
    tp_points: 400,
    close_opposite: true,
    use_stop_loss: true,
    use_take_profit: false,
    entry_mode: 'MEVO',
    use_mevo_confidence: true,
    mevo_min_confidence: 65,
    logs: [],
  };

  const ctx = vm.createContext({
    console,
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      addEventListener: () => {},
      activeElement: null,
    },
    window: { addEventListener: () => {} },
    setInterval: () => {},
    setTimeout: () => {},
    fetch: async (url, opts) => {
      if (url === '/api/bot/status') return { ok: true, json: async () => status };
      if (url === '/api/bot/config' || url === '/api/bot/follow') {
        sent.push({ url, body: JSON.parse(opts.body) });
        return { ok: true, json: async () => status };
      }
      return { ok: true, json: async () => ({}) };
    },
  });

  vm.runInContext(fs.readFileSync('app/static/app.js', 'utf8'), ctx);
  ctx.showToast = (text, type) => notices.push({ text, type });

  // 1. Initial modal load receives server values
  await ctx.toggleBotSettingsModal();
  assert.equal(node('cfg-mevo-use-conf').checked, true);
  assert.equal(node('cfg-mevo-min-conf').value, 65);

  // 2. Change values in modal and save
  node('cfg-mevo-use-conf').checked = false;
  node('cfg-mevo-min-conf').value = 60;
  await ctx.saveBotSettings();

  const lastSaved = sent.at(-1).body;
  assert.equal(lastSaved.use_mevo_confidence, false);
  assert.equal(lastSaved.mevo_min_confidence, 60);

  // 3. Quick settings save when bot is stopped uses /api/bot/config
  node('bot-quick-mevo-conf-check').checked = true;
  node('bot-quick-mevo-min-conf').value = 55;
  await ctx.saveBotQuickMevoConf();
  assert.equal(sent.at(-1).url, '/api/bot/config');
  assert.equal(sent.at(-1).body.use_mevo_confidence, true);
  assert.equal(sent.at(-1).body.mevo_min_confidence, 55);

  // 4. Quick settings save when bot is running uses /api/bot/follow
  status.is_running = true;
  await ctx.fetchBotStatus();
  node('bot-quick-mevo-conf-check').checked = false;
  await ctx.saveBotQuickMevoConf();
  assert.equal(sent.at(-1).url, '/api/bot/follow');
  assert.equal(sent.at(-1).body.use_mevo_confidence, false);

  // 5. Bot mode line reflects custom threshold and disabled state
  ctx.renderBotMode({ entry_mode: 'MEVO', use_mevo_confidence: true, mevo_min_confidence: 60 });
  assert.match(node('bot-mode-line').innerHTML, /güven ≥ %60/);

  ctx.renderBotMode({ entry_mode: 'MEVO', use_mevo_confidence: false });
  assert.match(node('bot-mode-line').innerHTML, /güven filtresi kapalı/);

  console.log('MevO confidence UI & API settings PASS');
})().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
