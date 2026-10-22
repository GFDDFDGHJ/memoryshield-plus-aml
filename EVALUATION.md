# Evaluating memory behavior

An AML Smoke result is a compatibility and limited quality observation. It is
not a substitute for a complete evaluation, and a higher Smoke average is not
by itself a release criterion. Aggregate scores can hide a lost capability.

The participant implements Add and Search. These local checks assess the
evidence made available to the answer model; they do not measure the complete
answer-generation and judging pipeline and do not predict a Full score.

## Paired comparisons

Freeze a source snapshot, fixtures, evaluator, embedding model, vector cache,
and context budgets before comparing a candidate. Run the same fixtures and
vectors against both implementations. Reports retain source, fixture,
evaluator, and vector hashes. Generate additional deterministic seed variants
only after candidate changes are frozen, and retain their results separately.

Use invented data and disposable user scopes. The evaluation must not open a
deployment database, official questions or answers, or participant secrets.
Real embedding preparation is a separate explicit step; cached comparisons
must run without provider access.

## Behavior coverage

Evaluate source coverage and fidelity in each family, together with failure
examples, rather than optimizing a single combined pass count:

- Stated facts and attribution: distinguish people, roles, quoted statements,
  repeated observations, negation, and unrelated records.
- Connected evidence: retain intermediate sources and independent causes when
  the query uses different wording from the original conversation.
- Time: retain the conversation date that anchors relative language; distinguish
  event dates, recording timestamps, insertion order, and unknown date anchors.
- State: retain previous values, explicit corrections, uncertainty, and genuine
  retractions without turning another speaker's statement into a user fact.
- Synthesis: cover early, middle, and late sources across topics and sessions;
  disclose partial coverage and preserve units, conditions, and observations.
- Rule execution: retain applicability, exceptions, priority, and output
  constraints. Equivalent requests must receive equivalent evidence budgets.
- Privacy: do not pull unrelated private topics into a narrow task; preserve
  evidence needed for an authorized personal query.
- Streaming: compare one-batch and incremental ingestion, queries between
  updates, corrections, and reuse of relevant earlier information in a new
  session. Retries and distinct repeated observations have different meanings.

Vary wording, language, history length, evidence position, session boundaries,
and distractor density. Check evidence visible at 8k and 16k characters with
Top K 100, source identity, forbidden unrelated evidence, and evidence rank.
These prefix sizes are local stress budgets, not asserted AML platform limits.
Controlled vectors isolate packing and selection behavior; shared real vectors
are also needed to test semantic retrieval.

Unrelated material is context contamination; only some such material is private.
Minimal disclosure checks concern evidence sent to the downstream model, not a
demonstrated leak in the final answer. Source-order checks do not infer event
order when the original messages do not explicitly establish it. Source fidelity
checks verify attributed raw quotations and decoded literal source tables, not
the semantic correctness of every extracted fact or state label.

## Release and official runs

Keep protected capability checks alongside new cases. Record regressions and
unresolved limitations; do not adjust a fixture merely because a candidate
fails it. Fix demonstrably invalid assumptions as a separately versioned test.

Publish the complete implementation and its configuration together. Deploy
before starting a run, record the source revision and runtime version, and
keep that version fixed until the run finishes. Request audit records include
the runtime version without message content or credentials. Keep the existing
participant URL and authentication stable when updating an implementation.

Use the next Smoke as an external check after offline comparisons. Start Full
only with a declared frozen version and sufficient confidence in broad
capability coverage; local evidence tests cannot guarantee its result.

## rc4 candidate comparison

The frozen rc3 baseline and rc4 candidate use the same 47 invented scenarios,
evaluator v1.1, Top K 100, and a shared cache of real `text-embedding-v4` vectors.
The second seed changes generated names and wording within the same scenario
design; it is a robustness variant, not 47 additional independent tasks.

| Visible evidence budget | rc3, seed 20261022 | rc4, seed 20261022 | rc3, seed 20261031 | rc4, seed 20261031 |
|---|---:|---:|---:|---:|
| First 8,000 characters | 233 / 306 | 285 / 306 | 233 / 306 | 285 / 306 |
| First 16,000 characters | 286 / 306 | 300 / 306 | 287 / 306 | 301 / 306 |

Counts are required evidence markers, with many numeric observations in data
scenarios. They are not equally weighted capability scores. Strict scenario
passes change from 38/47 to 41/47 on both seeds. At 16k, unrelated-context flags
change from three scenarios to two. The remaining flags concern instructions
for other workflows; they are not demonstrated final-answer privacy leaks.
Quotation/source consistency checks pass on both versions. The protected fact,
multi-hop, state, personalization, and streaming scenarios remain passing.

Regressions and limits remain visible:

- A Chinese long-history case moves two key sources beyond the first 8k; all
  remain available within 16k. A broad paraphrased synthesis query still misses
  relevant sources, and arbitrary implicit workflows are not fully modeled.
- The source-order proxy remains failing with real vectors; this does not prove
  event-time ordering is wrong, because that fixture only specifies source order.
- The older 27-check evaluation now passes 26/27: unrelated garden instructions
  can appear after the exact answer. The permanent score-gap deletion that kept
  this context absent also deleted an independent causal source in a separate
  paraphrased question. The fixture and its failed result are retained.
- Small-case source fidelity does not prove every extracted assertion is valid.
  Long single-paragraph tails and lowercase entity bridges remain limitations.

All 231 local checks pass, including original controls and new timestamp,
schema, correction, relation, privacy, and data-validity regressions. The old
37-check evidence evaluation and 4/4 plus 7/7 real-vector controls also pass.
An independent counterexample verifies that a later statement invalidating
previous readings is retained before their numeric evidence, rather than
silently treating invalid observations as complete usable statistics.

Frozen algorithm source SHA256:

```text
store.py  86ff46724321b98d23f521357d16d9148cd775646bef4d548f5dac1d9b4f0734
facts.py  1b4f94147274018bec5ca4478bfde4adee105fe53af36ea0ce398d013635b9a6
```

Fixture SHA256 (first seed): `f1e51c0c71d563dc145451f8ed4490d277ab2ccef7add7e88574fe4ade018943`.
Evaluator SHA256: `4c2964c22ae001bab97bb95901717775db4167286a38688804d625c59767a897`.
Shared vector cache SHA256 (first seed): `1ecccbc036bde7eab51b79c576acf0ff01526185eb216b928cdc3dcd806e383c`.

## rc5 capability preservation

rc5 preserves unresolved personal-state evidence instead of treating the fact
parser as a complete whitelist. Narrow property excerpts retain adjacent
effective-date qualifications and continue to exclude recognizable unrelated
properties. A preserved statement is evidence, not a confirmed replacement.

Explicit response policies apply to factual answers as well as execution
requests. Applicable rules use result and character budgets rather than a
twelve-rule cutoff; explicit local domains remain local. Rule extraction no
longer discards qualified instructions after the twelfth item, and source
recovery keeps their full adjacent exception chains. Same-source execution
protocols remain attached to matching rule evidence even when they do not
repeat the queried variables.

A shared source-aware scope plan recognizes conservative references to the
speaker's prior communication. An unmatched concrete topic does not become
permission to return the whole history. Context packing compares literal and
reversible numeric encodings, preserves required qualifications, covers source
boundaries, and fills the remaining budget with whole source spans.

All 275 local checks pass: the previous 231, 25 new state/rule/privacy checks,
and 19 source-planning/budget checks. The same new 25 checks pass 12/25 on
frozen rc4 and 25/25 on rc5. Two independent execution-protocol variants change
from 0/2 to 2/2 for complete operational evidence at 8k, 16k and full context.
These are invented evidence tests; no official conversations or questions
were used.

The frozen 47-scenario real-vector evaluation is unchanged on both seeds:
41/47 strict scenario passes; 285/306 evidence markers at 8k; 300/306 and
301/306 at 16k respectively. Per-scenario marker, quotation, scope-contamination
and ranking comparisons find no new regressions. The previously documented
six strict-scenario failures and the older 26/27 context result remain visible.

The new tests do not establish final-answer correctness, comprehensive privacy
protection or an expected official score. Unknown languages/property grammars,
abstract domain summaries without source anchors, and very long indivisible
paragraphs remain limitations. No model, embedding dimension, storage schema,
participant URL or authentication change is required.

Frozen rc5 algorithm source SHA256:

```text
store.py  49b02a229545c25822354ef3c47359ec0ed45bf53c9dbbdb073b94f510a885c6
facts.py  6eb00bbfe4b3621eab05528aaaa7094bb728a41c100fb6b49da47e3d5dfcb34f
```
