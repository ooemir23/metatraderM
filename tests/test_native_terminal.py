import asyncio
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import native_terminal as native
from test_trade_safety import authenticated_web, client

@pytest.fixture(autouse=True)
def isolated_native_mode(monkeypatch):
    from app import main
    monkeypatch.setattr(main.native_terminal,'login_mode_allowed',lambda:True)


def test_native_session_cannot_be_forged_or_reused_after_password_role_change(monkeypatch):
    terminal=native.NativeTerminal()
    token=terminal.token('test')
    assert terminal.validate_token(token)
    assert not terminal.validate_token(token[:-1]+('0' if token[-1]!='0' else '1'))
    monkeypatch.setenv('DASHBOARD_PASSWORD','rotated')
    assert not terminal.validate_token(token)
    monkeypatch.setenv('DASHBOARD_USER_2','second');monkeypatch.setenv('DASHBOARD_PASSWORD_2','second-secret')
    with pytest.raises(ValueError):terminal.token('second')
    monkeypatch.setenv('DASHBOARD_ROLE_2','ADMIN')
    second=terminal.token('second');assert terminal.validate_token(second)
    monkeypatch.setenv('DASHBOARD_ROLE_2','TRADER');assert not terminal.validate_token(second)


@pytest.mark.parametrize('value',['','broken','xxx.yyy','e30.invalid','x'*3000])
def test_bad_session_fails_closed(value):
    assert not native.NativeTerminal().validate_token(value)


def test_native_session_expires(monkeypatch):
    terminal=native.NativeTerminal();now=time.time();token=terminal.token('test')
    monkeypatch.setattr(native.time,'time',lambda:now+1801)
    assert not terminal.validate_token(token)


@pytest.mark.parametrize('path',['../config','.','app/../../etc','//foreign/path','bad\\path','bad\x00path'])
def test_native_proxy_cannot_change_origin_or_traverse_paths(path):
    with pytest.raises(ValueError):native.NativeTerminal().upstream(path)


def test_native_http_is_admin_only_and_never_exposes_desktop_credentials(monkeypatch):
    from app import main
    monkeypatch.setenv('MT5_DESKTOP_PASSWORD','internal-desktop-secret')
    captured=[]
    def fetch(self,url,authorization,method):
        captured.append((url,authorization,method))
        return 200,'text/html',b'<html>Actual terminal</html>',None
    monkeypatch.setattr(native.NativeTerminal,'fetch',fetch)
    web=authenticated_web();r=web.get('/native-mt5/')
    assert r.status_code==200 and r.headers['x-frame-options']=='SAMEORIGIN'
    cookie=r.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/native-mt5/' in cookie
    assert main.native_terminal.validate_token(web.cookies.get(native.COOKIE))
    assert b'internal-desktop-secret' not in r.content and 'internal-desktop-secret' not in cookie
    assert base64.b64decode(captured[0][1].split()[1]).decode()=='admin:internal-desktop-secret'
    assert 'native_mt5_session=' in web.get('/native-mt5/vnc/index.html').headers['set-cookie']
    monkeypatch.setenv('DASHBOARD_USER_2','reader');monkeypatch.setenv('DASHBOARD_PASSWORD_2','reader-secret')
    monkeypatch.setenv('DASHBOARD_ROLE_2','TRADER')
    token=base64.b64encode(b'reader:reader-secret').decode()
    other=TestClient(main.app,headers={'Authorization':'Basic '+token})
    assert other.get('/native-mt5/').status_code==403
    assert len(captured)==2
    assert web.get('/').headers['x-frame-options']=='DENY'


def test_native_redirect_cannot_send_browser_to_external_host(monkeypatch):
    monkeypatch.setenv('MT5_DESKTOP_PASSWORD','internal')
    monkeypatch.setattr(native.NativeTerminal,'fetch',lambda *args:(302,'text/html',b'','https://untrusted.example/'))
    assert authenticated_web().get('/native-mt5/').status_code==503


def test_native_websocket_requires_signed_cookie_and_same_origin():
    web=authenticated_web()
    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect('/native-mt5/websockify',headers={'Origin':'http://testserver'}):pass


def test_native_websocket_relays_only_authorized_connection_and_cleans_up(monkeypatch):
    from app import main
    monkeypatch.setenv('MT5_DESKTOP_PASSWORD','internal')
    monkeypatch.setattr(native.NativeTerminal,'fetch',lambda *args:(200,'text/html',b'<html>Terminal</html>',None))
    class Remote:
        subprotocol='binary'
        def __init__(self):self.queue=asyncio.Queue()
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):await self.queue.put(value)
        async def messages(self):
            yield b'RFB 003.008\n'
            while True:yield await self.queue.get()
        def __aiter__(self):return self.messages()
    calls=[]
    def connect(url,**kwargs):calls.append((url,kwargs));return Remote()
    monkeypatch.setattr(native,'connect',connect)
    web=authenticated_web();assert web.get('/native-mt5/').status_code==200
    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect('/native-mt5/websockify',headers={'Origin':'https://untrusted.example'}):pass
    assert not calls
    with web.websocket_connect('/native-mt5/websockify',headers={'Origin':'http://testserver'},subprotocols=['binary']) as ws:
        assert ws.receive_bytes()==b'RFB 003.008\n'
        ws.send_bytes(b'isolated-stub-echo');assert ws.receive_bytes()==b'isolated-stub-echo'
    assert main.native_terminal.connections==0
    assert calls[0][0]=='ws://metatrader5:3000/websockify'
    assert calls[0][1]['proxy'] is None


def test_native_activation_cannot_be_triggered_by_get_and_disables_conflicting_auto_login(client,monkeypatch):
    from app import main
    from unittest.mock import Mock
    monkeypatch.setattr(main,'mt5_client',client)
    monkeypatch.setenv('MT5_DESKTOP_PASSWORD','isolated-desktop-password')
    monkeypatch.setattr(main.native_terminal,'login_mode_allowed',lambda:client.terminal_managed_login)
    monkeypatch.setattr(native.NativeTerminal,'fetch',lambda *args:(200,'text/html',b'<html>Terminal</html>',None))
    client.save_credentials=Mock()
    web=authenticated_web()
    assert web.get('/native-mt5/vnc/index.html').status_code==409
    assert not client.terminal_managed_login
    response=web.post('/api/terminal/activate',json={'request_id':'native-activate-test-123'})
    assert response.status_code==200 and client.terminal_managed_login
    client.save_credentials.assert_called_once()
    assert web.get('/native-mt5/vnc/index.html').status_code==200
    client.password='saved-demo-password';client.mt5.account_info.return_value=None
    client.get_account_info()
    client.mt5.login.assert_not_called()
    client.mt5.order_send.assert_not_called()
