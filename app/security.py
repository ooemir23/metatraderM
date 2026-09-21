"""Single-operator dashboard authentication, including HTML and debug routes."""
import base64
import binascii
import os
import secrets
from urllib.parse import urlsplit
from starlette.responses import JSONResponse


async def protect_dashboard(request, call_next):
    password = os.getenv('DASHBOARD_PASSWORD', '')
    username = os.getenv('DASHBOARD_USER', 'admin')
    if not password:
        return JSONResponse({'detail': 'Panel erişimi kapalı: DASHBOARD_PASSWORD yapılandırılmalı.'}, status_code=503)
    try:
        scheme, value = request.headers.get('authorization', '').split(' ', 1)
        if scheme.lower() != 'basic':
            raise ValueError()
        supplied_user, supplied_password = base64.b64decode(value, validate=True).decode().split(':', 1)
        valid = secrets.compare_digest(supplied_user.encode(), username.encode()) & secrets.compare_digest(supplied_password.encode(), password.encode())
    except (ValueError, UnicodeError, binascii.Error):
        valid = False
    if not valid:
        return JSONResponse({'detail': 'Panel oturumu gerekli.'}, status_code=401,
                            headers={'WWW-Authenticate': 'Basic realm="Trading Dashboard", charset="UTF-8"'})
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
    return response
