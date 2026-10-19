# Changelog

## 0.2.1 (19 Oct 2026)

### Fixed
- `claude-sonnet-5-5` rejects a forced `tool_choice` (HTTP 400: type tool and any are not supported for this model), so every draft failed. The Anthropic provider now falls back to `tool_choice: auto` with an explicit instruction to call the tool, remembers the model, and counts the tokens of every attempt in the cost line. Test with a stub that answers 400 the way the API does.

### Added
- First model-graded evaluation run, 19 Oct 2026, in `evals/report.md` and the README: 90% category accuracy, 0 missed escalations, 69% of ready drafts sendable as they are, 0 unsupported figures in ready drafts, 11 of 35 ready drafts with an invented non-numeric claim flagged by the judge, about $0.02 per enquiry.

## 0.2.0 (19 Oct 2026)

### Added
- `inboxdraft evals --offline`: privacy and retrieval checks on the 50 evaluation enquiries with the production code and no model or key. Writes `evals/offline-report.md`. Results: 0 of 154 personal details left in the text a model would see; every required fact in the top 5 chunks for 39 of 39 answerable enquiries.
- README results measured, not placeholders; `docs/demo.md` is now a written walkthrough with the two screens.

### Fixed
- The scrubber missed the sender's name when it was written in lower case ("how much? ben"). Known names now match in any case, except first names that are everyday words ("will", "mark"), which still need a capital letter so "I will pay the bill" survives. Found by the new offline check; two tests.

## 0.1.0 (18 Oct 2026)

First release: ingest, PII scrubbing, classification, retrieval, drafting with citations, the figures guard, the review UI, and the evaluation harness.
