# AI Searchability Audit: Spec

Nov 15, 2026

## What it is

A website owner enters one page URL and gets three things back: which AI search engines can reach the page, a 0 to 100 page score, and a ranked fix list. Every check cites the engine's own documentation, so the owner can verify each finding.

The audit answers two separate questions, and keeps them apart:

1. **Can each engine get in?** Crawler rules in robots.txt, firewall blocks, and index or snippet opt-outs. A failure here makes everything else irrelevant for that engine.
2. **Is the page easy to read and quote?** Text in the HTML, title and heading alignment, answer-shaped structure, schema.

The positioning is honesty. Google states that AI search needs no special files or markup and warns against tools promising AI ranking gains. So the app reports access and readability facts, and never predicts rankings or citations.

## Sources

Six of the ten guides were read in full and drive the automated checks, and one more was used from search extracts. Three could not be opened and feed only the manual checklist. The post's short links did not resolve, so each guide was located by title.

| # | Guide | What it contributes | Read |
| --- | --- | --- | --- |
| 1 | [Google: AI features and your website](https://developers.google.com/search/docs/appearance/ai-features) | Index and snippet eligibility, preview controls, text-form content, schema must match visible text | Yes |
| 2 | [Google: Succeeding in AI search](https://developers.google.com/search/blog/2025/05/succeeding-in-ai-search) and its 2026 successor, [Optimizing for generative AI features](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide) | Semantic HTML, page experience, images and video, plus the list of things to ignore | Successor read in full; 2025 post via search extract |
| 3 | [Google: SEO Starter Guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide) | Titles, descriptions, headings, alt text, sitemaps | Search extract only |
| 4 | [Microsoft: Content for AI search answers](https://about.ads.microsoft.com/en/blog/post/october-2025/optimizing-your-content-for-inclusion-in-ai-search-answers) | Title, H1 and description alignment; descriptive headings; Q&A, lists, tables; mistakes to avoid | Yes |
| 5 | Bing: Webmaster Guidelines | Sitemaps, IndexNow, Bing Webmaster Tools | No. Page blocked for my reader; rules taken from summaries and marked manual |
| 6 | [OpenAI: Overview of OpenAI crawlers](https://developers.openai.com/api/docs/bots) | OAI-SearchBot, ChatGPT-User, GPTBot tokens and effects | Yes |
| 7 | [Anthropic: How Claude crawls the web](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-the-web) | Claude-SearchBot, Claude-User, ClaudeBot tokens and effects | Yes |
| 8 | [Perplexity: Perplexity crawlers](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) | PerplexityBot, Perplexity-User, firewall guidance | Yes |
| 9 | [LinkedIn: Unlocking AI Visibility](https://business.linkedin.com/advertise/resources/unlocking-ai-visibility) | B2B off-site presence | Landing page only. The guide is a gated PDF |
| 10 | Searchable: ChatGPT Business Hub masterclass | Claiming a business profile in ChatGPT | No. Covered from [news reports](https://qz.com/openai-chatgpt-business-profiles-ai-agents-111126) of the 10 November 2026 launch |

If you can share the LinkedIn PDF and the Bing page text, their rules can move from manual to sourced.

## Audit rules

The audit runs 29 automated checks in six groups. Access checks set each engine's status. The other five groups add up to the 100-point page score.

### Access (per engine, not scored)

| ID | Check | Source |
| --- | --- | --- |
| A1 | robots.txt is reachable and parses | Google 1, OpenAI 6 |
| A2 | Each search and retrieval bot may fetch this URL under robots.txt: Googlebot, Bingbot, OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User, PerplexityBot | Guides 1, 5, 6, 7, 8 |
| A3 | The page answers each bot's user-agent with the same status a browser gets. A 401, 403 or 429 for the bot alone means a firewall or CDN block | Google 1 (CDN and hosting), Perplexity 8 (WAF) |
| A4 | Training bots are reported as the owner's choice, never as a fault: GPTBot, ClaudeBot, Google-Extended | Guides 1, 6, 7 |
| A5 | A made-up address on the site returns 404 or 410. A 200 means error pages look like real content (a soft 404) | Google: HTTP status codes and soft 404 errors |
| A6 | The page loads after at most two server-side redirects, with no meta refresh or JavaScript redirect | Google: Redirects and Google Search |
| A7 | The page is readable without signing in: no 401 or 403, no redirect to a sign-in page, no password field in place of content | Google 1 |

A5 to A7 appear in the Checks list as an unscored Access group. A failed A7 is a blocker and sets every engine to Sign-in required. Failed A5 and A6 rank in the fix list above the scored checks.

### Indexability (25 points)

| ID | Check | Points | Source |
| --- | --- | --- | --- |
| I1 | Final HTTP status is 200 | 10 | Google 1, 2 |
| I2 | No noindex in the robots meta tag or X-Robots-Tag header | 8 | Google 1 |
| I3 | No nosnippet and no max-snippet:0, which remove the page from AI answers. A max-snippet under 50 characters earns half | 5 | Google 1 |
| I4 | Canonical is absent or points to this page | 2 | Google 2 (duplicates) |

### Readable content (20 points)

| ID | Check | Points | Source |
| --- | --- | --- | --- |
| R1 | At least 150 words of body text exist in the raw HTML, before any JavaScript runs | 10 | Google 1 (text form), Microsoft 4 |
| R2 | At least 80% of content images carry alt text | 4 | Microsoft 4, Google 3 |
| R3 | No more than a quarter of the text sits inside collapsed details elements | 3 | Microsoft 4 |
| R4 | The page uses a main or article landmark | 3 | Google 2 (semantic HTML) |

### Answer structure (35 points)

| ID | Check | Points | Source |
| --- | --- | --- | --- |
| S1 | Title exists, 15 to 65 characters | 5 | Microsoft 4, Google 3 |
| S2 | Meta description exists, 50 to 160 characters | 4 | Microsoft 4, Google 3 |
| S3 | Exactly one H1 | 5 | Microsoft 4 |
| S4 | Title and H1 share at least half their meaningful words | 4 | Microsoft 4 |
| S5 | Pages over 300 words have at least two H2 or H3 headings | 5 | Microsoft 4, Google 2 |
| S6 | No vague headings such as Learn more or Click here | 3 | Microsoft 4 |
| S7 | At least one list or table | 3 | Microsoft 4 |
| S8 | At least one heading phrased as a question | 2 | Microsoft 4 |
| S9 | No paragraph over 120 words | 3 | Microsoft 4 |
| S10 | No decorative symbols or repeated punctuation in title and headings | 1 | Microsoft 4 |

### Schema (10 points)

| ID | Check | Points | Source |
| --- | --- | --- | --- |
| M1 | JSON-LD is present and parses | 5 | Microsoft 4 |
| M2 | The schema's name or headline appears in the visible title or H1 | 5 | Google 1 |

### Discovery (10 points)

| ID | Check | Points | Source |
| --- | --- | --- | --- |
| D1 | A sitemap is declared in robots.txt or served at /sitemap.xml | 5 | Bing 5, Google 3 |
| D2 | The page links to at least three other pages on the same site | 5 | Google 1 |

### Manual checklist (ticked by the owner, not scored)

These live in accounts the app cannot see.

- Site verified in Google Search Console, with the generative AI control set to include
- Site added to Bing Webmaster Tools, sitemap submitted, IndexNow enabled
- Business profile claimed in ChatGPT Business Hub
- Google Business Profile and Merchant Center details current
- For B2B: LinkedIn company page active, and named experts publishing structured posts

## Scoring model

The report leads with engine access, then the page score, because a blocked engine cannot be fixed by better content.

**Engine access** has four states per engine:

| State | Rule |
| --- | --- |
| Open | Every search and retrieval bot for that engine is allowed, and the page is indexable |
| Partly open | The indexing bot is allowed but the user-fetch bot is blocked, or the reverse |
| Blocked | robots.txt disallows the indexing bot for this URL. A failed firewall test shows as Check firewall, since only server logs can confirm it |
| Not indexable | Shown as Opted out. Applies to Google and Bing when the page returns noindex or a non-200 status, and to Google when snippets are off |

| Engine | Indexing bot | User-fetch bot |
| --- | --- | --- |
| Google AI Overviews and AI Mode | Googlebot | none |
| Microsoft Copilot and Bing | Bingbot | none |
| ChatGPT search | OAI-SearchBot | ChatGPT-User |
| Claude | Claude-SearchBot | Claude-User |
| Perplexity | PerplexityBot | Perplexity-User |

Two documented quirks are shown as notes. OpenAI says robots.txt may not apply to ChatGPT-User, and Perplexity says Perplexity-User generally ignores it, since a person triggered each fetch.

**Page score** is the sum of points earned across the five scored groups: Indexability 25, Readable content 20, Answer structure 35, Schema 10, Discovery 10. A check that cannot run, such as an image check on a page with no images, awards its points. Bands: 85 and above is strong, 65 to 84 is fair, below 65 needs work.

**Fix list** order: access blockers first, then failed checks by points lost.

## Site-wide mode

In live mode the owner can audit the whole site instead of one page.

1. The server fetches the entered page and tests each bot user-agent against it.
2. It reads the sitemaps declared in robots.txt, or /sitemap.xml, following one level of sitemap index (four files at most).
3. It drops other hosts, files such as PDFs and images, legal and account pages, translations of pages already listed, and anything robots.txt disallows for all crawlers.
4. It picks up to nine more pages, taking turns across the site's top-level sections, shortest addresses first. With no sitemap it uses the links on the entered page.
5. Each page gets the full page audit. The site report shows the average score, each engine's worst state with the number of pages affected, a fix list grouped by check with the pages each fix applies to, and a table of pages that opens each page's own report.

The firewall test runs on the entered page only, to keep the number of requests to the site small. A site-wide audit counts as three audits against the rate limit.

## What the app does not score

Google's 2026 guide lists tactics that have no effect on its AI features. The app reports the first as information and leaves the rest out, so the score cannot be gamed with busywork.

- **llms.txt**: detected and shown, worth zero points. Google says Search does not use it; other services may.
- **Chunking content into small pieces**: Google says there is no ideal page length. Microsoft does favour self-contained passages, so S9 flags only very long paragraphs.
- **Special AI schema**: none exists. Schema earns 10 points because Microsoft recommends it and Google requires it to match the page.
- **Keyword variants written for AI, and bought mentions**: not checked.

Where Google and Microsoft differ, the app follows the stricter structural advice but keeps the weight small.

## Architecture and limits

The prototype is three files with no dependencies, and 29 automated tests pass against a local test site.

1. `server.py` fetches the page, robots.txt, sitemap.xml and llms.txt, then requests the page once per bot user-agent. It refuses private network addresses.
2. `engine.js` turns that bundle into the report. It is one pure function and runs in the browser.
3. `index.html` renders the report. With no server behind it, it switches to paste mode.

Limits to state in any demo:

- **Not yet run on real websites.** My workspace cannot reach the open web, so testing used a local site built to trigger each rule. Run it on five real sites before showing it.
- **Site-wide mode samples.** It audits at most 10 pages, not the whole site.
- **The firewall test is a hint.** Sites that verify crawler IP addresses can refuse the test request while admitting the real bot.
- **Raw HTML only.** It does not run JavaScript, which is the point of check R1 but undercounts pages Google can still render.
- **Thresholds are my judgement.** The guides give principles; numbers such as 150 words or 120-word paragraphs are starting values to tune.
- **The hosted preview cannot fetch sites.** It offers paste mode and an example report.

## Next steps

- [ ] Run the app on five real sites and tune the thresholds that misfire
- [x] Host the server so the audit works from a shared link
- [x] Add site-wide mode: read the sitemap and audit a sample of pages
- [ ] Add AI rewrite suggestions for failed structure checks
- [ ] Get the LinkedIn PDF and Bing guideline text, and convert their manual items into sourced checks
- [ ] Write the portfolio case study: problem, the Google versus Microsoft tension, and why the tool refuses to predict rankings
