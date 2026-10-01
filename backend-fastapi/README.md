# backend-fastapi

The scan engine. FastAPI serves a small API; each scan drives a real headless
Chromium through Playwright and uses Claude (Opus 5.5 by default) to explore the
app and judge what it finds.

## How a scan works

| Stage | Who does it | What it produces |
| --- | --- | --- |
| 1. Address check | `security.py` | Refuses private, loopback, link-local and metadata addresses, on every request the browser makes, not only the first URL |
| 2. Evidence | Playwright (`scanner/browser.py`) | Console errors, uncaught exceptions, failed and erroring requests, dialogs, load timing, page metadata, axe-core accessibility violations, broken links. Each becomes an entry with an id like `obs-12` |
| 3. Browser checks | `scanner/findings.py` | Deterministic findings from that evidence. No AI involved, so these cannot be hallucinated |
| 4. Exploration | Claude (`scanner/agent.py`) | Claude drives the browser through tools (click, fill, navigate, screenshot) to exercise real flows. Every action is logged as `act-N` with what changed |
| 5. Review | Claude (`scanner/reviewer.py`) | A second pass that merges duplicates, drops noise, and calibrates severity against the cited evidence |

## How false positives are handled

This is the part of the brief the design is built around.

1. **Evidence first.** Half the report comes from things the browser verifiably recorded. Those findings are marked "Browser verified" and carry high confidence by construction.
2. **Claude must cite evidence.** `report_finding` requires `evidence_ids`. A finding that cites nothing real is kept but downgraded to low confidence and labelled ungrounded, and Claude is told so in the tool result, which pushes it to reproduce the problem instead of asserting it.
3. **A reviewer with limited power.** The review pass can keep, merge or drop, but the limits live in code, not only in the prompt. It cannot invent findings, cannot drop a high-confidence browser finding, and cannot raise the confidence of an ungrounded claim.
4. **Nothing disappears silently.** Everything filtered out is returned under `suppressed` with the reason, so a developer can see what was removed and overrule it.
5. **Page text is data.** The system prompt treats site content as untrusted, so a page cannot talk the agent into leaving its task.

## Findings export as Playwright tests

A finding is only useful until it is fixed; a test keeps it fixed. `scanner/playwright_export.py`
turns each finding into a `@playwright/test` spec:

- **Steps** are the browser actions the scan recorded. For each element Claude touched, the
  scanner computes candidate locators in Playwright's recommended order (test id, role and
  accessible name, label, placeholder, then CSS and text) and keeps the first that Playwright
  confirms matches exactly one element. Icon fonts in accessible names fall back to a non-exact
  role match, still proven unique. A step with no unique locator becomes a TODO, never a guess.
- **The assertion** comes from a structured `expectation` on `report_finding`: one checkable fact
  from a fixed set (text visible or absent, URL, element state or text, no console errors). The
  server renders it; model output only ever lands inside escaped string literals, so page content
  cannot prompt-inject executable code into the file.
- **Replay before export.** The test's own steps are replayed in a fresh, isolated browser
  context (SSRF guard included), and the assertion is checked there. `fails-now` means the test
  catches the issue; `passes-now` flags one that may not; `unverified` is never presented as
  proven.
- **Which steps.** From the first cited action, walking back over the fields filled just before
  it, to the last cited action. Earlier or later attempts are separate experiments.

`GET /scans/{id}/tests.spec.ts` downloads every test as one suite. Verified end to end by running
a downloaded suite with the real Playwright runner: each test executed its steps and failed at
exactly the assertion the server predicted.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install --only-shell chromium
python scripts/fetch_axe.py       # accessibility rules, once
cp .env.example .env              # then put your key in ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000
```

Interactive API docs are at http://localhost:8000/docs. Point the frontend at it
with `VITE_API_URL=http://localhost:8000 npm run dev` from the repository root.

If your key is an organization-level key not scoped to a workspace, the Claude API
rejects every request until you also set `ANTHROPIC_WORKSPACE_ID`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/scans` | Start a scan. Body: `{"url": "...", "options": {"maxPages": 5}}`. Returns `202` with an id |
| `GET` | `/scans/{id}` | Poll status, live progress, findings, usage and cost |
| `GET` | `/scans/{id}/tests.spec.ts` | Every finding's Playwright regression test as one downloadable suite |
| `GET` | `/scans/{id}/report.md` | The finished report as Markdown, ready to paste into an issue |
| `GET` | `/scans?ids=a,b,c` | Summaries for the history sidebar (up to 30 ids). Unknown or expired ids are left out |
| `DELETE` | `/scans/{id}` | Delete the record and every stored screenshot. Needs the `X-Owner-Token` returned when the scan started |
| `GET` | `/healthz` | Liveness, model, and whether Claude is configured |

## History without accounts

There is no login, so there is deliberately no endpoint that lists everyone's scans.
Starting a scan returns a random owner token; the browser keeps the scan id and token
locally, and the server stores only the token's SHA-256 hash, compared in constant time.
The sidebar asks for summaries of the ids it holds, and only the holder of a token can
delete that scan. Deleting removes the screenshots first, then the record, so a failure
part-way leaves the scan listed and retryable rather than leaving orphaned files.

## Guardrails for a public endpoint

Every scan spends real API credit, and the service opens arbitrary URLs from inside AWS.

- **SSRF:** hostnames are resolved and every address must be public. The guard is enforced inside the browser for all requests, so redirects and links cannot reach the VPC or the metadata service.
- **Cost:** per-client rate limit, a cap on concurrent scans, a per-scan step budget, and a wall-clock limit that asks Claude to wrap up before it is cut off. Two depths: quick (30 actions, 4 minutes, the default) and thorough (100 actions, 30 minutes, one at a time, so a quick scan always has a free slot). The API reports token usage and estimated cost per scan.
- **Safe exploration:** the agent is told to use obviously fake data, never complete purchases or change settings, and stay on the site under test. Navigation off the site is refused in code.

## Choices worth knowing about

- **Manual tool loop, not the SDK tool runner.** The loop needs a budget that injects a wrap-up note, a progress event per action, and evidence checking on every report. History is append-only, which Opus 5.5's thinking requires.
- **Opus 5.5 specifics.** Thinking cannot be disabled, so `output_config.effort` is the only dial. Forced `tool_choice` is rejected, so tools are `strict` with `auto` choice. The review step uses structured outputs validated by Pydantic.
- **Degrades instead of failing.** If the Claude API is unreachable or rejects the key, the scan still returns every browser finding with a note saying the AI stages did not run.
- **One uvicorn worker.** Scans run as background tasks in-process; the rate limiter and concurrency cap live in that process. Scaling out would move scans onto a queue.

## Tests

```bash
pytest -q && ruff check app tests scripts
```

The suite covers the SSRF guard, the deterministic checks, the reviewer's limits,
evidence grounding, and the HTTP API. It needs no browser and no API key.
