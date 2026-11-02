# MemoryShield+ — Textual v3.17.0

**Deployed on the original URL; official evaluation pending.** On 2026-10-31 at
14:26:46 China Standard Time (UTC+08:00), the original bound URL
https://hlq5070.tail50f4d4.ts.net switched to method v3.17.0,
`raw_on_unmatched`, gateway v1.1.3 and a separate database. The earlier v3.16.0
backend and its database remain available. This version has not passed official
Smoke or Full; no official task was resumed or created by the deployment, and
compatibility with the earlier Full checkpoint remains unconfirmed.

For independent local validation, follow [DEPLOYMENT.txt](DEPLOYMENT.txt) and use
`ops/run-portable.ps1`: localhost port 18080, one worker, private credentials,
and a new `data/aml_text_gpt_v317.db`. The online deployment uses the separate
host-specific `ops/watch-isolated-v317.ps1` in Git. That supervisor, `ingress/`
and historical machine-specific launchers are excluded from the portable
archive. Running the portable package does not switch or manage the online URL.

## Method change

The earlier implementation failed an entire Add when a GPT-selected quote was
absent from its declared source or its integer source reference was unknown or
inconsistent. The new default `raw_on_unmatched` policy rejects those derived
selections, while retaining other verified selections and all original messages.

Every new nonempty Add still calls official **gpt-4o-mini**. Model identity,
completion status, response structure, source-index types, quote types and
lengths, evidence kinds and usage metadata remain validated. Provider failures,
refusals, incomplete output and protocol errors still fail Add. Unknown sources
are never guessed by searching other messages. Only exact substrings in their
declared sources become stored spans, with actual offsets and source roles.
There is no Unicode normalization, fuzzy matching or invented memory.

An otherwise valid response whose selections are all unverifiable may produce
zero GPT spans. Original messages, embeddings, deterministic facts, real model
usage and the request receipt still commit together. Those messages may lose the
GPT ranking bonus, which can affect retrieval quality. This is a method change,
not an equivalent repair or a promise of higher scores. Rejection logs contain
only fixed categories and integer counts, without source text or identifiers.

`strict` preserves the earlier all-or-nothing behavior.
`fail_if_all_unmatched` retains valid selections but fails when every proposed
selection was rejected. These policies support explicit comparison; the portable
launcher pins `raw_on_unmatched`, and health discloses the actual active policy.

## Unchanged components

Search returns evidence and never generates final answers. Its source ranking,
temporal/state rules, privacy exclusions and optional qwen3-rerank are unchanged.
Add uses gpt-4o-mini at https://api.openai.com/v1. Embeddings use Beijing DashScope
text-embedding-v4, standard mode, 1024 dimensions and batches of at most ten.
Model concurrency remains two; per-call and total GPT budgets remain 60 and
180 seconds. Committed receipts prevent repeat model calls. The new database
does not migrate or reuse the existing official evaluation database.

## Diagnostic-only update

Method/API version remains **v3.17.0**; the separate observability version is
**v3.16.2**. An incomplete GPT response now records only a fixed finish-reason
category and bounded integer token usage when supplied. No source text,
response content, business identifiers, headers or exception strings are added
to these diagnostics. Incomplete responses still fail with the same error;
this update does not fix them or change models, prompts, limits, retrieval or
transaction behavior.

The pre-diagnostic v3.17.0 source baseline is commit
[`137f7610d29edb63ad68fb867821aae4eb7ecae3`](https://github.com/GFDDFDGHJ/memoryshield-plus-aml/tree/137f7610d29edb63ad68fb867821aae4eb7ecae3).
This publication adds a diagnostic delta to that baseline. It does not claim
that an already-started Full originally ran this newer diagnostic commit.

Run the focused offline checks with
`python -m unittest -v test_add_memory test_add_failure_observability test_finish_diagnostics`.
The offline runner also treats the new diagnostic tests as modern tests,
without replacing the real extractor with the legacy test hook. An optional
old-source comparison runs only when `AML_FINISH_BASELINE_SOURCE` explicitly
points to an independent file matching the pinned pre-diagnostic source SHA;
otherwise that comparison is skipped. These tests make no provider requests.

## Validation and capacity

`run_offline_tests.py` runs invented-data regression and integration checks.
`ops/test_portable_launcher.ps1` checks the portable launch configuration.
Opt-in `probe_gpt_add.py` uses invented messages and a disposable database for a
small paid provider check. This is not an official Smoke and grants no formal
evaluation eligibility.

Candidate validation on 2026-10-31 passed all 42 offline groups and 43 portable
launcher checks. A separate real-provider probe on invented messages returned
Add/Search 200, rejected one ungrounded selection, retained two exact spans and
all three original messages, and passed receipt replay and user isolation. Its
database was temporary; no deployed service or official task was changed.

After deployment, invented-data Add/Search, receipt replay and user isolation
checks also passed on the original URL. Independent anonymous health checks
from Singapore, China and the United States returned HTTP 200, valid TLS and
v3.17.0. These observations do not replace official Smoke or Full.

These checks do not certify full-suite runtime, budget or platform concurrency.
A standalone backend can still return retryable 503 on overload. The existing
original-host FIFO gateway is a separate deployment component and is not
installed by this package. Organizer confirmation is still needed for the new
version's evaluation arrangement and any reuse of earlier checkpoints.

## Source and disclosure

Repository: https://github.com/GFDDFDGHJ/memoryshield-plus-aml

This version builds on `9f42b2af4d280b89440fa3cc6bb4ece2bd3de47e`.
Underlying baselines and third-party attribution remain in NOTICE.txt,
EVALUATION.md and LICENSE. The author approved MIT licensing; third-party code
retains its own notices. Repair tests use invented data; official evaluation
source, questions and answers were not inspected for tuning.

`prepare_submission.py` packages an explicit source allowlist. It excludes
credentials, operational databases, logs, evaluation artifacts and historical
machine-specific launchers. Publishing this version does not claim approval
or completion of a formal evaluation.
