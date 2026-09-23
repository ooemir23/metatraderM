const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const html = fs.readFileSync('app/static/index.html', 'utf8');
  const passwordInput = html.match(/<input[^>]*id="login-pass"[^>]*>/)?.[0];
  const accountInput = html.match(/<input[^>]*id="login-acc"[^>]*>/)?.[0];
  assert.match(passwordInput || '', /type="password"/);
  assert.doesNotMatch(passwordInput || '', /\svalue=/);
  assert.doesNotMatch(accountInput || '', /\svalue=/);
  assert.match(html, /id="login-mode-demo"/);
  assert.match(html, /id="login-mode-real"/);

  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', checked:false, disabled:false,
      className:'', textContent:'', classList:{toggle(){},contains(){return false}}});
    return nodes.get(id);
  };
  node('login-acc').value = '12345';
  node('login-pass').value = 'example-password';
  node('login-srv').value = 'Broker-Live-2';
  node('login-mode-real').checked = true;
  let request;
  const context = vm.createContext({console, Date, AbortController,
    document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[]},
    window:{addEventListener(){}},setTimeout(){},clearTimeout(){},setInterval(){},
    fetch:async(url, options) => {
      request = {url, payload:JSON.parse(options.body)};
      return {ok:false,json:async()=>({detail:'wrong mode'})};
    }});
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8'), context);
  context.showToast=()=>{};
  await context.submitLogin();
  assert.equal(request.url, '/api/account/login');
  assert.equal(request.payload.account_type, 'REAL');
  assert.equal(request.payload.server, 'Broker-Live-2');
  assert.equal(node('login-pass').value, '', 'password must be cleared after login attempt');
  context.confirmAction = async () => true;
  context.renderAccountData({connected:true,account_type:'REAL',account_mismatch:false,login:12345,
    server_time:'12:00',connected_since:'12:00',currency:'USD',balance:100,equity:100,margin_free:100,profit:0});
  node('ai-cfg-mode').value = 'FULL_AUTO';
  await context.saveAIAutopilot();
  assert.equal(request.url, '/api/ai/autopilot');
  assert.equal(request.payload.confirm_real_full_auto, true);
  console.log('Broker login choice and password masking PASS');
})().catch(error => {console.error(error);process.exitCode=1;});
