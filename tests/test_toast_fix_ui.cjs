// Clickable error toasts open the place where the error is fixed; the click itself changes nothing.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let down;
const g = {console, setTimeout: fn => fn(), String, document: {addEventListener: (n, fn) => { if (n === 'pointerdown') down = fn; }, getElementById: () => null}, window: {}};
g.window = g;
vm.runInNewContext(fs.readFileSync('app/static/toast-fix.js', 'utf8'), g);
const fix = g.HMAToastFix;
const risk = '❌ Hata: XAUUSD: Emrin stop riski 9378 USD; özsermayenin %2 sınırı (9298.54 USD) aşılıyor.';
assert.equal(fix.resolve(risk, 'bot').label, 'Bot ayarları · lot ve stop', 'bot / Şimdi AL-SAT origin -> bot settings');
assert.equal(fix.resolve(risk, 'order').label, 'Emir paneli · hacim', 'order panel origin -> order volume');
assert.equal(fix.resolve('Gerçek hesapta yeni emir için geçerli Stop Loss zorunlu.', 'order').label, 'Emir paneli · Zarar durdur (SL)');
assert.equal(fix.resolve('Günlük zarar sınırı doldu; yeni emirler kapalı.').label, 'Günlük risk ve emir kilidi');
assert.equal(fix.resolve('TOTP doğrulama kodu gerekli').label, 'İşlem Güvenliği (TOTP)');
assert.equal(fix.resolve('"Şimdi AL/SAT" 3 lot açar. Otomatik bot en fazla 1 lot açabilir; bu lotla bot başlatılamaz.', 'bot').label, 'Bot ayarları · lot sınırları');
assert.equal(fix.resolve('Bu sembolde işlem seansı kapalı.'), null, 'nothing to fix -> plain toast');
// origin follows where the user clicked
down({target: {closest: () => ({matches: s => s.includes('#order-card')})}});
assert.equal(fix.origin(), 'order');
down({target: {closest: () => ({matches: () => false})}});
assert.equal(fix.origin(), 'bot');
// attach only on error / warning toasts with a known fix
const toast = () => ({classList: {add() {}}, setAttribute() {}, appendChild(n) { this.hint = n; }, addEventListener(n, f) { this[n] = f; }, remove() { this.removed = true; }});
g.document.createElement = () => ({});
const ok = toast();
assert.equal(fix.attach(ok, risk, 'error'), true);
assert.match(ok.hint.textContent, /Çözmek için tıkla/);
assert.equal(fix.attach(toast(), risk, 'success'), false);
assert.equal(fix.attach(toast(), 'Piyasa kapalı', 'error'), false);
console.log('Toast fix: risk/stop/lot/limit/security routes by origin, plain toasts untouched PASS');
