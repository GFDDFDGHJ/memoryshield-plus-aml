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

## v3.12.0: assertion context, numbered procedures and repeated source encoding

This medium update preserves the existing models, public Add/Search routes,
storage schema and source records. Development uses invented data and temporary
databases; no official raw task messages, questions or answers are inspected.

- Natural-language fact and preference mining now checks the local clause and
  quotation containing the match. Questions, reported quotations, hypothetical
  statements and explicitly historical descriptions do not retire a current
  assertion merely because they contain its first-person grammar. A later real
  assertion or correction in the same source remains independently eligible.
  Explicit structured triples retain their prior input semantics. This is a
  conservative attribution guard, not general truth or event-time inference.
  The final fixed fixture correctly rejects 1/31 versus 31/31 nonassertions,
  while retaining all 13 genuine assertion/correction controls in both versions.
  Original text remains available for retrieval. An explicit Actually/Correction
  update can retain a past-event date phrase without being mistaken for a
  historical aside; question, quotation and used-to guards still apply.
- Physical-line list ordinals remain attached to their original sentence.
  A matching step can retain a source-local consecutive operational list,
  heading and stated execution controls through original character intervals.
  It requires an explicit procedure heading and at least two valid numbered
  operational items; numbering gaps, blank lines, ordinary narration, private
  gaps and more than 24 segments stop whole-block expansion. Preview budgets
  retain the complete linked block or disclose omitted evidence. The fixed
  long-source case improves from 1/4 to 4/4 necessary literal inputs without
  repeating a step. This neither executes rules nor grants source authority.
- The over-budget compact view can encode at least three whole, identical,
  nonnumeric records with one zero-slot literal template and separate rows for
  each source RID, sequence, segment, session, role, timestamp and origin. Only
  complete single-sentence records with distinct RIDs qualify; fragments and
  repeated sentences within one record retain their previous representation.
  No genuine observation is deduplicated. When the normal literal view fits,
  its representation remains unchanged. Ordinary non-template rows share
  metadata without opening a separate envelope for each uncommon sentence.
  An independent decoder reconstructs every occurrence from returned text.

In a fixed 140-record Add/Search fixture (120 repeated background records and
20 distinct topics), the 8k prefix reconstructs 46 versus 119 source RIDs and
the 16k prefix 67 versus 140. Both versions retain all 20 topics and all 140
sources in the complete return, with unchanged role/literal fidelity. Overall
returned characters increase from 32,849 to 36,648 because complete compressed
digest coverage leaves room for additional raw evidence. The claim is improved
bounded evidence coverage, not universally shorter responses or better answers.

All 37 isolated offline scripts pass. Both original 47-case real-v4 cached
seeds retain every prior required evidence, source, role and rank contract
without new regression. Extended-context, state, table and long-source checks
retain 8/8, 12/12, 9/9 and 6/6 complete-evidence contracts. The exact three new
public capability fixtures also pass against a fresh offline temporary store
before deployment; live API and TLS checks are recorded separately.

The local evidence counts do not predict an official Smoke or Full score.

## v3.11.1: complete summary scope and qualified rule/state evidence

This medium update preserves the existing models, storage schema and public
Add/Search routes. Development uses invented fixtures and temporary databases;
official raw tasks are not inspected. The changes preserve source evidence
rather than generating answers or executing remembered instructions.

- A direct summary request with a complete personal-history or self object
  now supplies whole-history scope. Matching the entire request prevents a
  narrower complement such as "my memories of the kiln" from opening the
  whole history. Existing topic, quotation, numeric and private-field guards
  remain. In a separate fixed 16-paraphrase fixture, complete source coverage
  improves from 10/16 to 16/16; six changed requests improve from 65/67 of 128
  sources to all 128. Thirty-six negative controls retain their previous
  classification, and the older 16-case intent evaluator is unchanged.
- Rule evidence includes its immediately preceding explicit scope heading,
  including a consecutive rule list under that heading. It also retains
  consecutive Then/Next/Finally imperative actions and an explicit same-rule
  withdrawal sentence. Blank lines, narration and new scope headings stop
  expansion. General manner/feasibility headings retain their original words
  while scope classification keeps global response policies global. Ordinary
  Then/Next narration is not attached solely because its first word resembles
  a verb. The same fixed 17 invented rule contracts improve from 6/17 to
  17/17, including complete Search checks with low-similarity rules and
  110 distracting sources. This is not arbitrary domain or rule inference.
- The v3.11.1 patch distinguishes a response verb's bounded output manner
  from a business domain introduced by "in". Explicit lengths, formats and
  limited language forms keep their original text and global applicability;
  finance, emergencies, named projects, technical-field complements and
  request-trigger conditions remain local. A separate fixed 21-case check
  improves from 11/21 to 21/21. Public verification includes the previously
  omitted "Always reply in at most three sentences" policy under its general
  manner heading. Unsupported free-form output descriptions remain conservative.
- Unstructured English predicates no longer assert a negated complement such
  as a name of "not Eira" as a new positive value. Quoted literal values and
  explicit structured triples retain their original interpretation. An exact
  present-tense denial of the same self field/value can qualify an earlier
  assertion with CURRENT UNVERIFIED and the later original source. No new
  value is inferred and no stored assertion is retired by this selector.
  Twelve positive qualification fixtures improve from 0/12 to 12/12;
  thirty historical, modal, conditional, quoted, role/field and value-prefix
  controls have no added false selections. Full Search checks also verify
  source identity, role/user isolation and provided timestamp order.
- A literal condition actor's completed simple-past approval or denial can
  accompany that same object's source evidence. The new branch requires the
  outcome object tokens to be contained in the condition's object and obeys
  scope/private-field barriers. Five positive source-local contracts improve
  from 0/5 to 5/5, with no false additions in sixteen negative controls. Modal
  perfect forms such as "might have approved" remain uncompleted evidence.
  The service does not execute the condition or assert its target value.

All 34 isolated offline scripts pass. Both existing 47-case real-v4 cached
seeds retain every prior evidence, source, role and rank contract with no new
regression. Extended-context, state, data and long-source contracts retain
8/8, 12/12, 9/9 and 6/6 complete evidence respectively. An independent 12-case
rule review passes, including the global-style and narration counterexamples.
One synchronized 16-search cached-vector round has no errors, isolation or
literal failures; this does not measure public HTTP or provider throughput.

These counts measure selected, attributed original evidence and scoped query
behavior. They do not predict an official Smoke or Full answer score.

## v3.10.0: bounded synthesis and rule scope

This medium update retains the same models, routes, credentials, storage schema
and complete-source records. Its controls use only invented data, temporary
databases and unchanged cached model orders; no official raw task data was used.

- Global bounded context now orders each source's sentence queue by exact
  wording frequency before interleaving sources and sessions. A repeated
  document preamble can no longer postpone that source's uncommon body
  statement behind every other source's unique title. Required policies,
  validity conditions and schemas still take precedence. No new salience
  verbs or query triggers are introduced, genuine source RIDs are not merged,
  and the complete-view fast path is unchanged.
- Rules qualified after the response noun by `about/to/for` a topic, or by
  a request-trigger condition, are no longer elevated to global response
  policy. Language, output format and length controls remain global when
  independently unqualified. Topic-matching retrieval still retains the
  original local instruction with its source.
- Identifier linking can supplement the first four raw sources when their
  returned bodies are already excerpts. Missing safe background spans are
  appended to that same result, with original character ranges and source
  identity; score and role do not change. Existing visible literal spans are
  not appended again. The supplement explicitly is not a new observation;
  it is not a claim to reconstruct complete repeated-source statistics.
- Explicit personal-name/nickname exclusions apply to labelled source and
  derived fact/profile fields. Technical possessive names remain eligible.
  A past/habitual question about omission does not execute that omission.
  Ordinary and quoted name queries retain their original interpretation.
  Unlabelled free-form person mentions remain outside this narrow field grammar.

In the unchanged multi-domain progression fixture, the embedded global history
improves from 5/30 to 22/30 necessary literal claims at 8k and from 5/30 to
30/30 at 16k. Complete return remains 30/30; standalone and narrow-topic
controls retain their previous coverage. Independent fixtures cover buried
statements, the short sentence `I quit.`, 241 repeated observations with
distinct pointers, policy/schema/validity priority and privacy boundaries.
Unrecognized global-synthesis formulations remain disclosed failures; this
measures evidence visibility rather than summary-answer quality.

The fixed long-source diagnostic with cached real qwen3-rerank orders improves
from 5/6 to 6/6 complete evidence contracts. A separate new explicit-protocol
case restores four necessary unit/conversion/eligibility clauses when its
source is an excerpted head candidate; complete short seeds, private fields,
source role and TopK1 have negative controls. No new paid ranks were required.

Both real-v4 seeds of the unchanged 47-case evaluator retain all prior
required evidence, role, source-fidelity and rank checks: 42/47 strict,
291/306 required literals at 8k and 306/306 at 16k. Extended-context,
state-evidence and table contracts remain 8/8, 12/12 and 9/9 respectively.
Thirty isolated offline scripts pass; live availability is checked separately.
These local counts do not estimate an official Smoke or Full score.

## v3.9.0: late arrivals, execution evidence and explicit tables

This is a medium update from v3.8.0 (previously labelled v3.8 / rc8). The
embedding model, optional reranker, routes, credentials and database schema
are unchanged. All new diagnostics use invented data and temporary databases;
no official messages, questions, answers or raw traces were inspected.

- On Add, the affected single-valued fact key is replayed independently by
  source role. If every relevant utterance has a provided message timestamp,
  timestamp, source sequence and sentence position establish assertion order.
  Mixed or unknown origins retain source sequence; this is not event-date
  inference. Same-value reassertions retain distinct source pointers. No full
  database migration or search-time global replay is introduced.
- Current, historical-value and profile views quote explicit later governance
  evidence with `CURRENT UNVERIFIED`. They do not apply a condition or infer
  a new value. Completed withdrawal and same-source affirmative outcome
  controls differ from denied, hypothetical, question, nickname and other-role
  controls. Unsupported forms remain ordinary source evidence.
- Operational grammar distinguishes instructions to execute/skip rules from
  notebook mentions of those words. Long previews prioritize complementary,
  less-repeated literal spans; whole exceptions and protocol constraints still
  obey the budget or receive an incomplete-evidence notice. Saturated/failed
  optional reranking retains dense/sparse recall rather than only dense order.
- Explicit delimited table schemas can be before or after a contiguous block.
  Declared NA cells do not end that block. Cross-session association requires
  an explicit same-dataset schema reference and one unambiguous prior schema.
  Namespace, role, personal-field and original segment/sequence boundaries
  are negative controls. No missing value is filled or observation merged.
- Identifier background preserves technical unit pronouns and an adjacent
  Otherwise branch, while actual personal details still stop the quote.

The frozen nine-case data diagnostic improves from 3/9 to 9/9 complete-return
contracts. The same 223-record table has all 143 eligible original records
when its schema is first, last, or explicitly shared across sessions; one
declared missing cell yields the independently expected 142. This does not
claim that all observations fit every 8k/16k prefix. Prefix coverage is
reported separately, and oversized histories remain bounded.

The unchanged 47-case evaluator with both cached real-v4 seeds retains every
previous required literal, source-fidelity, role and rank check: 42/47 strict,
291/306 literals at 8k and 306/306 at 16k. Existing extended-context and state
contracts remain 8/8 and 12/12. Five new synthetic qwen3-rerank requests were
prepared separately; offline complete-request replay improves the six frozen
long-source evidence cases from 3/6 to 5/6. The linked long-source background
case still misses necessary units, and earlier narrative/procedural failures
remain disclosed. These are evidence checks, not generated-answer accuracy.

An independent controlled-slot check retains an old method at rank 2 when all
eight provider slots are busy (previously rank 9); the available provider
places it at rank 1. This reduces evidence loss without claiming identical
orders or assuming an official context-prefix limit. A temporary 4.1-million
character user history plus 16 simultaneous Add requests completes in 13.99s
in the local controlled-vector environment, with no lock errors and correct
final state. Source replay increases Add cost with history length; this is not
a live provider latency guarantee. Legacy incorrect state is repaired when
that attribute next receives Add, rather than by an unsolicited bulk rewrite.

Known privacy limitation: explicit exclusion of names is not yet recognized by
the field-exclusion grammar; paired checks show the same limitation in v3.8.0.

## rc8: source qualifications and repeated-background pressure

The new fixed diagnostics use invented conversations only. No official memory,
questions, answer keys or raw evaluation traces were inspected. Embeddings,
reranker, endpoint routes, authentication and database schema remain unchanged.

Changes select and schedule original evidence rather than producing answers:

- The topic-synthesis task from the query plan now reaches context selection
  and continuation budgeting. A prior fixed project-history paraphrase improves
  from 7/16 to 16/16 source markers; explicit and unknown-topic controls retain
  their previous scope behavior.
- Global sampling stably schedules lower-frequency literal sentences before
  repeated background. Source RIDs are never merged. An original instance of
  each global response policy is protected, using the same scope decision as
  dedicated rule retrieval, so common instructions are not delayed by rarity.
- `source_dependencies.py` selects explicit source-local qualifications for
  topic views, raw previews and precise personal-property quotes. It preserves
  registry outcomes, revocations, replacements and partial waivers; it does
  not evaluate a condition, assign truth or create an updated fact. Links are
  bounded to 24 added sentences, 512 sentence positions and four following
  qualification sentences. Different entities, private fields and hypothetical
  updates have independent negative controls. Reference resolution retains the
  original sentence indexes and hidden scope boundaries before privacy
  filtering; a second preview cannot reconnect scopes across an excluded
  field. Ambiguous repeated-sentence mappings add no new dependencies.
- Long ranking copies use bounded query-focused literal windows instead of
  an indiscriminate prefix. Wire payloads remain within 3,500 UTF-8 bytes per
  document and the existing weighted request ceiling. Returned evidence and
  source attribution remain independent of these disposable ranking copies.

| Fixed invented diagnostic | rc7 | rc8 |
|---|---:|---:|
| Thirty trajectory facts embedded in long sources, complete Search | 5/30 | 30/30 |
| Complete domains with five trajectory stages | 0/6 | 6/6 |
| Explicit state/qualification evidence contracts, 8k/16k/full | 7/12 | 12/12 |
| Repeated numerical observations retained by distinct RID | 481/481 | 481/481 |
| Existing extended-context contracts, full response | 8/8 | 8/8 |

The embedded trajectory control still exposes only five claims at 8k/16k;
its gain is complete-response coverage, not a claim about every prefix limit.
All other eleven frozen progression views have no full-return loss. Several
realistic handover/annual-review/narrative queries still miss synthesis intent;
they remain disclosed failures rather than a reason to add scenario-specific
trigger phrases. The repeated-observation control preserves 480 equal readings
and one outlier. The common response-policy control remains visible at 8k,
16k and full response after protecting policy dependencies.

The unchanged 47-case evaluator with both cached real-v4 seeds retains every
previous required literal, role, source-fidelity and rank check. Results remain
42/47 strict scenarios, 291/306 required literals at 8k and 306/306 at 16k.
These counts describe evidence, not the platform's generated answers.

`evaluate_long_source_ranking.py` freezes six independent long-source cases
and records both Store requests and the actual truncated client wire payload.
Two tail-definition cases change from 0/4 and 0/5 visible wire inputs to 4/4
and 5/5. Ten synthetic requests prepared real qwen3-rerank orders separately;
offline replay shows one necessary source improving from rank 2 to 1. Do not
attribute a general ranking gain to this small sample: complete evidence is
3/6 with model ranks in both versions (4/6 under fallback dense ordering).
One unusable execution-source excerpt moves from rank 1 to 11, while its
necessary inputs are already absent in both versions. Long procedural
descriptions and table-tail conversion/eligibility clauses still have failures.

Fixture hashes:

```text
progression    f10ad3a2896f52ffa2273ffc12d9524b7f7a699bd16bd5c7dd4b33cd953d1bee
state          892c0cb082bf4a180673afb8f02803ab1aa6aa4bdb6649697a561ed6e605c69c
long ranking   78f998e338ccd5ac7226d36e71d97ffe0d7f75f40a7858a5d7abbcb8cee89aac
priority       b05a3448435d9f41c8a1d48f07f204aa23c207520bcff23a33455777cb858a63
```

## rc7: synthesis planning and semantic ranking

rc7 adds a deterministic operation/scope/exclusion plan and an optional native
DashScope `qwen3-rerank` client. The [competition FAQ](https://agentmemories.ai/competition/)
leaves rerankers unrestricted; embeddings remain `text-embedding-v4` and no
generative LLM is called. Ranking uses at most 64 eligible original sources,
interleaving dense and rare-term recall inside one user. Provider scores only
order this pool; they never generate facts, delete memories or become a
cross-query relevance threshold. Time ordering, original/current state,
explicit correction and precise personal-property safeguards remain ahead of
the model. Network errors and malformed/incomplete ranks preserve prior order.

Ranking copies are capped at 3,000 query / 3,500 document UTF-8 bytes, with
`query_bytes * document_count + sum(document_bytes) <= 100,000`. Only these
copies may be cut; returned quotations keep their original source. Eight
shared provider slots do not queue: saturation falls back immediately. The
six-second urllib timeout bounds network operations, not a strict DNS/whole
request deadline. urllib follows the host's existing proxy configuration.

The unchanged 47-case evaluator, paired real-v4 caches and two fixed seeds
(20261022 / 20261031) produce these evidence diagnostics:

| Metric | rc6 | rc7 with replayed model ranks |
|---|---:|---:|
| Strict scenarios, both prefix budgets | 41 / 47 | 42 / 47 |
| Necessary literals visible at 8,000 characters | 285 / 306 | 291 / 306 |
| Necessary literals visible at 16,000 characters | 300 or 301 / 306 | 306 / 306 |
| Source fidelity | 47 / 47 | 47 / 47 |

Per-case comparisons found no lost necessary literal, newly incorrect role or
source-fidelity loss. The online/batch diagnostic now exposes seven original
sources rather than six in both write modes; their equivalence still passes.
Overall coverage gains come from synthesis planning and prefix priority. An
ablation with the same rc7 code and no model has identical literal coverage;
model ordering reduces one workflow distractor flag from two to one at 8k,
while two flags remain at 16k. Do not attribute every improvement to reranking.

`evaluate_rerank_replay.py` wraps the frozen evaluator without editing its
fixtures or scoring. Model orders are cached against a SHA256 of the full
query and ordered candidate documents, then replayed with networking blocked.
All 21 requests in each final real-vector evaluation hit that cache. Model
preparation used invented texts only, separately from the offline evaluation.

Additional checks:

- Eight frozen extended-context contracts remain 8/8 for the full return.
- `evaluate_synthesis_intent.py` has 16 invented query contrasts; its ten
  personal-history overview variants now expose all 128 original sources.
  Before rc7, some variants exposed 67/128 or 0/128. The explicit work/travel
  exclusion case retains its 32 source markers without its private witnesses.
  Unknown named-topic controls remain non-global; that does not mean dense
  fallback always returns no unrelated records.
- `evaluate_method_dependencies.py` freezes nine conditional, transfer,
  role and revocation scenarios. Both rc6 and rc7 retrieve all necessary
  inputs with real v4 vectors (9/9). Under deliberately adverse controlled
  dense ordering, rc6 is 4/9 and rc7 plus cached ranks is 7/9. The latter is a
  robustness diagnostic, not evidence of a real-vector gain. Independent
  arithmetic/state reference results are not generated answer scores.

Explicit privacy exclusions match known field/value grammar. They do not
constitute universal privacy detection. Abstract named-topic paraphrases,
implicit cross-language links and final rule application can still fail.
No official evaluation conversations, questions, answers or database contents
were inspected to tune this release. These results do not predict Smoke/Full.

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

## rc6 extended evidence and explicit references

The compact front of retrieval remains bounded as before. Large selected
histories can now append source segments omitted from that view, with a total
160,000-character ceiling and 128,000 characters available for continuations.
Small selected scopes remain within 32,000 characters. These are content
character bounds, not a promise about tokens or the Answer model's visible
window. The platform applies its own token-counted prefix; its documented input
budget is 117,760 tokens ([AML guide](https://agentmemories.ai/docs)).

Continuations reuse the original user and topic scope. The selection plan
tracks original source RID/segment identities and partial character ranges;
reversible numeric templates remain literal. Complete source segments already
in the compact view are not re-added as new observations. A raw copy and a
source view may quote the same record; consumers must count its RID once.

The new `evidence_links.py` follows explicit dataset/project/protocol references
from the query and a retrieved source. It supplies bounded source excerpts of
older definitions, units, conditions and revocations. The source speaker is
retained. It does not infer a version override, follow arbitrary multi-hop
entities, or connect unlabelled people. Different explicitly headed same-kind
IDs can be excluded consistently from raw and aggregated views; negative
references, unknown sources and relationship bridges stay eligible.

All 333 local checks pass: the protected 275 plus 15 continuation, 38 literal
identifier-link, and five Search integration controls. The old 47-scenario
real-vector evaluation has identical per-scenario evidence, fidelity,
contamination and rank outcomes on both existing seeds, including its six
previous strict failures. Existing fixtures were not changed.

`evaluate_extended_context.py` adds eight independently invented long-history,
numeric, explicit-reference and streaming scenarios. The same fixed inputs and
shared real `text-embedding-v4` cache give the following full-return evidence:

| Diagnostic | rc5 | rc6 |
|---|---:|---:|
| Cross-topic decision/rationale markers | 92 / 384 | 384 / 384 |
| Narrow project-history markers | 96 / 288 | 288 / 288 |
| Independently recorded observations | 194 / 713 | 713 / 713 |
| Cross-session dataset observations | 140 / 317 | 317 / 317 |
| Online/batch dataset observations | 123 / 173 | 173 / 173 |

The numeric decoder reads visible declared template columns, deduplicates by
original RID and checks count, mean and population variance against the
invented observations. Repeated equal-valued records remain separate RIDs.
All eight full-return evidence contracts pass in rc6, versus two in rc5.
This is neither an eight-question answer score nor a projection of Smoke/Full.
One contract also checks a newly stated analysis reference, which is diagnostic
coverage rather than a necessary numerical input. Prefix metrics are reported
separately and do not imply that 8k or 16k characters is the platform's limit.

A single in-process cached-vector stress round of 16 simultaneous long searches
had zero search, isolation or sentinel errors, retained 317/317 observations
each, and completed in 11.45 seconds (maximum request 11.44 seconds). It excludes
HTTP, embedding-provider and public-network latency, and does not establish
sustained throughput or an end-to-end service guarantee.

Limits remain: implicit aliases and reference-card types are not linked;
abstract topic requests can still lack anchors; very large histories may remain
truncated. Full-response coverage does not establish final-answer reasoning,
complete privacy protection or suitability for every downstream context limit.
The embedding model, Add transaction, database schema, endpoint and credentials
are unchanged. No generative LLM or unapproved model is introduced.

Frozen rc6 algorithm source SHA256:

```text
store.py           bf3a76d429bc557dc4afd6360b4c901d2de9f012995b255dbbdb78cd4ee4a341
facts.py           3c748676ce6a4be48bba913b5fb177b48e05613fd320bce8171c812f96cfa42f
evidence_links.py  b2e6960d0ae54d276b538497b73b8b099783eab92eae07b05148b52af45c7df0
```

Extended fixture SHA256: `0c3cb5da7305d1cb03e24a3d012bbf6cbd10590f6a49bbf206fad25ac6d8dce1`.
Shared extended vector cache SHA256: `8e28e6d6f663a628ba5f454a55770ebbd33af248c52f5027649a877a7ecc828e`.
