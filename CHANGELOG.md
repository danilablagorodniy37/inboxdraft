# Changelog

## 0.2.0 (19 Oct 2026)

### Added
- `inboxdraft evals --offline`: privacy and retrieval checks on the 50 evaluation enquiries with the production code and no model or key. Writes `evals/offline-report.md`. Results: 0 of 154 personal details left in the text a model would see; every required fact in the top 5 chunks for 39 of 39 answerable enquiries.
- README results measured, not placeholders; `docs/demo.md` is now a written walkthrough with the two screens.

### Fixed
- The scrubber missed the sender's name when it was written in lower case ("how much? ben"). Known names now match in any case, except first names that are everyday words ("will", "mark"), which still need a capital letter so "I will pay the bill" survives. Found by the new offline check; two tests.

## 0.1.0 (18 Oct 2026)

First release: ingest, PII scrubbing, classification, retrieval, drafting with citations, the figures guard, the review UI, and the evaluation harness.
