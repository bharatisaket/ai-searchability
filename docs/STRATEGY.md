# Product Strategy: Quotable

**Date**: 15 November 2026
**Stage**: Working prototype. Deployed, no known users, never run against a real website.

Everything below about users and demand is a hypothesis. Nothing here has been tested with a real person. Where I have no data, I say so instead of inventing a number.

## 1. Vision

A site owner should be able to find out in one minute, without an account, whether AI search engines can read their pages, and trust the answer because every finding links to what the engine itself has published.

## 2. Target segments

| Segment | Pain | What they use today | Priority |
| --- | --- | --- | --- |
| Solo marketers and founders at small companies, who own the website but not the server | Told "we need to show up in ChatGPT", with no way to tell real problems from vendor noise | Blog posts, paid AEO tools with sales calls, guessing | Primary |
| Developers and agencies shipping client sites | A CDN or firewall rule silently blocks AI crawlers and nobody notices | Reading robots.txt by hand, curl with a fake user-agent | Secondary |
| SEO teams at larger companies | Need tracking over time, share of voice, reporting | Profound, Peec, Elmo, GetCito | Not serving |

**Primary segment and why**: they have the problem most acutely and the least ability to check it. They cannot run curl, and they cannot justify a monthly tool for a question they ask twice a year.

**Explicitly not serving**: teams who want to track what AI engines say about their brand over time. That needs paid providers, scheduled workers and a database. Elmo and GetCito already do it in the open.

Segment sizes: unknown. I have no data and will not estimate.

## 3. Pain points and value

- **Accidental blocks.** A robots.txt line written for training bots, or a CDN bot rule, also shuts out the search bots. The owner never sees it, because their browser works. Quotable shows which engine is blocked, by which rule, and the lines to add.
- **Noise.** The advice market is full of tactics with no evidence: llms.txt as a ranking factor, special AI schema, chunking. Quotable declines to score them and says why.
- **No way to verify.** Other tools give a score and ask for trust. Every Quotable finding links to the engine's own documentation.

## 4. Value propositions

**Small-company marketer**: When my boss asks why we are not in AI answers, I want to rule out technical blocks in a minute, so I can spend my time on content instead of guessing.

**Developer or agency**: When I ship or change a site, I want to confirm no crawler is locked out, so I do not find out three months later from a client.

## 5. Strategic trade-offs

| We choose | Over | Because |
| --- | --- | --- |
| Access and readability facts | Predicting rankings or citations | Nobody outside the engines can predict them, and saying otherwise is the thing this tool exists to oppose |
| Checks that cite a primary source | A longer checklist | A check with no source is an opinion; the count of checks is not the product |
| Repeatable rules | Model-judged quality scores | The same page must get the same result tomorrow, or the report cannot be acted on |
| No account, no database | History, teams, dashboards | The primary user asks this question rarely; a login wall loses them |
| One audit, done well | Answer tracking | That is a different product with real running costs |

## 6. Key metrics

None of these are measured today. The app has no analytics.

- **North Star**: re-audits where a blocker found earlier is now gone. It is the only number that shows the tool changed something.
- **Input metrics**: audits finished as a share of audits started; share of reports copied or shared; second audit of the same site within 14 days; whole-site audits that run to the end.
- **Health metrics**: findings reported as wrong; audits that fail to fetch; median time to first result; first-load time after the server has slept.

## 7. Growth engine

Hypotheses, in the order I would test them:

1. **Shareable report links.** Today a report disappears on reload. A link that reopens the audit is how a marketer hands it to their developer, which is the natural path for this tool to spread.
2. **One sharp entry point.** "Is your firewall blocking ChatGPT?" is a question people already search for. The firewall test is the one thing a blog post cannot do for them.
3. **A command-line and CI check for developers.** The engine is one dependency-free file. A build step that fails when a crawler gets locked out puts Quotable in the secondary segment's workflow.
4. **Published findings.** Once it has run on enough real sites: what share block each crawler, and how often by accident. That is original data, which gets cited.

## 8. Core capabilities

| Capability | Build / buy | State |
| --- | --- | --- |
| Rules that track each engine's documentation | Build | Done once; no process for noticing when a guide changes |
| Reliable fetching across real-world sites | Build | Untested on real sites. This is the biggest gap |
| A report a non-technical person can act on | Build | Partly. See the UI section |
| Hosting that answers quickly | Buy | Free tier sleeps; first load can take a minute |

## 9. Defensibility

There is none today, and it is better to say so. The engine is a few hundred lines, and two MIT-licensed competitors include an audit.

What could become one: the sourcing discipline, if maintained as the guides change, and the dataset of real audits, which nobody else publishes. Neither exists yet.

## Strategic risks

1. **It has never met a real website.** Thresholds, the sitemap sampler and the firewall test were all checked against a local test site. Real sites will break assumptions, and a wrong finding costs more trust than a missing one.
2. **The engines may make it unnecessary.** Search Console and Bing Webmaster Tools could report AI crawler access directly.
3. **The problem may be too small to return for.** If most sites pass access, the remaining checks are ordinary SEO hygiene under a new name.

## The interface: what is wrong and what to change

Based on using the deployed build, not on general principles.

**First impression**

- The page opens on a full example report, several screens long, for a made-up coffee site. A visitor has to work out that it is not theirs. Replace it with the form, three lines on what they get, and a "See an example" link.
- There are two public copies: Render (live) and GitHub Pages (paste only). The Pages copy leads with "This hosted preview cannot fetch websites", which reads as broken. Retire it or redirect it.
- After the server sleeps, the first load is a blank wait of up to a minute. No design fixes that; it needs a paid instance or a static front page that loads at once and wakes the server behind it.

**The report**

- The spec says access comes first, but the score dial is the largest thing on the screen. Lead with a one-line verdict in words ("ChatGPT and Perplexity cannot reach this page"), then engines, then the score.
- In a whole-site report, one page behind a sign-in marks all five engines "Sign-in required" with the same sentence five times. Show the count per engine ("Open on 35 of 36 pages") and state the exception once.
- The fix list gives fifteen items equal weight. Show blockers and the top three, and fold the rest.
- Check IDs (I1, S4) and the pills "Access", "Fix", "Improve" are the builder's vocabulary. Use "Blocking", "Worth fixing", "Optional".
- Each fix says what to do but rarely gives the thing to paste. The robots.txt snippet does this well. Do the same for the Organization JSON-LD, the meta description and the canonical tag, filled in with the page's own values.

**Flow**

- No link to a result. Put the audited address in the URL so a report can be bookmarked and sent.
- No way to see progress on a fix. Keep the last report for each address in the browser and show what changed on a re-run.
- The pages table has no sorting or filtering, which matters at 500 rows. Sort by score, filter to pages with blockers.
- A single-page audit shows one line of text while it works. Show the steps as they finish: page fetched, robots.txt read, each crawler tested.
- Paste mode asks for page source and robots.txt in two boxes with no guidance on a phone, where View Source does not exist. Once the Pages copy is retired, paste mode can go behind an "audit a page that is not public" link.

**Trust**

- Nothing on the page says who made it or how the score is built. Link the scoring model from the report.
- The thresholds (150 words, 120-word paragraphs) are judgement calls. Say so next to the checks that use them.

## Next steps

1. Run it on twenty real sites of different kinds and record every wrong or confusing finding. Do this before anything else; it will reorder this list.
2. Fix the first impression and the report order.
3. Add shareable links and the re-run comparison.
4. Decide what this is for. The spec mentions a portfolio case study. If that is the goal, steps 1 to 3 and a written case study are enough, and sections 6 and 7 can wait. If it is meant to find users, add basic privacy-respecting analytics next, since nothing in section 6 can be measured without it.
