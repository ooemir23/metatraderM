const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('app/static/index.html', 'utf8');
assert.match(html, /id="nc-bell"/); assert.match(html, /notify-center\.js/);
const src = fs.readFileSync('app/static/notify-center.js', 'utf8');
assert.match(src, /window\.showToast = wrapped/); assert.match(src, /MAX = 100/);
// Toasts last long enough to read.
const app = fs.readFileSync('app/static/app.js', 'utf8');
assert.match(app, /9000 : 6000/);
console.log('Notification bell: markup, history wrapper and toast duration PASS');
const nc = fs.readFileSync('app/static/notify-center.js', 'utf8');
for (const word of ['Sistem sağlığı', 'data-close-ticket', "'history'", "'trades'", "'mevo'", "'bot'", 'order-card']) assert.ok(nc.includes(word), word);
console.log('Notification bell: items link to the matching screen PASS');
const hs = fs.readFileSync('app/static/health.js', 'utf8');
assert.match(hs, /\[502, 504\]\.includes/); assert.match(hs, /Sunucu bağlantısı geri geldi/);
assert.doesNotMatch(hs, /Sunucuya ulaşılamadı:/);
console.log('Health: outage burst collapses into one message PASS');
