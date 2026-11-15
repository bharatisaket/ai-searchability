"""Tests for server.py against a local test site. Run: python3 -m unittest discover tests"""
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

PAGE = '<html><head><title>Test page</title></head><body><h1>Test page</h1></body></html>'


class Site(BaseHTTPRequestHandler):
    """A site that blocks PerplexityBot at the firewall and redirects /old to /page."""

    def do_GET(self):
        ua = self.headers.get('User-Agent', '')
        if self.path == '/old':
            self.send_response(301)
            self.send_header('Location', '/page')
            self.end_headers()
            return
        if self.path == '/loop':
            self.send_response(302)
            self.send_header('Location', '/loop')
            self.end_headers()
            return
        if self.path == '/page':
            if 'PerplexityBot' in ua:
                return self.reply(403, 'blocked')
            return self.reply(200, PAGE, {'X-Robots-Tag': 'nosnippet', 'Content-Type': 'text/html; charset=utf-8'})
        if self.path == '/robots.txt':
            return self.reply(200, 'User-agent: *\nDisallow: /private/\n')
        if self.path == '/llms.txt':
            return self.reply(200, '# site')
        self.reply(404, 'missing')

    def reply(self, code, text, headers=None):
        data = text.encode('utf-8')
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def article(title):
    return ('<html><head><title>%s guide for testing</title></head><body><main><h1>%s guide</h1>'
            '<a href="/blog/one">One</a><a href="/docs/start">Start</a><a href="/pricing">Pricing</a>'
            '<p>%s</p></main></body></html>') % (title, title, 'Plain words about the topic. ' * 40)


class BigSite(BaseHTTPRequestHandler):
    """A site with a sitemap, a soft 404, a redirect chain and a sign-in wall."""

    PAGES = ['/', '/pricing', '/blog/one', '/blog/two', '/blog/three', '/docs/start', '/docs/api',
             '/app/dashboard', '/chain']
    SKIPPED = ['/privacy', '/fr/pricing', '/files/report.pdf', '/secret/plan']

    def do_GET(self):
        base = 'http://127.0.0.1:%d' % self.server.server_address[1]
        path = self.path
        if path == '/robots.txt':
            return self.reply(200, 'User-agent: *\nDisallow: /secret/\nSitemap: %s/sitemap-index.xml\n' % base)
        if path == '/sitemap-index.xml':
            return self.reply(200, '<sitemapindex><sitemap><loc>%s/sitemap-pages.xml</loc></sitemap></sitemapindex>' % base)
        if path == '/sitemap-pages.xml':
            locs = ''.join('<url><loc>%s%s</loc></url>' % (base, p) for p in self.PAGES + self.SKIPPED)
            locs += '<url><loc>https://elsewhere.example/page</loc></url>'
            return self.reply(200, '<urlset>%s</urlset>' % locs)
        if path == '/app/dashboard':
            return self.redirect('/login?next=/app/dashboard')
        if path.startswith('/login'):
            return self.reply(200, '<html><head><title>Sign in</title></head><body><form><input type="password"></form></body></html>')
        if path == '/chain':
            return self.redirect('/chain2')
        if path == '/chain2':
            return self.redirect('/chain3')
        if path == '/chain3':
            return self.redirect('/pricing')
        if path in self.SKIPPED:
            return self.reply(500, 'this page should never be fetched')
        if path in self.PAGES:
            return self.reply(200, article(path.strip('/').replace('/', ' ') or 'home'), {'Content-Type': 'text/html'})
        # Soft 404: unknown addresses answer 200.
        self.reply(200, '<html><body><h1>Oops, nothing here</h1></body></html>')

    def redirect(self, to):
        self.send_response(302)
        self.send_header('Location', to)
        self.end_headers()

    reply = Site.reply

    def log_message(self, *args):
        pass


def start(handler):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d' % httpd.server_address[1]


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site, cls.site_url = start(Site)
        cls.big, cls.big_url = start(BigSite)
        cls.app, cls.app_url = start(type('Quiet', (server.Handler,), {'log_message': lambda *a: None}))

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.big.shutdown()
        cls.app.shutdown()

    def setUp(self):
        os.environ['QUOTABLE_ALLOW_PRIVATE'] = '1'
        server.LIMITER = server.RateLimiter(1000, 1000)

    def tearDown(self):
        os.environ.pop('QUOTABLE_ALLOW_PRIVATE', None)
        os.environ.pop('TRUST_PROXY', None)

    def post(self, url, route='/api/collect'):
        req = urllib.request.Request(self.app_url + route, json.dumps({'url': url}).encode(),
                                     {'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req))

    def test_health(self):
        self.assertEqual(json.load(urllib.request.urlopen(self.app_url + '/api/health')), {'ok': True})

    def test_serves_app_but_not_other_files(self):
        self.assertIn(b'Quotable', urllib.request.urlopen(self.app_url + '/').read())
        self.assertIn(b'AuditEngine', urllib.request.urlopen(self.app_url + '/engine.js').read())
        for path in ('/server.py', '/.git/config', '/../server.py'):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(self.app_url + path)

    def test_collect_bundle(self):
        b = self.post(self.site_url + '/page')
        self.assertEqual(b['page']['status'], 200)
        self.assertEqual(b['page']['body'], PAGE)
        self.assertEqual(b['page']['xRobotsTag'], 'nosnippet')
        self.assertEqual(b['robots']['status'], 200)
        self.assertIn('Disallow: /private/', b['robots']['body'])
        self.assertEqual(b['sitemap']['status'], 404)
        self.assertEqual(b['llms']['status'], 200)

    def test_firewall_block_is_detected_per_bot(self):
        ua = self.post(self.site_url + '/page')['ua']
        self.assertEqual(ua['PerplexityBot'], 403)
        for token in ('browser', 'Googlebot', 'Bingbot', 'OAI-SearchBot', 'ChatGPT-User',
                      'Claude-SearchBot', 'Claude-User', 'Perplexity-User'):
            self.assertEqual(ua[token], 200, token)

    def test_follows_redirects(self):
        b = self.post(self.site_url + '/old')
        self.assertEqual(b['page']['status'], 200)
        self.assertTrue(b['page']['finalUrl'].endswith('/page'))

    def test_redirect_loop_is_an_error(self):
        self.assertIn('too many times', self.post(self.site_url + '/loop')['error'])

    def test_missing_page_reports_status(self):
        self.assertEqual(self.post(self.site_url + '/nope')['page']['status'], 404)

    def test_bad_addresses_are_errors(self):
        for url in ('ftp://example.com/', 'file:///etc/passwd', 'https://user:pw@example.com/', ''):
            self.assertIn('error', self.post(url), url)

    def test_refuses_private_addresses(self):
        os.environ.pop('QUOTABLE_ALLOW_PRIVATE', None)
        for url in (self.site_url + '/page', 'http://localhost/', 'http://10.0.0.1/', 'http://169.254.169.254/latest/',
                    'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://0.0.0.0/'):
            self.assertIn('private or local', self.post(url).get('error', ''), url)

    def test_public_ip_check(self):
        self.assertTrue(server.is_public_ip('93.184.216.34'))
        for ip in ('127.0.0.1', '10.1.2.3', '192.168.1.1', '172.16.0.1', '169.254.1.1', '100.64.0.1', '::1', 'fe80::1', 'fc00::1'):
            self.assertFalse(server.is_public_ip(ip), ip)

    def test_missing_page_probe(self):
        self.assertEqual(self.post(self.site_url + '/page')['notFound']['status'], 404)
        self.assertEqual(self.post(self.big_url + '/pricing')['notFound']['status'], 200)

    def test_redirect_hops_are_recorded(self):
        self.assertEqual(self.post(self.site_url + '/page')['page']['redirects'], [])
        hops = self.post(self.big_url + '/chain')['page']['redirects']
        self.assertEqual([h['status'] for h in hops], [302, 302, 302])
        self.assertTrue(hops[0]['url'].endswith('/chain'))

    def test_site_mode_samples_the_sitemap(self):
        site = self.post(self.big_url + '/', '/api/collect-site')
        urls = [p['url'] for p in site['pages']]
        paths = sorted(u.replace(self.big_url, '') for u in urls)
        self.assertEqual(paths, sorted(BigSite.PAGES))
        self.assertEqual(urls[0], self.big_url + '/')                # the entered page comes first
        self.assertEqual(site['sampled']['source'], 'sitemap')
        self.assertEqual(site['sampled']['listed'], len(BigSite.PAGES) + len(BigSite.SKIPPED) + 1)
        self.assertEqual(site['errors'], [])
        self.assertIn('ua', site['pages'][0])                        # bots are tested on the entered page only
        self.assertNotIn('ua', site['pages'][1])
        self.assertEqual(site['shared']['notFound']['status'], 200)
        self.assertEqual(site['shared']['robots']['status'], 200)
        login = [p for p in site['pages'] if p['url'].endswith('/app/dashboard')][0]
        self.assertIn('/login', login['page']['finalUrl'])

    def test_site_mode_falls_back_to_links(self):
        site = self.post(self.site_url + '/old', '/api/collect-site')
        self.assertEqual(site['sampled']['source'], 'none')         # the test page has no sitemap and no links
        self.assertEqual(len(site['pages']), 1)

    def test_choose_pages_spreads_across_sections_and_caps(self):
        base = 'https://example.com'
        urls = [base + '/blog/post-%d' % i for i in range(30)] + [base + '/docs/a', base + '/docs/b', base + '/about',
                base + '/de/about', base + '/terms', base + '/logo.png', 'https://other.example/x', base + '/#top']
        chosen, eligible = server.choose_pages(urls, base + '/', '', 6)
        self.assertEqual(len(chosen), 6)
        self.assertEqual(eligible, 33)
        self.assertIn(base + '/about', chosen)
        self.assertIn(base + '/docs/a', chosen)
        self.assertEqual(sum('/blog/' in u for u in chosen), 3)
        for bad in ('/de/about', '/terms', '/logo.png', 'other.example'):
            self.assertFalse(any(bad in u for u in chosen), bad)

    def test_site_audit_costs_more_against_the_rate_limit(self):
        server.LIMITER = server.RateLimiter(4, 1000)
        self.post(self.site_url + '/page', '/api/collect-site')      # costs 3
        self.post(self.site_url + '/page')                           # costs 1
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post(self.site_url + '/page')
        self.assertEqual(ctx.exception.code, 429)

    def test_rate_limit_per_client(self):
        server.LIMITER = server.RateLimiter(2, 1000)
        self.post(self.site_url + '/page')
        self.post(self.site_url + '/page')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post(self.site_url + '/page')
        self.assertEqual(ctx.exception.code, 429)
        self.assertGreaterEqual(int(ctx.exception.headers['Retry-After']), 1)
        self.assertIn('Too many audits', json.load(ctx.exception)['error'])

    def test_rate_limit_uses_forwarded_address_behind_proxy(self):
        os.environ['TRUST_PROXY'] = '1'
        server.LIMITER = server.RateLimiter(1, 1000)

        def post_as(ip):
            req = urllib.request.Request(self.app_url + '/api/collect', json.dumps({'url': self.site_url + '/page'}).encode(),
                                         {'Content-Type': 'application/json', 'X-Forwarded-For': ip + ', 10.0.0.9'})
            return urllib.request.urlopen(req).status

        self.assertEqual(post_as('203.0.113.5'), 200)
        self.assertEqual(post_as('203.0.113.6'), 200)
        with self.assertRaises(urllib.error.HTTPError):
            post_as('203.0.113.5')

    def test_rate_limiter_windows(self):
        rl = server.RateLimiter(2, 3)
        self.assertEqual(rl.check('a', 0), 0)
        self.assertEqual(rl.check('a', 1), 0)
        self.assertEqual(rl.check('a', 2), 59)          # a is over its own limit
        self.assertEqual(rl.check('b', 3), 0)
        self.assertEqual(rl.check('c', 4), 57)          # overall limit reached
        self.assertEqual(rl.check('a', 61), 0)          # window moved on
        self.assertEqual(rl.check('c', 61.5), 0)

    def test_rejects_bad_requests(self):
        for body in (b'', b'not json', b'{"url": 5}', b'[]'):
            req = urllib.request.Request(self.app_url + '/api/collect', body, {'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)


if __name__ == '__main__':
    unittest.main()
