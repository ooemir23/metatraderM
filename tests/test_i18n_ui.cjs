const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let language = 'en';
const ctx = {window:{},localStorage:{getItem:()=>language},navigator:{language:'tr-TR'},document:{readyState:'loading',addEventListener(){}}};
vm.runInNewContext(fs.readFileSync('app/static/i18n.js','utf8'),ctx);
const translate = ctx.window.MT5I18n.translate;
for (const file of fs.readdirSync('app/static').filter(f=>f.endsWith('.html'))) {
  const html = fs.readFileSync('app/static/'+file,'utf8').replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi,'');
  const texts = [...html.matchAll(/>([^<>]+)</g)].map(m=>m[1]);
  texts.push(...[...html.matchAll(/(?:title|placeholder|aria-label)="([^"]*)"/g)].map(m=>m[1]));
  for (const value of texts) {
    const text = value.trim().replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
    if (!/[çğıöşüÇĞİÖŞÜ]/.test(text) || text==='Türkçe') continue; // Native language name stays identifiable.
    assert.doesNotMatch(translate(text),/[çğıöşüÇĞİÖŞÜ]/,`${file}: ${text}`);
  }
}
assert.equal(translate('ATR(14): 2.00000 · ER: 0.40 · Onay bekleniyor · ATR stop/hedef'),'ATR(14): 2.00000 · ER: 0.40 · Awaiting confirmation · ATR stop/target');
language = 'tr';
assert.equal(translate('XAUUSD · HMA / KAMA / ATR araştırması'),'XAUUSD · HMA / KAMA / ATR araştırması');
console.log('Language UI: HTML labels, attributes, dynamic bot status and Turkish preservation PASS');
