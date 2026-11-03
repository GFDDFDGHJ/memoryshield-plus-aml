# MemoryShield+ — Textual v3.17.1

## Search input-length repair (2026-11-03)

This patch builds on commit
[`00b0d22cca1739d4a13867faa7254d68b233519e`](https://github.com/GFDDFDGHJ/memoryshield-plus-aml/commit/00b0d22cca1739d4a13867faa7254d68b233519e).
It is a retrieval input compatibility change, not a claim that an existing Full
ran one fixed commit throughout. The platform's original task label is v3.14.0;
the deployed source has changed during that task. No new evaluation is created
by this patch, and its formal checkpoint eligibility is not certified here.

Search first makes the same original text-embedding-v4 request. Only a structured
HTTP 400 `InvalidParameter` identifying the input-length range enables fallback.
All queries accepted by the original request retain that exact vector. Quota,
authentication, network, rate-limit and other parameter errors do not trigger it.

Rejected queries are split in order at Unicode code-point boundaries, at most
4096 UTF-8 bytes per chunk and 128 chunks. Every character, including the tail,
is retained. The same model and dimensions encode batches of at most ten. The
UTF-8 byte-weighted mean is L2-normalized; the original query remains unchanged
for lexical matching, query planning and reranking. This is an approximation:
it does not recreate an unavailable whole-query embedding or guarantee scores.

The [official API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
limits each v4 text to 8192 model tokens. A byte budget is conservative engineering,
not an exact token count; the error's 33000 range is not treated as a token limit.
A rejected chunk still fails, without recursive retry. Dispatch stops after a
60-second fallback budget; each chunk request's transport timeout is bounded by
the remaining budget (transport phase timeouts are not a hard wall-clock cap).
Over-budget, invalid indices/dimensions, non-finite values and zero-direction
vectors fail explicitly. There is no truncation, fabricated hit or success on
provider failure. The rare fallback adds model calls for the chunks.

Add extraction, source embeddings, prompts, database schema and stored data are
unchanged. Models remain gpt-4o-mini / text-embedding-v4 / qwen3-rerank. Runtime
version becomes v3.17.1; diagnostic version remains v3.16.2. On the original host,
the runtime directory and `aml_text_gpt_v317.db` retain their historical names.
The address and authentication are unchanged. The gateway's new named selector
`gpt_v3171` uses the same backend port; the older selector remains for rollback.

Run `python -m unittest -v test_query_embedding_length` for invented-data,
network-blocked checks, or `python run_offline_tests.py` for regression groups.
No official memory, query or answer was read to implement this repair. No paid
probe is required or automatically dispatched.

Offline validation for this repair passed 22 focused length-recovery tests,
44 retrieval/Add test groups (after updating the release-version assertion),
86 gateway tests and 43 portable-launcher checks. The optional old-source
diagnostic comparison is skipped when its explicitly pinned file is absent.
These checks use invented data and do not certify completion of the Full.

## Historical v3.17.0 deployment and method disclosure

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

That diagnostic-only update kept method/API version **v3.17.0**; its observability version is
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
