const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '../app/static/header-autohide.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');

assert.ok(!html.includes('id="market-open"'), 'symbol search button removed from the top bar');
assert.ok(!html.includes('id="market-favorites"'), 'favourite symbol strip removed from the top bar');
assert.ok(html.includes('id="chart-symbol-button"'), 'chart symbol button still opens the instrument list');
assert.ok(html.includes('header-autohide.js'), 'auto-hide script is loaded');
assert.ok(/id="hx-account-menu"[^>]*hidden/.test(html), "account status lives in the Hesap menu, hidden until opened");

const classes = new Set();
const header = {offsetHeight:150, classList:{add:c => classes.add(c), remove:c => classes.delete(c)}, contains:() => false};
let handler;
const window = {scrollY:0, addEventListener:(name, fn) => { handler = fn; }, requestAnimationFrame:fn => fn()};
vm.runInNewContext(src, {window, document:{getElementById:() => header, activeElement:null}});
const scroll = y => { window.scrollY = y; handler(); };
scroll(100); assert.ok(!classes.has('hx-header-hidden'), 'stays visible near the top');
scroll(400); assert.ok(classes.has('hx-header-hidden'), 'hides while scrolling down');
scroll(700); assert.ok(classes.has('hx-header-hidden'));
scroll(650); assert.ok(!classes.has('hx-header-hidden'), 'shows again on scroll up');
scroll(900); assert.ok(classes.has('hx-header-hidden'));
scroll(0); assert.ok(!classes.has('hx-header-hidden'), 'always visible at the top');
console.log('header auto-hide ok');
