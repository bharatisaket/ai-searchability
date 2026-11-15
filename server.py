#!/usr/bin/env python3
"""
Quotable live-audit server. Standard library only.

Serves the app (index.html, engine.js) and two API routes:

  GET  /api/health   -> {"ok": true}
  POST /api/collect  -> body {"url": "https://example.com/page"}
                        returns the bundle engine.js audits:
                        { url, page, robots, sitemap, llms, notFound, home, ua }
  POST /api/collect-stream -> same body and bundle as /api/collect, sent as
                        one JSON object per line: a {"step": ...} line as each
                        lookup finishes, then {"done": bundle} or {"error": ...}
  POST /api/collect-site -> same body; fetches the entered page and lists
                        the rest of the site from its sitemap (or the page's
                        links): { url, origin, shared, sampled, pages, queue }
  POST /api/collect-pages -> body {"urls": [...]} (10 at most); fetches the
                        pages in a site's queue: { pages, errors }

Run:  python3 server.py            (http://localhost:8000)
Env:  PORT (default 8000), HOST (default 127.0.0.1; use 0.0.0.0 when hosting)
      RATE_LIMIT_PER_CLIENT (default 10) audits per minute per visitor
      RATE_LIMIT_GLOBAL (default 60) audits per minute across all visitors
      RATE_LIMIT_PAGES_PER_CLIENT (default 120) and RATE_LIMIT_PAGES_GLOBAL
      (default 600) site pages fetched per minute
      SITE_MAX_PAGES (default 500) pages in one whole-site audit
      TRUST_PROXY=1 reads the visitor address from X-Forwarded-For (set
      automatically on Render, where every request arrives through its proxy)
      QUOTABLE_ALLOW_PRIVATE=1 lets it fetch private addresses (tests only).
"""
import gzip
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/engine.js': ('engine.js', 'text/javascript; charset=utf-8'),
}

TIMEOUT = 10            # seconds per request
MAX_BODY = 3_000_000    # bytes of page or robots.txt kept
MAX_REDIRECTS = 5
MAX_REQUEST = 32768     # bytes of POST body accepted
BATCH = 10              # pages fetched per /api/collect-pages call
SITE_BODY = 1_000_000   # characters of each page kept in site-wide mode
SITE_COST = 3           # a site-wide audit counts as this many audits for rate limiting
MAX_SITEMAPS = 50       # sitemap files read per site
MAX_SITEMAP_URLS = 50000

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


def fetch(url, user_agent=BROWSER_UA, read_body=True, hops=None):
    """GET url, following redirects. Returns (status, final_url, headers, text).

    Each redirect followed is appended to hops as {status, url}.
    """
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, body = request_once(url, user_agent, read_body)
        location = headers.get('Location')
        if status in (301, 302, 303, 307, 308) and location:
            if hops is not None:
                hops.append({'status': status, 'url': url})
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


def fetch_page(url):
    """Fetch one page as a browser. Returns the page part of a bundle."""
    hops = []
    status, final_url, headers, body = fetch(url, hops=hops)
    return {
        'status': status,
        'finalUrl': final_url,
        'xRobotsTag': ', '.join(headers.get_all('X-Robots-Tag') or []),
        'body': body,
        'redirects': hops,
    }


def fetch_entry(url):
    """Fetch the page the person entered, turning failures into readable errors."""
    try:
        return fetch_page(url)
    except ssl.SSLError:
        raise AuditError('The site has a certificate problem, so the page could not be fetched securely.')
    except socket.timeout:
        raise AuditError('The site took too long to answer.')
    except (OSError, http.client.HTTPException):
        raise AuditError('Could not connect to the site. Check the address and try again.')


def origin_of(url):
    return '{0.scheme}://{0.netloc}'.format(urlsplit(url))


def fetch_robots(origin):
    try:
        st, _, _, text = fetch(origin + '/robots.txt')
        return {'status': st, 'body': text if st == 200 else ''}
    except (AuditError, OSError, http.client.HTTPException):
        return {'status': None, 'body': ''}


def start_shared(pool, origin):
    """Start the site-level lookups every audit needs. Returns a function that waits for them."""
    jobs = {
        'robots': pool.submit(fetch_robots, origin),
        'sitemap': pool.submit(fetch_status, origin + '/sitemap.xml'),
        'llms': pool.submit(fetch_status, origin + '/llms.txt'),
        # A made-up address: a site that answers 200 here serves "soft 404" pages.
        'notFound': pool.submit(fetch_status, origin + '/quotable-check-' + secrets.token_hex(6)),
    }

    def result():
        return {
            'robots': jobs['robots'].result(),
            'sitemap': {'status': jobs['sitemap'].result()},
            'llms': {'status': jobs['llms'].result()},
            'notFound': {'status': jobs['notFound'].result()},
        }
    return result


def probe_bots(pool, url, browser_status):
    jobs = {token: pool.submit(fetch_status, url, ua) for token, ua in BOT_UAS.items()}
    ua = {'browser': browser_status}
    for token, job in jobs.items():
        ua[token] = job.result()
    return ua


def is_home(url):
    parts = urlsplit(url)
    return parts.path in ('', '/') and not parts.query


def fetch_home(origin):
    """The site's home page, used for the checks on who runs the site."""
    try:
        status, _, _, text = fetch(origin + '/')
        return {'status': status, 'body': text[:SITE_BODY] if status == 200 else ''}
    except (AuditError, OSError, http.client.HTTPException):
        return {'status': None, 'body': ''}


def collect(raw_url, emit=None):
    """Fetch everything one page audit needs. emit, if given, is called with a step as each lookup finishes."""
    emit = emit or (lambda step: None)
    url = normalise_url(raw_url)
    page = fetch_entry(url)
    final = page['finalUrl']
    origin = origin_of(final)
    emit({'step': 'page', 'status': page['status'], 'redirects': len(page['redirects'])})
    with ThreadPoolExecutor(max_workers=12) as pool:
        jobs = {
            pool.submit(fetch_robots, origin): ('robots', None),
            pool.submit(fetch_status, origin + '/sitemap.xml'): ('sitemap', None),
            pool.submit(fetch_status, origin + '/llms.txt'): ('llms', None),
            # A made-up address: a site that answers 200 here serves "soft 404" pages.
            pool.submit(fetch_status, origin + '/quotable-check-' + secrets.token_hex(6)): ('notFound', None),
        }
        # When the audited page is the home page, the engine reads these facts from the page itself.
        if not is_home(final):
            jobs[pool.submit(fetch_home, origin)] = ('home', None)
        for token, agent in BOT_UAS.items():
            jobs[pool.submit(fetch_status, final, agent)] = ('bot', token)
        bundle = {'url': url, 'page': page, 'ua': {'browser': page['status']}, 'home': None}
        for job in as_completed(jobs):
            kind, token = jobs[job]
            value = job.result()
            if kind == 'bot':
                bundle['ua'][token] = value
                emit({'step': 'bot', 'token': token, 'status': value})
            elif kind in ('robots', 'home'):
                bundle[kind] = value
                emit({'step': kind, 'status': value['status']})
            else:
                bundle[kind] = {'status': value}
                emit({'step': kind, 'status': value})
        bundle['ua'] = dict([('browser', page['status'])] + [(t, bundle['ua'][t]) for t in BOT_UAS])
        return bundle


# ---------- site-wide mode ----------

NON_PAGE = re.compile(r'\.(pdf|png|jpe?g|gif|webp|svg|ico|zip|gz|xml|json|txt|csv|mp4|mp3|css|js|woff2?)$', re.I)
BOILERPLATE = re.compile(r'(^|-)(careers?|jobs?|legal|privacy|terms|tos|cookies?|polic(y|ies)|disclaimer|login|signin|signup|cart|checkout)(-|$)', re.I)
LOCALE = re.compile(r'^[a-z]{2}([-_][a-z]{2,4})?$', re.I)
LOC = re.compile(r'<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)', re.I)


def bare_host(url):
    host = (urlsplit(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def page_key(url):
    parts = urlsplit(url)
    return bare_host(url) + (parts.path.rstrip('/') or '/') + ('?' + parts.query if parts.query else '')


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)


def page_links(html, base):
    parser = LinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    return [urljoin(base, href) for href in parser.links]


def read_sitemap_text(url):
    """Text of one sitemap file, or '' if it cannot be read."""
    try:
        status, _, headers, body = request_sitemap(url)
    except (AuditError, OSError, http.client.HTTPException):
        return ''
    if status != 200:
        return ''
    if body[:2] == b'\x1f\x8b':  # a .xml.gz file
        try:
            body = zlib.decompressobj(31).decompress(body, MAX_BODY)
        except zlib.error:
            return ''
    return body.decode('utf-8', errors='replace')


def request_sitemap(url):
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, body = request_once(url, BROWSER_UA, True)
        location = headers.get('Location')
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        return status, url, headers, body
    return 0, url, None, b''


def sitemap_urls(origin, robots_body):
    """Page addresses listed in the site's sitemaps."""
    declared = [line.split(':', 1)[1].strip() for line in robots_body.splitlines()
                if line.lower().startswith('sitemap:')]
    queue = [u for u in declared if u.startswith(('http://', 'https://'))] or [origin + '/sitemap.xml']
    seen, urls, read = set(), [], 0
    while queue and read < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        read += 1
        text = read_sitemap_text(url)
        locs = LOC.findall(text)
        if '<sitemapindex' in text.lower():
            queue.extend(locs)
        else:
            urls.extend(locs[:MAX_SITEMAP_URLS - len(urls)])
    return urls


def choose_pages(candidates, entry_url, robots_body, limit):
    """Pick up to limit pages, spread across the site's sections. Returns (chosen, eligible count)."""
    rules = RobotFileParser()
    rules.parse(robots_body.splitlines())
    host, entry = bare_host(entry_url), page_key(entry_url)
    pages = {}
    for url in candidates:
        parts = urlsplit(url.strip())
        if parts.scheme not in ('http', 'https') or bare_host(url) != host:
            continue
        clean = parts._replace(fragment='').geturl()
        key = page_key(clean)
        segments = [seg for seg in parts.path.lower().split('/') if seg]
        if key == entry or key in pages or NON_PAGE.search(parts.path):
            continue
        if any(BOILERPLATE.search(seg) for seg in segments):
            continue
        if not rules.can_fetch('*', clean):
            continue
        pages[key] = (clean, segments)
    # Translations repeat the default-language pages, so skip them.
    paths = {'/'.join(segments) for _, segments in pages.values()}
    paths.add('/'.join(seg for seg in urlsplit(entry_url).path.lower().split('/') if seg))
    kept = [(url, segments) for url, segments in pages.values()
            if not (segments and LOCALE.match(segments[0]) and '/'.join(segments[1:]) in paths)]
    sections = {}
    for url, segments in kept:
        sections.setdefault(segments[0] if len(segments) > 1 else '', []).append((len(segments), len(url), url))
    for group in sections.values():
        group.sort()
    order = sorted(sections, key=lambda name: (name != '', -len(sections[name]), name))
    chosen = []
    while len(chosen) < limit and any(sections.values()):
        for name in order:
            if sections[name] and len(chosen) < limit:
                chosen.append(sections[name].pop(0)[2])
    return chosen, len(kept)


def collect_site(raw_url):
    url = normalise_url(raw_url)
    entry = fetch_entry(url)
    origin = origin_of(entry['finalUrl'])
    with ThreadPoolExecutor(max_workers=12) as pool:
        shared_job = start_shared(pool, origin)
        home_job = None if is_home(entry['finalUrl']) else pool.submit(fetch_home, origin)
        ua = probe_bots(pool, entry['finalUrl'], entry['status'])
        shared = shared_job()
        shared['home'] = home_job.result() if home_job else {'status': entry['status'], 'body': entry['body'][:SITE_BODY]}

    robots_body = shared['robots']['body']
    listed = sitemap_urls(origin, robots_body)
    source = 'sitemap'
    limit = site_max_pages() - 1
    queue, eligible = choose_pages(listed, entry['finalUrl'], robots_body, limit)
    if not queue:
        source = 'links'
        queue, eligible = choose_pages(page_links(entry['body'], entry['finalUrl']), entry['finalUrl'], robots_body, limit)

    entry['body'] = entry['body'][:SITE_BODY]
    return {
        'url': url,
        'origin': origin,
        'shared': shared,
        'sampled': {'source': source if queue else 'none', 'listed': len(listed), 'eligible': eligible + 1,
                    'capped': eligible > len(queue), 'batch': BATCH},
        'pages': [{'url': url, 'page': entry, 'ua': ua}],
        'queue': queue,
    }


def site_max_pages():
    return env_int('SITE_MAX_PAGES', 500)


def collect_pages(urls):
    """Fetch a batch of pages from a site's queue."""
    def one(raw):
        try:
            page_url = normalise_url(raw)
            page = fetch_page(page_url)
            page['body'] = page['body'][:SITE_BODY]
            return {'url': page_url, 'page': page}
        except AuditError as e:
            return {'url': raw, 'error': str(e)}
        except (OSError, http.client.HTTPException):
            return {'url': raw, 'error': 'Could not fetch this page.'}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, urls))
    return {'pages': [r for r in results if 'page' in r], 'errors': [r for r in results if 'error' in r]}


class RateLimiter:
    """Sliding one-minute window, per visitor and across all visitors."""

    WINDOW = 60.0
    MAX_CLIENTS = 10000

    def __init__(self, per_client, overall):
        self.per_client = per_client
        self.overall = overall
        self.clients = {}
        self.everyone = deque()
        self.lock = threading.Lock()

    def check(self, client, now=None, cost=1):
        """Record an audit of the given cost. Returns 0 if allowed, else seconds to wait."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.WINDOW
        with self.lock:
            while self.everyone and self.everyone[0] <= cutoff:
                self.everyone.popleft()
            mine = self.clients.get(client)
            if mine is None:
                if len(self.clients) >= self.MAX_CLIENTS:
                    self.clients = {k: v for k, v in self.clients.items() if v and v[-1] > cutoff}
                mine = self.clients[client] = deque()
            while mine and mine[0] <= cutoff:
                mine.popleft()
            if mine and len(mine) + cost > self.per_client:
                return max(1, int(mine[0] - cutoff) + 1)
            if self.everyone and len(self.everyone) + cost > self.overall:
                return max(1, int(self.everyone[0] - cutoff) + 1)
            mine.extend([now] * cost)
            self.everyone.extend([now] * cost)
            return 0


def env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


LIMITER = RateLimiter(env_int('RATE_LIMIT_PER_CLIENT', 10), env_int('RATE_LIMIT_GLOBAL', 60))
PAGE_LIMITER = RateLimiter(env_int('RATE_LIMIT_PAGES_PER_CLIENT', 120), env_int('RATE_LIMIT_PAGES_GLOBAL', 600))


def trust_proxy():
    return os.environ.get('TRUST_PROXY') == '1' or bool(os.environ.get('RENDER'))


class Handler(BaseHTTPRequestHandler):
    server_version = 'Quotable/1.0'

    def client_id(self):
        if trust_proxy():
            forwarded = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            if forwarded:
                return forwarded[:64]
        return self.client_address[0]

    def send_json(self, code, payload, headers=None):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
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
        route = urlsplit(self.path).path
        if route not in ('/api/collect', '/api/collect-stream', '/api/collect-site', '/api/collect-pages'):
            return self.send_json(404, {'error': 'Not found.'})
        site = route == '/api/collect-site'
        batch = route == '/api/collect-pages'
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST:
            return self.send_json(400, {'error': 'Send a JSON body with a url.'})
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            raw_url = payload.get('url') if isinstance(payload, dict) else None
            urls = payload.get('urls') if isinstance(payload, dict) else None
            if batch:
                if not isinstance(urls, list) or not 1 <= len(urls) <= BATCH or not all(isinstance(u, str) for u in urls):
                    raise ValueError
            elif not isinstance(raw_url, str):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self.send_json(400, {'error': 'Send a JSON body with a url.'})
        if batch:
            wait = PAGE_LIMITER.check(self.client_id(), cost=len(urls))
        else:
            wait = LIMITER.check(self.client_id(), cost=SITE_COST if site else 1)
        if wait:
            return self.send_json(429, {'error': 'Too many audits right now. Try again in %d seconds.' % wait},
                                  {'Retry-After': str(wait)})
        if route == '/api/collect-stream':
            return self.stream(raw_url)
        try:
            self.send_json(200, collect_pages(urls) if batch else collect_site(raw_url) if site else collect(raw_url))
        except AuditError as e:
            self.send_json(200, {'error': str(e)})
        except Exception:
            self.send_json(200, {'error': 'Something went wrong while fetching that page.'})

    def stream(self, raw_url):
        """Answer with one JSON object per line, written as each lookup finishes."""
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')  # ask proxies not to hold lines back
        self.end_headers()

        def emit(obj):
            self.wfile.write(json.dumps(obj).encode('utf-8') + b'\n')
            self.wfile.flush()

        try:
            try:
                emit({'done': collect(raw_url, emit)})
            except AuditError as e:
                emit({'error': str(e)})
            except OSError:
                raise
            except Exception:
                emit({'error': 'Something went wrong while fetching that page.'})
        except OSError:
            pass  # the visitor closed the page

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
