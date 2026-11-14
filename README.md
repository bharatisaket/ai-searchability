# ai-searchability

**Quotable** is an AI search audit. It checks whether AI search engines (Google AI Overviews, Copilot/Bing, ChatGPT search, Claude, Perplexity) can reach a page and quote it. Every finding cites the engine's own documentation.

Live app: https://bharatisaket.github.io/ai-searchability/

## What is here

| File | Purpose |
| --- | --- |
| `index.html` | The app UI. Renders the report and an example audit. |
| `engine.js` | The audit engine: one pure function that turns fetched page material into a report. |
| `server.py` | Live-audit backend. Serves the app and fetches pages for it. |
| `tests/test_server.py` | Server tests, run against a local test site. |
| `docs/SPEC.md` | The spec: sources, the 26 audit rules, scoring model and limits. |

## Using it

The hosted app runs in **paste mode**: enter the page URL, paste the page source and the site's `robots.txt`, and run the audit. It cannot fetch websites itself, so the firewall test and sitemap lookup are skipped.

## Live audits

`server.py` adds **live mode**: it fetches the page, `robots.txt`, `sitemap.xml` and `llms.txt`, then requests the page once as each AI crawler to spot firewall blocks. It needs Python 3.8 or later and nothing else.

```bash
python3 server.py
```

Open http://localhost:8000 and the app switches to live mode on its own.

The server refuses private and local network addresses, follows at most five redirects, and only serves `index.html` and `engine.js`. To host it, run it anywhere that runs Python with `HOST=0.0.0.0` and the platform's `PORT`. It has no rate limiting, so add one before sharing a hosted link widely.

Tests:

```bash
python3 -m unittest discover tests
```

The score reports access and readability. It does not predict rankings or citations.
