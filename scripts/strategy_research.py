"""Fetch broker H1/H4 history or compare exported CSVs; never submits orders."""
import argparse
import base64
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.strategy_research import Settings, compare


def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        rows = []
        for r in csv.DictReader(stream):
            row = {'time': int(r['time']), **{k: float(r[k]) for k in ('open', 'high', 'low', 'close')}}
            if r.get('spread'):
                row['spread'] = float(r['spread'])
            rows.append(row)
        return rows


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace the user's earlier reports/data.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def fetch_history(credentials, start, end, destination):
    c = json.loads(Path(credentials).read_text())
    base = c['url'].rstrip('/')
    url = urllib.parse.urlsplit(base)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Panel kimlik bilgileri için HTTPS veya localhost gerekli.')
    auth = 'Basic ' + base64.b64encode((c['username'] + ':' + c['password']).encode()).decode()
    result = {}
    # Reject redirects: do not forward authorization to a different destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    for tf in (60, 240):
        query = urllib.parse.urlencode({'symbol': 'XAUUSD', 'timeframe_minutes': tf, 'start': start, 'end': end})
        req = urllib.request.Request(base + '/api/research/history?' + query,
                                     headers={'Authorization': auth})
        with opener.open(req, timeout=180) as response:
            data = json.load(response)
        result[str(tf)] = data
        print(f"{'H1' if tf == 60 else 'H4'}: {data['count']} kapanmış mum")
    save_json(destination, result)


def epoch(text):
    return int(datetime.strptime(text, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--credentials', default='production.credentials.json')
    fetch.add_argument('--start', required=True, type=epoch)
    fetch.add_argument('--end', required=True, type=epoch)
    fetch.add_argument('--output', required=True)
    run = sub.add_parser('compare')
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument('--history', help='fetch komutunun JSON çıktısı')
    source.add_argument('--h1-csv', help='time,open,high,low,close,spread sütunları')
    run.add_argument('--h4-csv')
    run.add_argument('--settings', help='Settings alanlarını içeren JSON dosyası')
    run.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'fetch':
        if args.end <= args.start:
            parser.error('Başlangıç bitişten önce olmalı.')
        fetch_history(args.credentials, args.start, args.end, args.output)
    else:
        if args.history:
            data = json.loads(Path(args.history).read_text())
            h1, h4 = data['60']['rates'], data['240']['rates']
        else:
            if not args.h4_csv:
                parser.error('--h1-csv ile --h4-csv gerekli.')
            h1, h4 = read_csv(args.h1_csv), read_csv(args.h4_csv)
        settings = json.loads(Path(args.settings).read_text()) if args.settings else {'historical_spread': True}
        result = compare(h1, h4, Settings(**settings))
        save_json(args.output, result)
        print(f"Karşılaştırma: {len(result['comparisons'])} seçenek, durum: {result['status']}")


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        message = 'Sunucuda araştırma sürümü henüz kurulu değil.' if exc.code == 404 else 'Panel geçmiş isteği başarısız.'
        sys.exit(f'{message} HTTP {exc.code}')
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(f'Araştırma tamamlanamadı: {type(exc).__name__}')
