/*
 * Quotable audit engine.
 * Pure logic: takes a bundle of fetched material for one URL and returns a report.
 * Runs in the browser (DOMParser) and in tests (any DOM parser passed in).
 *
 * bundle = {
 *   url,
 *   page:    { status, finalUrl, xRobotsTag, body },
 *   robots:  { status, body },            // status null = not fetched
 *   sitemap: { status },                  // /sitemap.xml, status null = not fetched
 *   llms:    { status },                  // /llms.txt,    status null = not fetched
 *   ua:      { browser: 200, Googlebot: 200, ... } | null
 *   notFound:{ status },                  // a made-up URL on the site, status null = not fetched
 *   home:    { status, body } | null       // the site's home page, when the audited page is not it
 *   live:    true when fetched by the server without per-bot requests (site-wide mode)
 * }
 * page.redirects = [{ status, url }, ...] lists each redirect hop, when known.
 *
 * auditSite() takes { url, origin, shared: { robots, sitemap, llms, notFound }, sampled, pages: [{ url, page, ua }] }.
 * For large sites, call auditSitePage() per page as pages arrive, then buildSite() once.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AuditEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SOURCES = {
    G1: { label: 'Google: AI features and your website', url: 'https://developers.google.com/search/docs/appearance/ai-features' },
    G2: { label: 'Google: Optimizing for generative AI features', url: 'https://developers.google.com/search/docs/fundamentals/ai-optimization-guide' },
    G3: { label: 'Google: SEO Starter Guide', url: 'https://developers.google.com/search/docs/fundamentals/seo-starter-guide' },
    MS: { label: 'Microsoft: Content for AI search answers', url: 'https://about.ads.microsoft.com/en/blog/post/october-2025/optimizing-your-content-for-inclusion-in-ai-search-answers' },
    BING: { label: 'Bing: Webmaster Guidelines', url: 'https://www.bing.com/webmaster/help/webmaster-guidelines-30fba23a' },
    OAI: { label: 'OpenAI: Overview of OpenAI crawlers', url: 'https://developers.openai.com/api/docs/bots' },
    ANT: { label: 'Anthropic: How Claude crawls the web', url: 'https://support.claude.com/en/articles/8896518-does-anthropic-crawl-the-web' },
    PPLX: { label: 'Perplexity: Perplexity crawlers', url: 'https://docs.perplexity.ai/docs/resources/perplexity-crawlers' },
    LI: { label: 'LinkedIn: Unlocking AI Visibility', url: 'https://business.linkedin.com/advertise/resources/unlocking-ai-visibility' },
    HUB: { label: 'ChatGPT Business Hub', url: 'https://chatgpt.com/business-hub' },
    G4: { label: 'Google: HTTP status codes and soft 404 errors', url: 'https://developers.google.com/search/docs/crawling-indexing/http-network-errors' },
    G5: { label: 'Google: Redirects and Google Search', url: 'https://developers.google.com/search/docs/crawling-indexing/301-redirects' },
    G6: { label: 'Google: Organization structured data', url: 'https://developers.google.com/search/docs/appearance/structured-data/organization' },
    G7: { label: 'Google: Article structured data', url: 'https://developers.google.com/search/docs/appearance/structured-data/article' },
    G8: { label: 'Google: Publication dates', url: 'https://developers.google.com/search/docs/appearance/publication-dates' },
    G9: { label: 'Google: Creating helpful, reliable, people-first content', url: 'https://developers.google.com/search/docs/fundamentals/creating-helpful-content' }
  };

  var ENGINES = [
    { id: 'google', name: 'Google AI Overviews and AI Mode', index: 'Googlebot', user: null, source: 'G1' },
    { id: 'bing', name: 'Microsoft Copilot and Bing', index: 'Bingbot', user: null, source: 'BING' },
    { id: 'chatgpt', name: 'ChatGPT search', index: 'OAI-SearchBot', user: 'ChatGPT-User', source: 'OAI',
      note: 'OpenAI says robots.txt rules may not apply to ChatGPT-User, because a person triggers each fetch.' },
    { id: 'claude', name: 'Claude', index: 'Claude-SearchBot', user: 'Claude-User', source: 'ANT' },
    { id: 'perplexity', name: 'Perplexity', index: 'PerplexityBot', user: 'Perplexity-User', source: 'PPLX',
      note: 'Perplexity says Perplexity-User generally ignores robots.txt, because a person triggers each fetch.' }
  ];

  var TRAINING = [
    { token: 'GPTBot', operator: 'OpenAI', source: 'OAI',
      meaning: 'Blocking it keeps your content out of OpenAI model training. It does not affect ChatGPT search.' },
    { token: 'ClaudeBot', operator: 'Anthropic', source: 'ANT',
      meaning: 'Blocking it keeps future content out of Anthropic model training. It does not affect Claude search.' },
    { token: 'Google-Extended', operator: 'Google', source: 'G1',
      meaning: 'Limits training and grounding in other Google AI systems. It does not affect Google Search or AI Overviews.' }
  ];

  var BLOCK_CODES = [401, 403, 406, 429, 503];

  // ---------- robots.txt (RFC 9309) ----------

  function parseRobots(text) {
    var groups = [], sitemaps = [], current = null, lastWasAgent = false;
    String(text || '').split(/\r?\n/).forEach(function (raw) {
      var line = raw.replace(/#.*$/, '').trim();
      if (!line) return;
      var i = line.indexOf(':');
      if (i < 0) return;
      var key = line.slice(0, i).trim().toLowerCase();
      var value = line.slice(i + 1).trim();
      if (key === 'user-agent') {
        if (!lastWasAgent || !current) { current = { agents: [], rules: [] }; groups.push(current); }
        current.agents.push(value.toLowerCase());
        lastWasAgent = true;
      } else if (key === 'allow' || key === 'disallow') {
        if (current) current.rules.push({ type: key, path: value });
        lastWasAgent = false;
      } else if (key === 'sitemap') {
        sitemaps.push(value);
      } else {
        lastWasAgent = false;
      }
    });
    return { groups: groups, sitemaps: sitemaps };
  }

  function patternToRegex(pattern) {
    var anchored = /\$$/.test(pattern);
    var body = anchored ? pattern.slice(0, -1) : pattern;
    var re = body.split('*').map(function (s) { return s.replace(/[.+?^${}()|[\]\\]/g, '\\$&'); }).join('.*');
    return new RegExp('^' + re + (anchored ? '$' : ''));
  }

  // Returns { allowed, rule, group } for one bot token and one URL path.
  function isAllowed(robots, token, path) {
    var t = token.toLowerCase();
    var matched = robots.groups.filter(function (g) { return g.agents.indexOf(t) >= 0; });
    var groupName = token;
    if (!matched.length) {
      matched = robots.groups.filter(function (g) { return g.agents.indexOf('*') >= 0; });
      groupName = '*';
    }
    if (!matched.length) return { allowed: true, rule: null, group: null };
    var best = null;
    matched.forEach(function (g) {
      g.rules.forEach(function (r) {
        if (r.path === '') return; // empty Disallow = allow everything
        var hit;
        try { hit = patternToRegex(r.path).test(path); } catch (e) { hit = false; }
        if (!hit) return;
        var len = r.path.length;
        if (!best || len > best.len || (len === best.len && r.type === 'allow')) best = { len: len, rule: r };
      });
    });
    if (!best) return { allowed: true, rule: null, group: groupName };
    return {
      allowed: best.rule.type === 'allow',
      rule: (best.rule.type === 'allow' ? 'Allow: ' : 'Disallow: ') + best.rule.path,
      group: groupName
    };
  }

  // ---------- text helpers ----------

  var STOP = ' a an and are as at be by for from how in into is it of on or our that the this to we what when why with you your vs '.split(' ');

  function norm(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
  function words(s) { var t = norm(s); return t ? t.split(' ').length : 0; }
  function tokens(s) {
    return norm(s).toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(function (w) {
      return w.length > 1 && STOP.indexOf(w) < 0;
    });
  }
  function overlap(a, b) { // share of a's tokens found in b
    var ta = tokens(a), tb = tokens(b);
    if (!ta.length || !tb.length) return 0;
    var hit = ta.filter(function (w) { return tb.indexOf(w) >= 0; }).length;
    return hit / ta.length;
  }
  function normUrl(u) {
    try {
      var x = new URL(u);
      return (x.host.toLowerCase().replace(/^www\./, '') + x.pathname.replace(/\/+$/, '') + x.search).toLowerCase();
    } catch (e) { return null; }
  }

  // ---------- entity helpers ----------

  var ORG_TYPES = /^(Organization|Corporation|LocalBusiness|OnlineBusiness|OnlineStore|Store|Restaurant|ProfessionalService|NGO|NewsMediaOrganization|EducationalOrganization|GovernmentOrganization|MedicalOrganization|Person)$/;
  var ARTICLE_TYPES = /^(Article|BlogPosting|NewsArticle|TechArticle|ScholarlyArticle|Report|LiveBlogPosting)$/;

  // Every typed object in a set of JSON-LD blocks, including nested ones such as publisher and author.
  function flattenSchema(value, out, depth) {
    out = out || []; depth = depth || 0;
    if (!value || typeof value !== 'object' || depth > 6) return out;
    if (Array.isArray(value)) { value.forEach(function (v) { flattenSchema(v, out, depth + 1); }); return out; }
    if (value['@type']) out.push(value);
    Object.keys(value).forEach(function (k) { flattenSchema(value[k], out, depth + 1); });
    return out;
  }
  function hasType(item, re) { return [].concat(item['@type'] || []).some(function (t) { return re.test(String(t)); }); }
  function parseJsonLd(blocks) {
    var items = [];
    blocks.forEach(function (raw) { try { flattenSchema(JSON.parse(raw), items); } catch (e) { /* counted elsewhere */ } });
    return items;
  }

  // What the home page says about who runs the site.
  function homeFacts(jsonldBlocks, links, homeUrl) {
    var orgs = parseJsonLd(jsonldBlocks).filter(function (x) { return hasType(x, ORG_TYPES); });
    var org = orgs.filter(function (o) { return o.name && o.url; })[0] || orgs[0] || null;
    var sameAs = org ? [].concat(org.sameAs || []).filter(function (s) { return typeof s === 'string' && /^https?:/.test(s); }) : [];
    var about = false, contact = false;
    links.forEach(function (l) {
      var href = l.href || '', text = norm(l.text), seg = '';
      try { seg = new URL(href, homeUrl).pathname.toLowerCase().replace(/\/+$/, '').split('/').pop().replace(/\.[a-z]+$/, ''); } catch (e) { /* bad href */ }
      if (/^(about|about-us|aboutus|company|who-we-are|our-story|team)$/.test(seg) || /\babout\b|our story|who we are/i.test(text)) about = true;
      if (/^(contact|contact-us|contactus|support|help|get-in-touch)$/.test(seg) || /\bcontact\b|get in touch/i.test(text) || /^(mailto|tel):/i.test(href)) contact = true;
    });
    return { org: org, sameAs: sameAs, about: about, contact: contact };
  }
  var homeCache = { body: null, facts: null };

  // ---------- the audit ----------

  function audit(bundle, parse) {
    parse = parse || function (html) { return new DOMParser().parseFromString(html, 'text/html'); };
    var page = bundle.page || {};
    var pageUrl = page.finalUrl || bundle.url;
    var u; try { u = new URL(pageUrl); } catch (e) { u = null; }
    var path = u ? (u.pathname + u.search) : '/';
    var doc = parse(page.body || '');
    var q = function (sel, el) { return Array.prototype.slice.call((el || doc).querySelectorAll(sel)); };

    // Raw facts taken before stripping anything.
    var title = norm((doc.querySelector('title') || {}).textContent);
    var metaEl = doc.querySelector('meta[name="description" i]');
    var metaDesc = norm(metaEl ? metaEl.getAttribute('content') : '');
    var robotsMeta = q('meta[name="robots" i], meta[name="googlebot" i], meta[name="bingbot" i]')
      .map(function (m) { return (m.getAttribute('content') || '').toLowerCase(); }).join(', ');
    var directives = (robotsMeta + ', ' + String(page.xRobotsTag || '').toLowerCase());
    var canonicalEl = doc.querySelector('link[rel="canonical" i]');
    var canonical = canonicalEl ? canonicalEl.getAttribute('href') : null;
    var jsonldRaw = q('script[type="application/ld+json" i]').map(function (s) { return s.textContent; });
    var scriptCount = q('script').length;
    var dataNosnippet = q('[data-nosnippet]').length;
    var anchorHrefs = q('a[href]').map(function (el) { return el.getAttribute('href'); });
    var anchorLinks = q('a[href]').map(function (el) { return { href: el.getAttribute('href'), text: el.textContent }; });
    var metaContent = function (sel) { var m = doc.querySelector(sel); return m ? norm(m.getAttribute('content')) : ''; };
    var ogType = metaContent('meta[property="og:type" i]').toLowerCase();
    var metaPublished = metaContent('meta[property="article:published_time" i]');
    var metaModified = metaContent('meta[property="article:modified_time" i]') || metaContent('meta[property="og:updated_time" i]');
    var metaAuthor = metaContent('meta[name="author" i]');
    var timeTags = q('time[datetime]').length;
    var authorMarks = q('a[rel~="author" i], [itemprop~="author" i]').length;
    var metaRefresh = q('meta[http-equiv="refresh" i]').map(function (m) { return m.getAttribute('content') || ''; })
      .filter(function (c) { return /url\s*=/i.test(c); });
    var jsRedirect = /\blocation(\.href)?\s*=[^=]|\blocation\.(replace|assign)\s*\(/.test(
      q('script:not([src])').map(function (el) { return el.textContent; }).join('\n'));
    var passwordFields = q('input[type="password" i]').length;

    q('script, style, noscript, template, svg, iframe').forEach(function (el) { el.remove(); });

    var body = doc.body || doc.documentElement;
    var landmark = doc.querySelector('main, [role="main"], article');
    var content = landmark || body;
    if (!landmark && body) q('nav, header, footer, aside', body).forEach(function (el) { el.remove(); });

    var text = norm(content ? content.textContent : '');
    var wordCount = words(text);
    var headings = q('h1, h2, h3, h4', body).map(function (h) { return { level: +h.tagName[1], text: norm(h.textContent) }; });
    var h1s = headings.filter(function (h) { return h.level === 1; });
    var subheads = headings.filter(function (h) { return h.level === 2 || h.level === 3; });
    var imgs = q('img', content);
    var imgsWithAlt = imgs.filter(function (i) { return i.hasAttribute('alt'); });
    var hiddenWords = q('details, [hidden]', content).reduce(function (n, el) { return n + words(el.textContent); }, 0);
    var listsTables = q('ul, ol, table', content).filter(function (el) { return !el.closest('nav, header, footer'); });
    var paras = q('p', content).map(function (p) { return words(p.textContent); });
    var longParas = paras.filter(function (n) { return n > 120; }).length;
    var host = u ? u.host.replace(/^www\./, '') : '';
    var internal = {};
    anchorHrefs.forEach(function (href) {
      try {
        var t = new URL(href, pageUrl);
        if (!/^https?:$/.test(t.protocol)) return;
        if (t.host.replace(/^www\./, '') !== host) return;
        var p = t.pathname.replace(/\/+$/, '') || '/';
        if (u && p === (u.pathname.replace(/\/+$/, '') || '/')) return;
        internal[p] = true;
      } catch (e) { /* bad href */ }
    });
    var internalCount = Object.keys(internal).length;

    // JSON-LD
    var schemaItems = [], schemaBad = 0;
    jsonldRaw.forEach(function (raw) {
      try {
        var v = JSON.parse(raw);
        (Array.isArray(v) ? v : [v]).forEach(function (item) {
          if (item && item['@graph']) schemaItems = schemaItems.concat(item['@graph']); else schemaItems.push(item);
        });
      } catch (e) { schemaBad++; }
    });
    schemaItems = schemaItems.filter(function (x) { return x && typeof x === 'object'; });
    var schemaTypes = [];
    schemaItems.forEach(function (x) { [].concat(x['@type'] || []).forEach(function (t) { if (schemaTypes.indexOf(t) < 0) schemaTypes.push(t); }); });
    var schemaNames = schemaItems.map(function (x) { return x.headline || x.name; }).filter(function (s) { return typeof s === 'string' && s; });

    // robots.txt
    var robotsStatus = bundle.robots ? bundle.robots.status : null;
    var robotsKnown = robotsStatus !== null && robotsStatus !== undefined;
    var robotsUnreachable = robotsKnown && robotsStatus >= 500;
    var robots = parseRobots(robotsKnown && robotsStatus === 200 ? bundle.robots.body : '');
    var ua = bundle.ua || null;
    var browserOk = ua && ua.browser === 200;

    function botState(token) {
      var r = { token: token, robots: 'unknown', rule: null, group: null, uaStatus: ua ? ua[token] : null, uaBlocked: false };
      if (robotsUnreachable) { r.robots = 'disallowed'; r.rule = 'robots.txt returned ' + robotsStatus; }
      else if (robotsKnown) {
        var a = isAllowed(robots, token, path);
        r.robots = a.allowed ? 'allowed' : 'disallowed'; r.rule = a.rule; r.group = a.group;
      }
      if (browserOk && r.uaStatus && BLOCK_CODES.indexOf(r.uaStatus) >= 0) r.uaBlocked = true;
      return r;
    }

    // ---------- scored checks ----------
    var noindex = /\bnoindex\b|\bnone\b/.test(directives);
    var nosnippet = /\bnosnippet\b|max-snippet\s*:\s*0\b/.test(directives);
    var snippetCap = (directives.match(/max-snippet\s*:\s*(\d+)/g) || [])
      .map(function (m) { return +m.replace(/\D+/g, ''); })
      .filter(function (n) { return n > 0 && n < 50; }).sort(function (a, b) { return a - b; })[0];
    var statusKnown = page.status !== null && page.status !== undefined;

    function check(id, title, points, status, detail, fix, source) {
      var earned = status === 'pass' || status === 'na' ? points : status === 'warn' ? points / 2 : 0;
      return { id: id, title: title, points: points, earned: earned, status: status, detail: detail, fix: fix || '', source: SOURCES[source] };
    }

    var I = [
      check('I1', 'Page returns HTTP 200', 10,
        !statusKnown ? 'na' : page.status === 200 ? 'pass' : 'fail',
        !statusKnown ? 'Status not checked in paste mode.' : 'The page returned ' + page.status + '.',
        'Serve the page with a 200 status. Fix redirects that end in an error and any server faults.', 'G1'),
      check('I2', 'Page allows indexing', 8, noindex ? 'fail' : 'pass',
        noindex ? 'A noindex directive is set (' + norm(directives.replace(/^, |, $/g, '')) + ').' : 'No noindex directive found.',
        'Remove noindex from the robots meta tag and the X-Robots-Tag header if you want this page in search and AI answers.', 'G1'),
      check('I3', 'Snippets are allowed', 5, nosnippet ? 'fail' : snippetCap ? 'warn' : 'pass',
        nosnippet ? 'nosnippet or max-snippet:0 is set. Google keeps such pages out of AI Overviews and AI Mode.'
          : snippetCap ? 'max-snippet:' + snippetCap + ' limits previews to ' + snippetCap + ' characters, too short to quote a full sentence.'
          : 'No snippet restriction found.' + (dataNosnippet ? ' ' + dataNosnippet + ' element(s) use data-nosnippet and are excluded from previews.' : ''),
        'Remove nosnippet and max-snippet:0, and raise small max-snippet limits, unless you mean to opt out of AI answers on Google.', 'G1'),
      (function () {
        var c = canonical ? normUrl(new URL(canonical, pageUrl).href) : null, me = normUrl(pageUrl);
        var other = c && me && c !== me;
        return check('I4', 'Canonical points to this page', 2, other ? 'warn' : 'pass',
          !canonical ? 'No canonical tag. That is fine for a unique page.' : other ? 'Canonical points to a different URL: ' + canonical : 'Canonical matches this URL.',
          'Point the canonical tag at this URL, or audit the canonical URL instead, since that is the version engines keep.', 'G2');
      })()
    ];

    var jsHint = wordCount < 150 && scriptCount >= 5 ? ' The page loads ' + scriptCount + ' scripts, so the content is probably rendered by JavaScript.' : '';
    var altShare = imgs.length ? imgsWithAlt.length / imgs.length : 1;
    var hiddenShare = wordCount ? hiddenWords / wordCount : 0;
    var R = [
      check('R1', 'Body text is in the HTML', 10, wordCount >= 150 ? 'pass' : wordCount >= 50 ? 'warn' : 'fail',
        wordCount + ' words of body text found in the raw HTML.' + jsHint,
        'Render the main content on the server so it is present in the HTML that crawlers download. Several AI crawlers do not run JavaScript.', 'G1'),
      check('R2', 'Images carry alt text', 4, !imgs.length ? 'na' : altShare >= 0.8 ? 'pass' : altShare >= 0.5 ? 'warn' : 'fail',
        !imgs.length ? 'No content images on the page.' : imgsWithAlt.length + ' of ' + imgs.length + ' images have an alt attribute.',
        'Add alt text to images that carry information, and repeat key facts from images in the page text.', 'MS'),
      check('R3', 'Answers are not hidden in collapsed sections', 3, hiddenShare <= 0.25 ? 'pass' : hiddenShare <= 0.5 ? 'warn' : 'fail',
        Math.round(hiddenShare * 100) + '% of the text sits in collapsed or hidden elements.',
        'Move important answers out of tabs, accordions and hidden blocks. AI systems may not open them.', 'MS'),
      check('R4', 'Page uses a main or article landmark', 3, landmark ? 'pass' : 'fail',
        landmark ? 'Found a <' + landmark.tagName.toLowerCase() + '> landmark around the content.' : 'No <main> or <article> element found.',
        'Wrap the primary content in <main> or <article> so parsers can tell it from navigation and footers.', 'G2')
    ];

    var h1 = h1s.length ? h1s[0].text : '';
    var align = title && h1 ? Math.max(overlap(h1, title), overlap(title, h1)) : 0;
    var VAGUE = ['learn more', 'read more', 'more', 'click here', 'details', 'info', 'more info', 'information', 'welcome', 'untitled', 'misc', 'other', 'section'];
    var vague = headings.filter(function (h) { return VAGUE.indexOf(h.text.toLowerCase().replace(/[^a-z ]/g, '').trim()) >= 0; });
    var questionRe = /\?\s*$|^(what|how|why|when|which|who|where|can|does|do|is|are|should)\b/i;
    var questions = headings.filter(function (h) { return questionRe.test(h.text); });
    var decoRe = /[!?]{2,}|[←-⇿■-◿☀-➿⬀-⯿]|\p{Extended_Pictographic}/u;
    var deco = [title].concat(headings.map(function (h) { return h.text; })).filter(function (s) { return decoRe.test(s); });

    var S = [
      check('S1', 'Title is present and a sensible length', 5, !title ? 'fail' : title.length >= 15 && title.length <= 65 ? 'pass' : 'warn',
        !title ? 'No <title> element.' : 'Title is ' + title.length + ' characters: "' + title + '"',
        'Write a title of 15 to 65 characters that says in plain language what the page delivers.', 'MS'),
      check('S2', 'Meta description is present and a sensible length', 4, !metaDesc ? 'fail' : metaDesc.length >= 50 && metaDesc.length <= 160 ? 'pass' : 'warn',
        !metaDesc ? 'No meta description.' : 'Description is ' + metaDesc.length + ' characters.',
        'Add a 50 to 160 character description that states the value or outcome of the page, without keyword stuffing.', 'MS'),
      check('S3', 'Exactly one H1', 5, h1s.length === 1 ? 'pass' : h1s.length > 1 ? 'warn' : 'fail',
        h1s.length + ' H1 heading(s) found.' + (h1 ? ' First: "' + h1 + '"' : ''),
        'Use one H1 that states the topic of the page.', 'MS'),
      check('S4', 'Title and H1 agree', 4, !title || !h1 ? 'fail' : align >= 0.5 ? 'pass' : 'fail',
        !title || !h1 ? 'Needs both a title and an H1 to compare.' : Math.round(align * 100) + '% of the meaningful words are shared between title and H1.',
        'Make the H1 match or closely reflect the title, so both set the same expectation.', 'MS'),
      check('S5', 'Subheadings break up the page', 5, wordCount <= 300 ? 'na' : subheads.length >= 2 ? 'pass' : subheads.length === 1 ? 'warn' : 'fail',
        wordCount <= 300 ? 'Page is under 300 words, so subheadings are optional.' : subheads.length + ' H2/H3 heading(s) across ' + wordCount + ' words.',
        'Add H2 and H3 headings that mark where one idea ends and the next begins.', 'MS'),
      check('S6', 'Headings are descriptive', 3, vague.length === 0 ? 'pass' : vague.length === 1 ? 'warn' : 'fail',
        vague.length ? 'Vague heading(s): ' + vague.map(function (h) { return '"' + h.text + '"'; }).join(', ') : 'No vague headings found.',
        'Replace headings such as "Learn more" with ones that name the topic of the section.', 'MS'),
      check('S7', 'Lists or tables are used', 3, listsTables.length ? 'pass' : 'fail',
        listsTables.length + ' list(s) or table(s) in the content.',
        'Put steps, comparisons and key features in lists or tables, which can be lifted cleanly into answers.', 'MS'),
      check('S8', 'A heading asks a question people search for', 2, questions.length ? 'pass' : 'fail',
        questions.length ? 'Found: "' + questions[0].text + '"' : 'No question-style headings found.',
        'Add a heading phrased as a real question, followed by a one to two sentence direct answer.', 'MS'),
      check('S9', 'No walls of text', 3, longParas === 0 ? 'pass' : longParas <= 2 ? 'warn' : 'fail',
        longParas + ' paragraph(s) run over 120 words.',
        'Split long paragraphs so each makes one point and still makes sense when quoted alone.', 'MS'),
      check('S10', 'No decorative symbols in title or headings', 1, deco.length ? 'fail' : 'pass',
        deco.length ? 'Found in: "' + deco[0] + '"' : 'None found.',
        'Remove arrows, stars, emoji and repeated punctuation from titles and headings.', 'MS')
    ];

    var schemaMatch = schemaNames.some(function (n) {
      return (title && Math.max(overlap(n, title), overlap(title, n)) >= 0.5) || (h1 && Math.max(overlap(n, h1), overlap(h1, n)) >= 0.5);
    });
    var hasSchema = schemaItems.length > 0;
    var M = [
      check('M1', 'Schema markup is present and valid', 5, hasSchema ? (schemaBad ? 'warn' : 'pass') : jsonldRaw.length ? 'warn' : 'fail',
        hasSchema ? 'JSON-LD types: ' + (schemaTypes.join(', ') || 'none declared') + (schemaBad ? '. ' + schemaBad + ' block(s) failed to parse.' : '.')
          : jsonldRaw.length ? 'JSON-LD is present but does not parse.' : 'No JSON-LD found.',
        'Add JSON-LD that labels what the page is, such as Article, Product, FAQPage or Organization. Google does not require it for AI features; Microsoft recommends it.', 'MS'),
      check('M2', 'Schema matches the visible page', 5, !hasSchema ? 'fail' : !schemaNames.length ? 'warn' : schemaMatch ? 'pass' : 'fail',
        !hasSchema ? 'Nothing to compare.' : !schemaNames.length ? 'Schema has no name or headline to compare.' : schemaMatch ? 'Schema name matches the title or H1.' : 'Schema name "' + schemaNames[0] + '" does not match the title or H1.',
        'Keep structured data consistent with the text people see on the page.', 'G1')
    ];

    var sitemapStatus = bundle.sitemap ? bundle.sitemap.status : null;
    var sitemapKnown = robotsKnown || (sitemapStatus !== null && sitemapStatus !== undefined);
    var hasSitemap = robots.sitemaps.length > 0 || sitemapStatus === 200;
    var D = [
      check('D1', 'A sitemap is published', 5, !sitemapKnown ? 'na' : hasSitemap ? 'pass' : 'fail',
        !sitemapKnown ? 'Not checked in paste mode.' : robots.sitemaps.length ? 'robots.txt declares: ' + robots.sitemaps[0] : sitemapStatus === 200 ? 'Found at /sitemap.xml, but robots.txt does not declare it.' : 'No sitemap in robots.txt or at /sitemap.xml.',
        'Publish an XML sitemap and declare it in robots.txt with a Sitemap: line. Submit it in Search Console and Bing Webmaster Tools.', 'BING'),
      check('D2', 'Page links to other pages on the site', 5, internalCount >= 3 ? 'pass' : internalCount >= 1 ? 'warn' : 'fail',
        internalCount + ' internal page(s) linked from this page.',
        'Link to related pages on your site with descriptive link text, so crawlers can find them.', 'G1')
    ];

    // ---------- entity and freshness checks (not scored) ----------
    var allSchema = parseJsonLd(jsonldRaw);
    var isHome = u && (u.pathname === '/' || u.pathname === '');
    var home = null, homeNote = '';
    if (bundle.home && bundle.home.status === 200) {
      if (homeCache.body !== bundle.home.body) {
        var hd = parse(bundle.home.body || '');
        homeCache = { body: bundle.home.body, facts: homeFacts(
          Array.prototype.slice.call(hd.querySelectorAll('script[type="application/ld+json" i]')).map(function (s) { return s.textContent; }),
          Array.prototype.slice.call(hd.querySelectorAll('a[href]')).map(function (el) { return { href: el.getAttribute('href'), text: el.textContent }; }),
          u ? u.origin + '/' : pageUrl) };
      }
      home = homeCache.facts;
    } else if (isHome) {
      home = homeFacts(jsonldRaw, anchorLinks, pageUrl);
    } else {
      homeNote = bundle.home ? 'The home page could not be fetched.' : 'The home page was not checked. Audit the home page to run this check.';
    }
    var articles = allSchema.filter(function (x) { return hasType(x, ARTICLE_TYPES); });
    var isArticle = articles.length > 0 || ogType === 'article';
    var pick = function (key) { return articles.map(function (x) { return x[key]; }).filter(Boolean)[0]; };
    var published = pick('datePublished') || metaPublished, modified = pick('dateModified') || metaModified;
    var badDate = [published, modified].filter(function (d) { return d && isNaN(Date.parse(d)); })[0];
    var authors = [];
    articles.forEach(function (x) {
      [].concat(x.author || []).forEach(function (a) { var n = typeof a === 'string' ? a : a && a.name; if (typeof n === 'string' && n.trim()) authors.push({ name: n.trim(), linked: !!(a && (a.url || a.sameAs)) }); });
    });
    var E = [
      check('E1', 'Home page says who runs the site', 0,
        !home ? 'na' : !home.org ? 'fail' : home.org.name && home.org.url ? 'pass' : 'warn',
        !home ? homeNote : !home.org ? 'No Organization or Person structured data on the home page.'
          : home.org.name && home.org.url ? 'Organization data names "' + norm(home.org.name) + '".' : 'Organization data is present but lacks a name or url.',
        'Add Organization JSON-LD to the home page with name, url and logo, so engines can tie the site to the right business. Use Person for a personal site.', 'G6'),
      check('E2', 'Organization links to its official profiles', 0,
        !home || !home.org ? 'na' : home.sameAs.length ? 'pass' : 'fail',
        !home ? homeNote : !home.org ? 'Needs Organization data first.' : home.sameAs.length ? home.sameAs.length + ' profile link(s) in sameAs.' : 'The Organization data has no sameAs links.',
        'List your official profiles, such as LinkedIn, Wikipedia or Crunchbase, in the Organization sameAs property.', 'G6'),
      check('E3', 'Article states when it was published and updated', 0,
        !isArticle ? 'na' : badDate ? 'warn' : published && modified ? 'pass' : published || modified || timeTags ? 'warn' : 'fail',
        !isArticle ? 'Not marked up as an article, so dates are optional.' : badDate ? 'A date is present but is not a valid date: "' + badDate + '".'
          : published && modified ? 'Published ' + String(published).slice(0, 10) + ', updated ' + String(modified).slice(0, 10) + '.'
          : published ? 'Published ' + String(published).slice(0, 10) + ', with no updated date.' : modified ? 'Updated ' + String(modified).slice(0, 10) + ', with no published date.'
          : timeTags ? 'A date is shown on the page but not in structured data.' : 'No published or updated date found.',
        'Give datePublished and dateModified in the Article JSON-LD, show the same dates on the page, and keep them accurate.', 'G8'),
      check('E4', 'Article names its author', 0,
        !isArticle ? 'na' : authors.length ? (authors.some(function (a) { return a.linked; }) ? 'pass' : 'warn') : metaAuthor || authorMarks ? 'warn' : 'fail',
        !isArticle ? 'Not marked up as an article, so an author is optional.'
          : authors.length ? 'Author: ' + authors[0].name + (authors.some(function (a) { return a.linked; }) ? '.' : ', with no link to an author page or profile.')
          : metaAuthor ? 'Author "' + metaAuthor + '" appears in a meta tag but not in structured data.' : authorMarks ? 'An author is marked on the page but not in structured data.' : 'No author found.',
        'Name the author in the Article JSON-LD as a Person with a url to their author page or profile, and show the byline on the page.', 'G7'),
      check('E5', 'Home page links to About and Contact pages', 0,
        !home ? 'na' : home.about && home.contact ? 'pass' : home.about || home.contact ? 'warn' : 'fail',
        !home ? homeNote : home.about && home.contact ? 'Both are linked from the home page.'
          : home.about ? 'An About page is linked, but no Contact page or contact details.' : home.contact ? 'Contact details are linked, but no About page.' : 'Neither is linked from the home page.',
        'Link an About page and a Contact page from the home page, so people and engines can see who is behind the site.', 'G9')
    ];

    // ---------- access checks (not scored) ----------
    var nf = bundle.notFound ? bundle.notFound.status : null;
    var nfKnown = nf !== null && nf !== undefined;
    var hops = Array.isArray(page.redirects) ? page.redirects : null;
    var chain = hops ? hops.map(function (h) { return h.status; }).join(', ') : '';
    var clientRedirect = metaRefresh.length ? 'a meta refresh tag' : (jsRedirect && wordCount < 50) ? 'JavaScript' : '';
    var LOGIN = /(^|\/)(log-?in|sign-?in|signin|sso|auth|authenticate|account\/login|users\/sign_in)(\/|$|\.)/i;
    var askedPath = ''; try { askedPath = new URL(bundle.url).pathname; } catch (e) { /* keep empty */ }
    var sentToLogin = u && hops && hops.length && LOGIN.test(u.pathname) && !LOGIN.test(askedPath);
    var signIn = statusKnown && (page.status === 401 || page.status === 403) ? 'The page returned ' + page.status + ' to a visitor who is not signed in.'
      : sentToLogin ? 'The page redirects to a sign-in page: ' + pageUrl
      : passwordFields && wordCount < 150 ? 'The page shows a password field and only ' + wordCount + ' words of text.' : '';
    var A = [
      check('A5', 'Missing pages return 404', 0, !nfKnown ? 'na' : nf === 404 || nf === 410 ? 'pass' : nf === 200 ? 'fail' : 'warn',
        !nfKnown ? 'Not checked in paste mode.' : nf === 200 ? 'A made-up address on this site returned 200. Crawlers will treat error pages as real content.'
          : 'A made-up address on this site returned ' + nf + '.',
        'Return a 404 status for pages that do not exist, instead of a 200 page or a redirect to the home page.', 'G4'),
      check('A6', 'Redirects are short and server-side', 0, clientRedirect || (hops && hops.length > 2) ? 'fail' : hops ? 'pass' : 'na',
        clientRedirect ? 'The page redirects using ' + clientRedirect + ', which many crawlers do not follow.'
          : !hops ? 'Redirect chain not checked in paste mode.' : !hops.length ? 'The page loads without redirects.'
          : hops.length + ' redirect(s) before the page loads (' + chain + ').',
        'Redirect on the server with a single 301 or 308, straight to the final address.', 'G5'),
      check('A7', 'Content is readable without signing in', 0, signIn ? 'fail' : 'pass',
        signIn || 'No sign-in wall found.',
        'Keep pages you want quoted readable without an account. Crawlers cannot sign in.', 'G1')
    ];

    var groups = [
      { id: 'A', name: 'Access', checks: A, unscored: true },
      { id: 'I', name: 'Indexability', checks: I },
      { id: 'R', name: 'Readable content', checks: R },
      { id: 'S', name: 'Answer structure', checks: S },
      { id: 'M', name: 'Schema', checks: M },
      { id: 'D', name: 'Discovery', checks: D },
      { id: 'E', name: 'Entity and freshness', checks: E, unscored: true }
    ];
    var score = 0;
    groups.forEach(function (g) {
      g.max = g.checks.reduce(function (n, c) { return n + c.points; }, 0);
      g.earned = g.checks.reduce(function (n, c) { return n + c.earned; }, 0);
      score += g.earned;
    });
    score = Math.round(score);

    // ---------- engine access ----------
    var pageDown = statusKnown && page.status !== 200;
    var engines = ENGINES.map(function (e) {
      var ib = botState(e.index), ub = e.user ? botState(e.user) : null;
      var softUser = e.id === 'chatgpt' || e.id === 'perplexity'; // user-fetch bots that may bypass robots.txt
      var state, reason;
      if (signIn && ib.robots !== 'disallowed') { state = 'signin'; reason = signIn + ' Crawlers cannot sign in.'; }
      else if (ib.robots === 'disallowed') { state = 'blocked'; reason = 'robots.txt blocks ' + e.index + (ib.rule ? ' (' + ib.rule + ' in the ' + ib.group + ' group).' : '.'); }
      else if ((e.id === 'google' || e.id === 'bing') && (noindex || pageDown)) { state = 'restricted'; reason = noindex ? 'The page is set to noindex.' : 'The page returned ' + page.status + '.'; }
      else if (e.id === 'google' && nosnippet) { state = 'restricted'; reason = 'Snippets are switched off, which keeps the page out of AI answers.'; }
      else if (ib.uaBlocked) { state = 'firewall'; reason = 'A request identifying as ' + e.index + ' got ' + ib.uaStatus + ' while a browser got 200. Check your firewall or CDN bot rules.'; }
      else if (ub && ub.robots === 'disallowed') { state = 'partial'; reason = 'robots.txt blocks ' + e.user + ', the agent that fetches pages when a person asks.' + (softUser ? ' This agent may fetch anyway.' : ''); }
      else if (ub && ub.uaBlocked) { state = 'partial'; reason = 'A request identifying as ' + e.user + ' got ' + ub.uaStatus + '. Check your firewall or CDN bot rules.'; }
      else if (ib.robots === 'unknown') { state = 'unknown'; reason = 'robots.txt was not provided, so access could not be checked.'; }
      else { state = 'open'; reason = e.index + (e.user ? ' and ' + e.user + ' are' : ' is') + ' allowed to fetch this page.'; }
      return { id: e.id, name: e.name, state: state, reason: reason, note: e.note || '', bots: ub ? [ib, ub] : [ib], source: SOURCES[e.source] };
    });

    var training = TRAINING.map(function (t) {
      var b = botState(t.token);
      return { token: t.token, operator: t.operator, robots: b.robots, rule: b.rule, meaning: t.meaning, source: SOURCES[t.source] };
    });

    // ---------- fix list ----------
    var fixes = [];
    if (robotsUnreachable) fixes.push({ level: 'blocker', title: 'robots.txt returns ' + robotsStatus,
      detail: 'Crawlers treat an unreachable robots.txt as a full block.', fix: 'Make /robots.txt return 200, or 404 if you have no rules.', source: SOURCES.G1 });
    var toAllow = [];
    engines.forEach(function (e) {
      if (e.state === 'open' || e.state === 'unknown') return;
      e.bots.forEach(function (b) { if (b.robots === 'disallowed' && !robotsUnreachable && toAllow.indexOf(b.token) < 0) toAllow.push(b.token); });
      if (e.state === 'blocked' || e.state === 'firewall') {
        fixes.push({ level: 'blocker', title: e.name + ' cannot reach this page', detail: e.reason,
          fix: e.state === 'firewall' ? 'Allow this bot in your firewall or CDN. Match on user-agent and the operator\'s published IP ranges.' : 'Allow the bot in robots.txt. A snippet is below.', source: e.source });
      } else if (e.state === 'partial') {
        fixes.push({ level: 'warning', title: e.name + ' is only partly open', detail: e.reason, fix: 'Allow the user-fetch agent if you want people to be able to pull this page into answers.', source: e.source });
      }
    });
    var robotsSnippet = toAllow.length ? toAllow.map(function (t) { return 'User-agent: ' + t + '\nAllow: /'; }).join('\n\n') : '';
    groups.forEach(function (g) {
      g.checks.forEach(function (c) {
        if (c.status === 'fail' || c.status === 'warn') {
          fixes.push({ level: (c.id === 'I1' || c.id === 'I2' || c.id === 'A7') && c.status === 'fail' ? 'blocker'
            : c.status !== 'fail' ? 'improve' : g.id === 'A' ? 'warning' : 'fix',
            id: c.id, title: c.title, detail: c.detail, fix: c.fix, lost: c.points - c.earned, source: c.source });
        }
      });
    });
    var order = { blocker: 0, warning: 1, fix: 2, improve: 3 };
    fixes.sort(function (a, b) { return order[a.level] - order[b.level] || (b.lost || 0) - (a.lost || 0); });

    var llmsStatus = bundle.llms ? bundle.llms.status : null;
    return {
      url: pageUrl,
      mode: ua || bundle.live ? 'live' : 'paste',
      score: score,
      band: score >= 85 ? 'strong' : score >= 65 ? 'fair' : 'weak',
      engines: engines,
      training: training,
      groups: groups,
      fixes: fixes,
      robotsSnippet: robotsSnippet,
      facts: {
        wordCount: wordCount, title: title, h1: h1, headingCount: headings.length, schemaTypes: schemaTypes,
        internalLinks: internalCount, robotsStatus: robotsStatus,
        llmsTxt: llmsStatus === null || llmsStatus === undefined ? 'unknown' : llmsStatus === 200 ? 'present' : 'absent'
      }
    };
  }

  function toMarkdown(r) {
    var lines = ['# AI searchability audit', '', r.url, '', '**Page score: ' + r.score + '/100 (' + r.band + ')**', '', '## Engine access', ''];
    r.engines.forEach(function (e) { lines.push('- **' + e.name + '**: ' + e.state + '. ' + e.reason); });
    lines.push('', '## Fix list', '');
    if (!r.fixes.length) lines.push('Nothing to fix.');
    r.fixes.forEach(function (f, i) { lines.push((i + 1) + '. **' + f.title + '** (' + f.level + '). ' + f.detail + ' ' + f.fix + ' Source: ' + f.source.label); });
    if (r.robotsSnippet) lines.push('', '```', r.robotsSnippet, '```');
    lines.push('', '## Scores by group', '');
    r.groups.forEach(function (g) {
      if (!g.unscored) return lines.push('- ' + g.name + ': ' + g.earned + '/' + g.max);
      var bad = g.checks.filter(function (c) { return c.status === 'fail' || c.status === 'warn'; }).length;
      lines.push('- ' + g.name + ' (not scored): ' + (bad ? bad + ' issue(s)' : 'no issues'));
    });
    return lines.join('\n');
  }

  // ---------- site-wide audit ----------

  var STATE_RANK = ['blocked', 'signin', 'firewall', 'restricted', 'partial', 'unknown', 'open'];
  var SITE_WIDE = ['A5', 'D1', 'E1', 'E2', 'E5'];

  // Audits one page of a site. shared = { robots, sitemap, llms, notFound } fetched once for the site.
  function auditSitePage(shared, p, parse) {
    shared = shared || {};
    var report = audit({ url: p.url, page: p.page, ua: p.ua || null, live: true,
      robots: shared.robots, sitemap: shared.sitemap, llms: shared.llms, notFound: shared.notFound, home: shared.home }, parse);
    return { url: p.url || report.url, finalUrl: report.url, score: report.score, band: report.band, issues: report.fixes.length, report: report };
  }

  function auditSite(site, parse) {
    return buildSite(site, (site.pages || []).map(function (p) { return auditSitePage(site.shared, p, parse); }));
  }

  // Combines page results from auditSitePage into one site report.
  function buildSite(site, pages) {
    var total = pages.length;
    var score = total ? Math.round(pages.reduce(function (n, p) { return n + p.score; }, 0) / total) : 0;

    var engines = ENGINES.map(function (e) {
      var per = pages.map(function (p) { return { url: p.url, e: p.report.engines.filter(function (x) { return x.id === e.id; })[0] }; });
      var open = per.filter(function (x) { return x.e.state === 'open'; }).length;
      var worst = per.slice().sort(function (a, b) { return STATE_RANK.indexOf(a.e.state) - STATE_RANK.indexOf(b.e.state); })[0];
      var affected = worst ? per.filter(function (x) { return x.e.state === worst.e.state; }).length : 0;
      return { id: e.id, name: e.name, state: worst ? worst.e.state : 'unknown', open: open, total: total, note: e.note || '',
        reason: !worst ? 'No pages were audited.' : open === total ? 'Open on all ' + total + ' pages audited.'
          : affected + ' of ' + total + ' pages affected. For example ' + worst.url + ': ' + worst.e.reason,
        source: SOURCES[e.source] };
    });

    var byKey = {}, fixes = [], order = { blocker: 0, warning: 1, fix: 2, improve: 3 }, tokens = [];
    pages.forEach(function (p) {
      p.report.fixes.forEach(function (f) {
        var key = f.id || f.title, item = byKey[key];
        if (!item) {
          item = byKey[key] = { id: f.id, level: f.level, title: f.title.replace(' this page', ' some pages'), detail: f.detail, fix: f.fix,
            source: f.source, lost: 0, pages: [], siteWide: SITE_WIDE.indexOf(f.id) >= 0 || /^robots\.txt returns/.test(f.title) };
          fixes.push(item);
        }
        if (order[f.level] < order[item.level]) item.level = f.level;
        item.lost += f.lost || 0;
        item.pages.push(p.url);
      });
      (p.report.robotsSnippet.match(/User-agent: (\S+)/g) || []).forEach(function (m) {
        var t = m.replace('User-agent: ', ''); if (tokens.indexOf(t) < 0) tokens.push(t);
      });
    });
    var reach = function (f) { return f.siteWide ? total : f.pages.length; };
    fixes.sort(function (a, b) { return order[a.level] - order[b.level] || reach(b) - reach(a) || b.lost - a.lost; });

    var first = pages[0] ? pages[0].report : null;
    return {
      mode: 'site', url: site.origin || site.url, score: score, band: score >= 85 ? 'strong' : score >= 65 ? 'fair' : 'weak',
      pages: pages, engines: engines, fixes: fixes, sampled: site.sampled || {}, errors: site.errors || [],
      robotsSnippet: tokens.map(function (t) { return 'User-agent: ' + t + '\nAllow: /'; }).join('\n\n'),
      training: first ? first.training : [], facts: first ? first.facts : {}
    };
  }

  function siteToMarkdown(r) {
    var lines = ['# AI searchability audit: whole site', '', r.url, '',
      '**Average page score: ' + r.score + '/100 (' + r.band + ') across ' + r.pages.length + ' pages**', '', '## Engine access', ''];
    r.engines.forEach(function (e) { lines.push('- **' + e.name + '**: ' + e.state + '. ' + e.reason); });
    lines.push('', '## Fix list', '');
    if (!r.fixes.length) lines.push('Nothing to fix.');
    r.fixes.forEach(function (f, i) {
      lines.push((i + 1) + '. **' + f.title + '** (' + f.level + ', ' + (f.siteWide ? 'whole site' : f.pages.length + ' of ' + r.pages.length + ' pages') + '). ' + f.fix + ' Source: ' + f.source.label);
    });
    if (r.robotsSnippet) lines.push('', '```', r.robotsSnippet, '```');
    lines.push('', '## Pages', '');
    r.pages.forEach(function (p) { lines.push('- ' + p.score + '/100 ' + p.url + ' (' + p.issues + ' issues)'); });
    return lines.join('\n');
  }

  return { audit: audit, auditSite: auditSite, auditSitePage: auditSitePage, buildSite: buildSite, siteToMarkdown: siteToMarkdown, parseRobots: parseRobots, isAllowed: isAllowed, toMarkdown: toMarkdown, SOURCES: SOURCES, ENGINES: ENGINES, TRAINING: TRAINING };
});
