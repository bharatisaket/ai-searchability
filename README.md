# ai-searchability

**Quotable** is an AI search audit. It checks whether AI search engines (Google AI Overviews, Copilot/Bing, ChatGPT search, Claude, Perplexity) can reach a page and quote it. Every finding cites the engine's own documentation.

Live app: https://bharatisaket.github.io/ai-searchability/

## What is here

| File | Purpose |
| --- | --- |
| `index.html` | The app UI. Renders the report and an example audit. |
| `engine.js` | The audit engine: one pure function that turns fetched page material into a report. |
| `docs/SPEC.md` | The spec: sources, the 26 audit rules, scoring model and limits. |

## Using it

The hosted app runs in **paste mode**: enter the page URL, paste the page source and the site's `robots.txt`, and run the audit. It cannot fetch websites itself, so the firewall test and sitemap lookup are skipped.

Live mode (fetching the page and testing each crawler user-agent) needs the `server.py` backend described in the spec. That file is not in this repository yet.

To run locally, serve the folder with any static server:

```bash
python3 -m http.server 8000
```

The score reports access and readability. It does not predict rankings or citations.
