---
name: liq-live-testing-chrome
description: >
  Use when deciding whether to verify something on LIQ's live pages via
  direct browser automation (claude-in-chrome), or whether to hand it to
  Code's harness instead. Trigger before starting any browser-based check,
  not after it's already timed out twice. Covers the two-deployment
  distinction (WordPress vs Railway backend) and the chunked-read
  workaround for large page pulls. Claude-in-Chrome is not reliable for
  volume, and this skill exists so that gets decided before the attempt,
  not discovered mid-task.
---

# LoyaltyIQ Live Testing via Claude-in-Chrome

## Core principle

Claude-in-Chrome is the right tool for a small number of precise,
read-only checks against a live page, confirming one computed style,
confirming one element exists or doesn't, a single visual spot-check. It
is not a batch-testing harness, and treating it as one has already cost
real time on this project, per Design's own skills assessment (E1, the
single most impactful infrastructure issue raised, repeated timeouts,
empty screenshots, mid-session drops).

## Live demonstration, from drafting this exact section

While confirming the Railway backend's actual URL, to write it correctly
into this file rather than repeating a document's claim unverified, two
consecutive calls against the live Railway origin, `get_page_text` then
`javascript_tool`, both failed identically: no result after 4 minutes,
with the tool's own error stating further calls would likely time out the
same way. This isn't a secondhand report of past instability, it's what
happened in the middle of writing this skill. The connector's
unreliability isn't historical, it recurred tonight, on the first attempt
to use it for exactly the kind of spot-check this skill says is the
right use case, and it still failed.

**What that means for method, not just for this one fact:** when a tool
call hits its own timeout and explicitly warns that repeating it will
likely fail again, stop. Don't spend a third attempt confirming what the
second attempt already told you. Fall back to a document source (with its
provenance stated honestly, as below) or hand off, don't keep paying the
same cost hoping for a different result.

## When direct browser verification is the right tool

- A single computed-style check (worked precedent, earlier this session:
  confirming `--trust-dark`/`--trust-light` resolved correctly via
  `getComputedStyle`, a handful of calls, succeeded cleanly).
- Confirming one specific element or class exists, or is absent (worked
  precedent: the trust-section markup-absence finding, one targeted grep
  equivalent via the DOM).
- Any check answerable in roughly one to three calls. If it's not
  answerable in that range, it's not a spot check anymore.

## When to hand off to Code's harness instead

- Any volume or batch query testing, dozens or hundreds of test queries
  against Mode 1/2/3 routing, belongs against the actual API endpoints
  (`/chat`, `/analyse`, etc.) via Code's Python harness, not the browser.
  The browser wasn't reliable for a handful of calls tonight, it will not
  hold up across a batch.
- Anything requiring many sequential page loads or round trips.
- Anything where a failed call needs to be distinguishable from a
  negative result. A browser timeout and "the element isn't there" can
  look similar if not handled carefully, Code's harness makes that
  distinction explicit, the browser tool does not.

## Recovery pattern

- If a call times out with a "further calls likely to fail" warning,
  don't retry the same tool immediately. Per this session's incident,
  switching from `get_page_text` to `javascript_tool` did not help, both
  failed the same way, so tool-switching isn't a reliable recovery on its
  own.
- `tabs_context_mcp` with `createIfEmpty` is the documented recovery for
  session/context loss specifically, not independently re-tested this
  session, since tonight's failure was a non-response from the tool
  itself, not a lost tab context.
- If recovery doesn't work within one retry, stop and fall back to a
  document source, the relevant platform's own dashboard/config (proved
  out just now, the Railway dashboard's Networking section confirmed the
  backend URL directly when browser verification had failed twice), or
  Code, rather than proceeding on an assumption. A platform dashboard is
  often a stronger source than a browser check would have been anyway,
  it's the actual configuration, not an inference from probing it.

## Two-deployment gotcha

- `liq.networkgrey.co.za` is WordPress, the Elementor-built pages.
- The Flask backend (`/health`, `/chat`, `/analyse`) is a separate Railway
  origin, public URL `web-production-0a66a.up.railway.app`, confirmed
  directly from the Railway dashboard's Public Networking section (21
  July 2026), not just cited from a document. Two things worth keeping
  distinct: this is different from `web.railway.internal`, which is the
  same service's *private* networking address, resolvable only between
  services inside Railway's own network, unreachable from a browser or
  `curl` outside it. If a check ever turns up the `.railway.internal`
  form, that's the wrong address for anything external, not an
  alternative. This session's Technical History document had already
  corrected an earlier project memory that wrongly recorded the
  WordPress domain as the Railway URL, this dashboard check confirms
  that correction was right, independent of the browser verification
  attempt above, which failed and couldn't confirm it either way.
- These are different origins entirely. A working WordPress page says
  nothing about backend health, and a healthy `/health` endpoint says
  nothing about whether WordPress is rendering correctly. Don't check one
  and report on the other.

## Chunked-read workaround for large page pulls

`get_page_text` has a documented history of timing out on some pages
(this session's incident is a fresh instance, not the first). The
fallback is `javascript_tool` with `document.body.innerText.slice(n, m)`,
reading the page in bounded chunks rather than pulling the full text in
one call. Smaller, bounded reads are both less likely to hit a timeout
and less likely to trip a length-based content filter on the tool's
response. Iterate the slice window across the page rather than
requesting everything at once. Tonight's incident shows this isn't a
guaranteed fix, `javascript_tool` failed too on this attempt, but it
remains the documented first fallback before escalating to "stop and use
another source."
