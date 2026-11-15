# ai-searchability

**Quotable** is an AI search audit. It checks whether AI search engines (Google AI Overviews, Copilot/Bing, ChatGPT search, Claude, Perplexity) can reach a page and quote it. Every finding cites the engine's own documentation.

Live app: https://quotable-6j8a.onrender.com/ (runs live audits; the first load after a pause can take up to a minute)

Paste-only copy: https://bharatisaket.github.io/ai-searchability/

## What is here

| File | Purpose |
| --- | --- |
| `index.html` | The app UI. Renders the report and an example audit. |
| `engine.js` | The audit engine: one pure function that turns fetched page material into a report. |
| `server.py` | Live-audit backend. Serves the app and fetches pages for it. |
| `tests/test_server.py` | Server tests, run against a local test site. |
| `docs/SPEC.md` | The spec: sources, the 34 audit rules, scoring model and limits. |

## Using it

The hosted app runs in **paste mode**: enter the page URL, paste the page source and the site's `robots.txt`, and run the audit. It cannot fetch websites itself, so the firewall test and sitemap lookup are skipped.

Reports can be shared: after a live audit the address bar holds a link that reruns the same audit when opened. The app also remembers the last report for each address in your browser and shows what changed when you audit it again.

## Live audits

`server.py` adds **live mode**: it fetches the page, `robots.txt`, `sitemap.xml` and `llms.txt`, then requests the page once as each AI crawler to spot firewall blocks. It needs Python 3.8 or later and nothing else.

```bash
python3 server.py
```

Open http://localhost:8000 and the app switches to live mode on its own.

Live mode also offers **Whole site**: it reads the sitemap, audits every page it lists, and groups the fixes by how many pages they affect. It works through the site ten pages at a time and can be stopped early. One audit covers at most 500 pages (set `SITE_MAX_PAGES` to change that). The crawler firewall test runs on the page you entered only.

The server refuses private and local network addresses, follows at most five redirects, and only serves `index.html` and `engine.js`. To host it, run it anywhere that runs Python with `HOST=0.0.0.0` and the platform's `PORT`.

Audits are rate limited: 10 per minute per visitor and 60 per minute across everyone. A whole-site audit counts as three, and its page fetches are limited to 120 pages per minute per visitor and 600 across everyone (`RATE_LIMIT_PAGES_PER_CLIENT`, `RATE_LIMIT_PAGES_GLOBAL`). Change these with the `RATE_LIMIT_PER_CLIENT` and `RATE_LIMIT_GLOBAL` environment variables. Counts are kept in memory, so they reset when the server restarts.

### Hosting on Render

`render.yaml` is a Render Blueprint for a free web service.

1. Sign in at https://dashboard.render.com and choose **New**, then **Blueprint**.
2. Connect this GitHub repository and apply the Blueprint.
3. Render builds the service and gives it an `onrender.com` address. Open it and the app runs in live mode.

Free services sleep after about 15 minutes without traffic, so the first visit after a pause takes up to a minute to load. Each push to `main` redeploys.

Tests:

```bash
python3 -m unittest discover tests
```

The score reports access and readability. It does not predict rankings or citations.
