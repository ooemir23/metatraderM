const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('app/static/index.html', 'utf8');
assert.match(html, /id="nc-bell"/); assert.match(html, /notify-center\.js/);
const src = fs.readFileSync('app/static/notify-center.js', 'utf8');
assert.match(src, /window\.showToast = wrapped/); assert.match(src, /MAX = 100/);
// Toasts last long enough to read.
const app = fs.readFileSync('app/static/app.js', 'utf8');
assert.match(app, /9000 : 6000/);
console.log('Notification bell: markup, history wrapper and toast duration PASS');
