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


def start(handler):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d' % httpd.server_address[1]


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site, cls.site_url = start(Site)
        cls.app, cls.app_url = start(type('Quiet', (server.Handler,), {'log_message': lambda *a: None}))

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.app.shutdown()

    def setUp(self):
        os.environ['QUOTABLE_ALLOW_PRIVATE'] = '1'

    def tearDown(self):
        os.environ.pop('QUOTABLE_ALLOW_PRIVATE', None)

    def post(self, url):
        req = urllib.request.Request(self.app_url + '/api/collect', json.dumps({'url': url}).encode(),
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

    def test_rejects_bad_requests(self):
        for body in (b'', b'not json', b'{"url": 5}', b'[]'):
            req = urllib.request.Request(self.app_url + '/api/collect', body, {'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)


if __name__ == '__main__':
    unittest.main()
