"""Same-origin, administrator-only access to the actual installed MT5 desktop."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import suppress
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.security import normalized_origin

COOKIE = 'native_mt5_session'
PREFIX = '/native-mt5/'


class NativeTerminal:
    def __init__(self, login_mode_allowed=None):
        self.secret = secrets.token_bytes(32)
        self.login_mode_allowed = login_mode_allowed
        self.connections = 0
        self.router = APIRouter()
        self.router.add_api_route(PREFIX+'{path:path}',self.http,methods=['GET','HEAD'])
        self.router.add_api_websocket_route(PREFIX+'{path:path}',self.websocket)

    def upstream(self, path, query='', websocket=False):
        host = os.getenv('MT5_HOST','metatrader5')
        port = int(os.getenv('MT5_DESKTOP_PORT','3000'))
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',host) or not 0 < port < 65536:
            raise ValueError('Geçersiz terminal bağlantı ayarı.')
        if ('\\' in path or '\x00' in path or any(part in ('.','..') for part in path.split('/'))
                or path.startswith('/') or len(path)>500):
            raise ValueError('Geçersiz terminal yolu.')
        return f"{'ws' if websocket else 'http'}://{host}:{port}/"+quote(path,safe='/')+('?' + query if query else '')

    def desktop_authorization(self):
        password = os.getenv('MT5_DESKTOP_PASSWORD','')
        if not password:
            raise ValueError('Orijinal terminal erişimi sunucuda yapılandırılmadı.')
        user = os.getenv('MT5_DESKTOP_USER','admin')
        return 'Basic '+base64.b64encode(f'{user}:{password}'.encode()).decode()

    def admin_key(self, username):
        accounts = [(os.getenv('DASHBOARD_USER','admin'),os.getenv('DASHBOARD_PASSWORD',''))]
        if os.getenv('DASHBOARD_ROLE_2','TRADER').upper()=='ADMIN':
            accounts.append((os.getenv('DASHBOARD_USER_2',''),os.getenv('DASHBOARD_PASSWORD_2','')))
        for user,password in accounts:
            if user and password and username==user:
                return hmac.new(self.secret,(username+'\0'+password).encode(),hashlib.sha256).digest()
        return None

    def token(self, username):
        key = self.admin_key(username)
        if key is None:
            raise ValueError('Yönetici oturumu doğrulanamadı.')
        value = base64.urlsafe_b64encode(json.dumps([username,int(time.time())+1800]).encode()).decode().rstrip('=')
        return value+'.'+hmac.new(key,value.encode(),hashlib.sha256).hexdigest()

    def validate_token(self, token):
        try:
            if not token or len(token)>2048:
                return False
            value,signature=token.split('.')
            username,expires=json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))
            key=self.admin_key(username)
            return (key is not None and type(expires) is int and time.time()<expires<=time.time()+1800
                    and hmac.compare_digest(signature,hmac.new(key,value.encode(),hashlib.sha256).hexdigest()))
        except (ValueError,TypeError,KeyError):
            return False

    def websocket_allowed(self, socket):
        public=os.getenv('DASHBOARD_PUBLIC_ORIGIN','').strip()
        expected=normalized_origin(public) if public else normalized_origin(
            ('https' if socket.url.scheme=='wss' else 'http')+'://'+socket.headers.get('host',''))
        origin=normalized_origin(socket.headers.get('origin'))
        enabled = self.login_mode_allowed is None or self.login_mode_allowed()
        return bool(enabled and expected and origin==expected and self.validate_token(socket.cookies.get(COOKIE)))

    def fetch(self, url, authorization, method):
        with requests.Session() as session:
            session.trust_env=False
            with session.request(method,url,headers={'Authorization':authorization,'Accept-Encoding':'identity'},
                                 timeout=(5,20),allow_redirects=False,stream=True) as upstream:
                if upstream.status_code in (401,403):
                    raise ValueError('Terminal sunucu oturumu doğrulanamadı.')
                chunks=[];size=0
                for chunk in upstream.iter_content(65536):
                    size+=len(chunk)
                    if size>32*1024*1024:
                        raise ValueError('Terminal yanıtı boyut sınırını aşıyor.')
                    chunks.append(chunk)
                return upstream.status_code,upstream.headers.get('Content-Type','application/octet-stream'),b''.join(chunks),upstream.headers.get('Location')

    async def http(self, request: Request, path: str):
        if getattr(request.state,'dashboard_role',None)!='ADMIN':
            raise HTTPException(status_code=403,detail='Orijinal MT5 terminali için yönetici erişimi gerekli.')
        if self.login_mode_allowed is not None and not self.login_mode_allowed():
            raise HTTPException(status_code=409,detail='Orijinal terminali paneldeki açma düğmesiyle etkinleştirin.')
        try:
            url=self.upstream(path,request.url.query)
            status,kind,body,location=await asyncio.to_thread(self.fetch,url,self.desktop_authorization(),request.method)
            headers={'Content-Type':kind,'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                     'Content-Security-Policy':"frame-ancestors 'self'"}
            if location:
                if not location.startswith('/') or location.startswith('//'):
                    raise ValueError('Terminal yönlendirmesi doğrulanamadı.')
                headers['Location']=PREFIX+location.lstrip('/')
            response=Response(body,status_code=status,headers=headers)
            if not path or path in ('index.html','vnc.html','vnc/index.html'):
                public=normalized_origin(os.getenv('DASHBOARD_PUBLIC_ORIGIN',''))
                response.set_cookie(COOKIE,self.token(request.state.dashboard_user),max_age=1800,
                    httponly=True,secure=(public[0]=='https' if public else request.url.scheme=='https'),
                    samesite='strict',path=PREFIX)
            return response
        except Exception:
            raise HTTPException(status_code=503,detail='Orijinal MT5 ekranı açılmadı; terminal bağlantısını kontrol edin.') from None

    async def websocket(self, socket: WebSocket, path: str):
        if path!='websockify' or not self.websocket_allowed(socket) or self.connections>=2:
            await socket.close(code=1008)
            return
        self.connections+=1
        tasks=[]
        try:
            url=self.upstream(path,socket.url.query,websocket=True)
            protocols=[p for p in socket.scope.get('subprotocols',[]) if p in ('binary','base64')]
            async with connect(url,additional_headers={'Authorization':self.desktop_authorization()},
                               subprotocols=protocols or None,origin=f'http://{os.getenv("MT5_HOST","metatrader5")}:3000',
                               proxy=None,compression=None,max_size=32*1024*1024,max_queue=4,
                               open_timeout=8,close_timeout=3) as remote:
                await socket.accept(subprotocol=remote.subprotocol)
                async def to_terminal():
                    while True:
                        event=await socket.receive()
                        if event['type']=='websocket.disconnect':return
                        if not self.websocket_allowed(socket):return
                        await remote.send(event.get('bytes') if event.get('bytes') is not None else event.get('text',''))
                async def from_terminal():
                    async for message in remote:
                        if not self.websocket_allowed(socket):return
                        await (socket.send_bytes(message) if isinstance(message,bytes) else socket.send_text(message))
                async def session_expiry():
                    while self.websocket_allowed(socket):await asyncio.sleep(15)
                tasks=[asyncio.create_task(fn()) for fn in (to_terminal,from_terminal,session_expiry)]
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect,ConnectionClosed):
            pass
        except Exception:
            pass  # Never log upstream credentials or native screen/input payloads.
        finally:
            for task in tasks:task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError,Exception):await task
            self.connections-=1
            with suppress(RuntimeError):await socket.close(code=1000)
