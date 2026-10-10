"""Browser POST requests cross TLS termination; no broker is used here."""
import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.security import protect_dashboard


def web(monkeypatch, public='https://trade.example'):
    monkeypatch.setenv('DASHBOARD_PUBLIC_ORIGIN', public)
    calls = []

    async def preview(request):
        calls.append(await request.json())
        return JSONResponse({'success': True})

    app = Starlette(routes=[Route('/api/trade/preview', preview, methods=['POST'])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=protect_dashboard)
    return TestClient(app, base_url='http://trade.example'), calls


@pytest.mark.parametrize('origin', ['https://trade.example', 'https://trade.example:443', 'https://TRADE.EXAMPLE'])
def test_https_browser_preview_is_accepted_behind_http_proxy(monkeypatch, origin):
    client, calls = web(monkeypatch)
    result = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
                         headers={'Origin': origin, 'Sec-Fetch-Site': 'same-origin'}, json={'symbol': 'BCHUSD'})
    assert result.status_code == 200 and calls == [{'symbol': 'BCHUSD'}]


@pytest.mark.parametrize('origin', ['http://trade.example', 'https://evil.example', 'https://trade.example:444',
    'https://trade.example:0', 'null', '', 'https://trade.example/path', 'https://user@trade.example',
    'https://trade.example?query=1', 'https://trade.example#fragment', 'https://trade.example:bad',
    'https://[invalid', 'https://trade.example, https://evil.example'])
def test_untrusted_origin_never_reaches_handler(monkeypatch, origin):
    client, calls = web(monkeypatch)
    response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
        headers={'Origin': origin, 'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'trade.example'}, json={})
    assert response.status_code == 403 and not calls
    assert response.json()['reason_code'] == 'untrusted_origin'
    assert response.json()['not_submitted'] is True


def test_configured_origin_requires_the_same_request_host(monkeypatch):
    client, calls = web(monkeypatch)
    response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
        headers={'Origin': 'https://trade.example', 'Host': 'evil.example', 'X-Forwarded-Host': 'trade.example'}, json={})
    assert response.status_code == 403 and not calls


def test_forwarded_headers_cannot_override_direct_origin_checks(monkeypatch):
    client, calls = web(monkeypatch, public='')
    response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
        headers={'Origin': 'https://trade.example', 'X-Forwarded-Proto': 'https'}, json={})
    assert response.status_code == 403 and not calls
    response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
        headers={'Origin': 'http://trade.example:80', 'Sec-Fetch-Site': 'same-origin'}, json={})
    assert response.status_code == 200 and len(calls) == 1


@pytest.mark.parametrize('public', ['not-an-origin', 'https://trade.example/path', 'https://user@trade.example'])
def test_invalid_public_origin_fails_closed(monkeypatch, public):
    client, calls = web(monkeypatch, public=public)
    response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'), json={})
    assert response.status_code == 503 and not calls


def test_same_origin_does_not_bypass_cross_site_or_form_rejection(monkeypatch):
    client, calls = web(monkeypatch)
    for extra in [{'Sec-Fetch-Site': 'cross-site'}, {'Content-Type': 'text/plain'}]:
        response = client.post('/api/trade/preview', auth=('test', 'test-panel-password'),
                               headers={'Origin': 'https://trade.example', **extra}, json={})
        assert response.status_code == 403 and not calls


def test_explicitly_authenticated_non_browser_client_still_works(monkeypatch):
    client, calls = web(monkeypatch)
    assert client.post('/api/trade/preview', auth=('test', 'test-panel-password'), json={}).status_code == 200
    assert calls == [{}]
    assert client.post('/api/trade/preview', headers={'Origin': 'https://trade.example'}, json={}).status_code == 401
    assert len(calls) == 1
