#!/usr/bin/env python3
"""
Quotable live-audit server. Standard library only.

Serves the app (index.html, engine.js) and two API routes:

  GET  /api/health   -> {"ok": true}
  POST /api/collect  -> body {"url": "https://example.com/page"}
                        returns the bundle engine.js audits:
                        { url, page, robots, sitemap, llms, ua }

Run:  python3 server.py            (http://localhost:8000)
Env:  PORT (default 8000), HOST (default 127.0.0.1; use 0.0.0.0 when hosting)
      QUOTABLE_ALLOW_PRIVATE=1 lets it fetch private addresses (tests only).
"""
import gzip
import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/engine.js': ('engine.js', 'text/javascript; charset=utf-8'),
}

TIMEOUT = 10            # seconds per request
MAX_BODY = 3_000_000    # bytes of page or robots.txt kept
MAX_REDIRECTS = 5
MAX_REQUEST = 4096      # bytes of POST body accepted

BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')

# The user-agent each crawler identifies with. Keys match the tokens engine.js expects.
BOT_UAS = {
    'Googlebot': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Bingbot': 'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
    'OAI-SearchBot': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot',
    'ChatGPT-User': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot',
    'Claude-SearchBot': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://www.anthropic.com)',
    'Claude-User': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)',
    'PerplexityBot': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)',
    'Perplexity-User': 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)',
}


class AuditError(Exception):
    """A problem worth showing to the person running the audit."""


def allow_private():
    return os.environ.get('QUOTABLE_ALLOW_PRIVATE') == '1'


def is_public_ip(text):
    ip = ipaddress.ip_address(text)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve(host, port):
    """Return one address for host that is safe to connect to."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise AuditError('Could not find the site "%s". Check the address.' % host)
    addresses = [info[4][0] for info in infos]
    if allow_private():
        return addresses[0]
    # Refuse if any answer is private, so a mixed DNS answer cannot be used to reach inside.
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise AuditError('That address points to a private or local network, which this tool will not fetch.')
    return addresses[0]


class PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to an address we already validated, not to whatever DNS says now."""

    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=TIMEOUT)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def normalise_url(raw):
    raw = (raw or '').strip()
    if not raw:
        raise AuditError('Enter the address of the page you want to audit.')
    if '://' not in raw:
        raw = 'https://' + raw
    parts = urlsplit(raw)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise AuditError('That does not look like a web address. Try something like https://example.com/pricing')
    if parts.username or parts.password:
        raise AuditError('Addresses with a username or password are not supported.')
    return parts._replace(fragment='').geturl()


def request_once(url, user_agent, read_body):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise AuditError('The site redirected to an address this tool cannot fetch.')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise AuditError('That address has an invalid port.')
    address = resolve(parts.hostname, port)
    cls = PinnedHTTPSConnection if parts.scheme == 'https' else PinnedHTTPConnection
    conn = cls(parts.hostname, port, address)
    try:
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        conn.request('GET', path, headers={
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'close',
        })
        res = conn.getresponse()
        headers = res.headers
        body = b''
        if read_body:
            body = res.read(MAX_BODY + 1)[:MAX_BODY]
            encoding = (headers.get('Content-Encoding') or '').lower()
            try:
                if encoding == 'gzip':
                    body = zlib.decompressobj(31).decompress(body, MAX_BODY)
                elif encoding == 'deflate':
                    try:
                        body = zlib.decompressobj().decompress(body, MAX_BODY)
                    except zlib.error:
                        body = zlib.decompressobj(-15).decompress(body, MAX_BODY)
            except (zlib.error, gzip.BadGzipFile):
                body = b''
        return res.status, headers, body
    finally:
        conn.close()


def fetch(url, user_agent=BROWSER_UA, read_body=True):
    """GET url, following redirects. Returns (status, final_url, headers, text)."""
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, body = request_once(url, user_agent, read_body)
        location = headers.get('Location')
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        charset = headers.get_content_charset() or 'utf-8'
        try:
            text = body.decode(charset, errors='replace')
        except LookupError:
            text = body.decode('utf-8', errors='replace')
        return status, url, headers, text
    raise AuditError('The page redirected too many times.')


def fetch_status(url, user_agent=BROWSER_UA):
    """Status code only; None when the request fails."""
    try:
        return fetch(url, user_agent, read_body=False)[0]
    except (AuditError, OSError, http.client.HTTPException):
        return None


def collect(raw_url):
    url = normalise_url(raw_url)
    try:
        status, final_url, headers, body = fetch(url)
    except ssl.SSLError:
        raise AuditError('The site has a certificate problem, so the page could not be fetched securely.')
    except socket.timeout:
        raise AuditError('The site took too long to answer.')
    except (OSError, http.client.HTTPException):
        raise AuditError('Could not connect to the site. Check the address and try again.')

    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(final_url))

    def robots():
        try:
            st, _, _, text = fetch(origin + '/robots.txt')
            return {'status': st, 'body': text if st == 200 else ''}
        except (AuditError, OSError, http.client.HTTPException):
            return {'status': None, 'body': ''}

    with ThreadPoolExecutor(max_workers=12) as pool:
        robots_job = pool.submit(robots)
        sitemap_job = pool.submit(fetch_status, origin + '/sitemap.xml')
        llms_job = pool.submit(fetch_status, origin + '/llms.txt')
        ua_jobs = {token: pool.submit(fetch_status, final_url, ua) for token, ua in BOT_UAS.items()}
        ua = {'browser': status}
        for token, job in ua_jobs.items():
            ua[token] = job.result()
        return {
            'url': url,
            'page': {
                'status': status,
                'finalUrl': final_url,
                'xRobotsTag': ', '.join(headers.get_all('X-Robots-Tag') or []),
                'body': body,
            },
            'robots': robots_job.result(),
            'sitemap': {'status': sitemap_job.result()},
            'llms': {'status': llms_job.result()},
            'ua': ua,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = 'Quotable/1.0'

    def send_json(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.send_json(200, {'ok': True})
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(ROOT, name), 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_json(404, {'error': 'Not found.'})

    def do_POST(self):
        if urlsplit(self.path).path != '/api/collect':
            return self.send_json(404, {'error': 'Not found.'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST:
            return self.send_json(400, {'error': 'Send a JSON body with a url.'})
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            raw_url = payload.get('url') if isinstance(payload, dict) else None
            if not isinstance(raw_url, str):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self.send_json(400, {'error': 'Send a JSON body with a url.'})
        try:
            self.send_json(200, collect(raw_url))
        except AuditError as e:
            self.send_json(200, {'error': str(e)})
        except Exception:
            self.send_json(200, {'error': 'Something went wrong while fetching that page.'})

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))


def main():
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer((host, port), Handler)
    print('Quotable is running at http://%s:%d' % ('localhost' if host == '127.0.0.1' else host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
