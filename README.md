# MemoryShield+ — Textual candidate v3.17.0

**Independent candidate: not deployed, not approved for the existing Full, and
without an official Smoke or Full result.** The original bound URL continues to
serve method v3.16.0, diagnostic patch v3.16.1 and gateway v1.1.2. This checkout
and its source archive do not switch that deployment.

For independent local validation, follow [DEPLOYMENT.txt](DEPLOYMENT.txt) and use
`ops/run-portable.ps1`: localhost port 18080, one worker, private credentials,
and a new `data/aml_text_gpt_v317.db`. Historical machine-specific scripts and
`ingress/` in Git describe previous deployments; they are excluded from this
portable archive and are not v3.17.0 launch instructions.

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

These checks do not certify full-suite runtime, budget or platform concurrency.
A standalone backend can still return retryable 503 on overload. The existing
original-host FIFO gateway is a separate deployment component and is not
installed by this package. Organizer confirmation is still needed for the new
version's evaluation arrangement and any reuse of earlier checkpoints.

## Source and disclosure

Repository: https://github.com/GFDDFDGHJ/memoryshield-plus-aml

This candidate builds on `9f42b2af4d280b89440fa3cc6bb4ece2bd3de47e`.
Underlying baselines and third-party attribution remain in NOTICE.txt,
EVALUATION.md and LICENSE. The author approved MIT licensing; third-party code
retains its own notices. Repair tests use invented data; official evaluation
source, questions and answers were not inspected for tuning.

`prepare_submission.py` packages an explicit source allowlist. It excludes
credentials, operational databases, logs, evaluation artifacts and historical
machine-specific launchers. Publishing this candidate does not claim approval
or completion of a formal evaluation.
