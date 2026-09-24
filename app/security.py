"""Two-operator dashboard authentication and server-side role enforcement."""
import base64
import binascii
import os
import secrets
from urllib.parse import urlsplit
from starlette.responses import JSONResponse

TRADER_ACTIONS = {
    '/api/security/unlock', '/api/security/lock',
    '/api/trade/preview', '/api/backtest', '/api/ai/advice', '/api/ai/learn',
    '/api/ai/execute', '/api/order/open', '/api/order/pending', '/api/order/cancel',
    '/api/order/close', '/api/order/close-all', '/api/order/close-profit',
    '/api/order/close-loss', '/api/position/stops', '/api/position/partial-close',
}
VIEWER_ACTIONS = {'/api/trade/preview', '/api/backtest'}


async def protect_dashboard(request, call_next):
    accounts = [(os.getenv('DASHBOARD_USER', 'admin'), os.getenv('DASHBOARD_PASSWORD', ''), 'ADMIN')]
    second_user = os.getenv('DASHBOARD_USER_2', '').strip()
    second_password = os.getenv('DASHBOARD_PASSWORD_2', '')
    second_role = os.getenv('DASHBOARD_ROLE_2', 'TRADER').upper()
    if bool(second_user) != bool(second_password) or (second_user and second_user == accounts[0][0]):
        return JSONResponse({'detail': 'İkinci panel kullanıcısı eksik veya geçersiz yapılandırıldı.'}, status_code=503)
    if second_role not in ('VIEWER', 'TRADER', 'ADMIN'):
        return JSONResponse({'detail': 'İkinci kullanıcı rolü geçersiz.'}, status_code=503)
    if second_user:
        accounts.append((second_user, second_password, second_role))
    if not accounts[0][1]:
        return JSONResponse({'detail': 'Panel erişimi kapalı: DASHBOARD_PASSWORD yapılandırılmalı.'}, status_code=503)
    authenticated_user = None
    authenticated_role = None
    try:
        scheme, value = request.headers.get('authorization', '').split(' ', 1)
        if scheme.lower() != 'basic':
            raise ValueError()
        supplied_user, supplied_password = base64.b64decode(value, validate=True).decode().split(':', 1)
        for username, password, role in accounts:
            if secrets.compare_digest(supplied_user.encode(), username.encode()) & secrets.compare_digest(supplied_password.encode(), password.encode()):
                authenticated_user = username
                authenticated_role = role
    except (ValueError, UnicodeError, binascii.Error):
        pass
    if not authenticated_user:
        return JSONResponse({'detail': 'Panel oturumu gerekli.'}, status_code=401,
                            headers={'WWW-Authenticate': 'Basic realm="Trading Dashboard", charset="UTF-8"'})
    request.state.dashboard_user = authenticated_user
    request.state.dashboard_role = authenticated_role
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        allowed = (authenticated_role == 'ADMIN' or
                   request.url.path in (VIEWER_ACTIONS if authenticated_role == 'VIEWER' else TRADER_ACTIONS))
        if not allowed:
            return JSONResponse({'detail': 'Bu işlem için panel yetkiniz yok.'}, status_code=403)
    if request.url.path.startswith('/api/debug/') and authenticated_role != 'ADMIN':
        return JSONResponse({'detail': 'Bu işlem için panel yetkiniz yok.'}, status_code=403)
    if request.url.path.startswith('/api/debug/') and os.getenv('ENABLE_DEBUG_API') != '1':
        return JSONResponse({'detail': 'Debug API kapalı.'}, status_code=404)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        cross_origin = origin and (urlsplit(origin).netloc != request.headers.get('host'))
        form = request.headers.get('content-type', '').split(';')[0] in (
            'application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain')
        if cross_origin or request.headers.get('sec-fetch-site') == 'cross-site' or form:
            return JSONResponse({'detail': 'Farklı kaynaktan işlem isteği reddedildi.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        try:
            from app.main import mt5_client
            mt5_client.journal.event('info' if response.status_code < 400 else 'warning', 'operator',
                f'{authenticated_user} {request.method} {request.url.path} HTTP {response.status_code}')
        except Exception:
            # An audit write must not turn a completed broker action into an ambiguous HTTP failure.
            pass
    return response
