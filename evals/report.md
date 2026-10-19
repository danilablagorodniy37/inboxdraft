# Evaluation report

Run: 19 Oct 2026 21:17 UTC. Model: anthropic (claude-sonnet-5-5 drafts, claude-haiku-5-5 classification and judge). Index: BAAI/bge-small-en-v1.5. Enquiries: 50, all synthetic.

| Metric | Value |
|---|---|
| Category classified correctly | 90% |
| Escalation decision correct | 88% (0 missed, 6 unnecessary) |
| Ready drafts (no escalation) | 35 of 50 |
| Ready drafts with all required facts (rules) | 97% |
| Judge: send as is / minor edit / rewrite | 69% / 20% / 11% |
| Ready drafts with invented facts (judge) | 11 |
| Unsupported figures caught by the guard | 4 |
| Unsupported figures in ready drafts | 0 |
| Time per enquiry, p50 / p95 | 5622 ms / 11395 ms |
| Cost per enquiry (classify + draft) | $0.02 |
| Total cost, drafts / judge | $0.79 / $0.01 |

Prices: Prices are configured values (USD per million tokens), last checked 18 Oct 2026. Verify at https://platform.claude.com/docs/en/about-claude/pricing and override with MODEL_PRICES in .env.

Emails sent during this run: 0. The pipeline has no code path to a mail server; see tests/test_no_send_without_human.py.

## Per enquiry

| id | expected | got | escalation | facts | judge | figures flagged | ms | cost |
|---|---|---|---|---|---|---|---|---|
| c01 | pricing | pricing | none | ok | minor_edit |  | 6038 | $0.02 |
| c02 | pricing | pricing | unnecessary | n/a |  | £280 | 8582 | $0.02 |
| c03 | pricing | pricing | unnecessary | n/a |  | £90 | 8809 | $0.02 |
| c04 | pricing | pricing | none | ok | send_as_is |  | 5184 | $0.01 |
| c05 | pricing | pricing | none | ok | send_as_is |  | 13530 | $0.02 |
| c06 | pricing | pricing | none | ok | send_as_is |  | 5369 | $0.01 |
| c07 | pricing | pricing | none | ok | send_as_is |  | 4839 | $0.01 |
| c08 | pricing | pricing | unnecessary | n/a |  |  | 11395 | $0.01 |
| c09 | pricing | pricing | none | ok | minor_edit |  | 8265 | $0.02 |
| c10 | pricing | pricing | none | ok | send_as_is |  | 4545 | $0.01 |
| c11 | pricing | pricing | none | ok | send_as_is |  | 4962 | $0.01 |
| c12 | pricing | pricing | none | ok | send_as_is |  | 5284 | $0.01 |
| c13 | appointment | appointment | none | ok | minor_edit |  | 5271 | $0.01 |
| c14 | appointment | appointment | none | ok | rewrite |  | 9702 | $0.02 |
| c15 | appointment | appointment | none | ok | send_as_is |  | 7026 | $0.01 |
| c16 | appointment | appointment | none | ok | send_as_is |  | 5029 | $0.01 |
| c17 | appointment | other (x) | none | ok | send_as_is |  | 4762 | $0.01 |
| c18 | appointment | appointment | none | ok | send_as_is |  | 5699 | $0.01 |
| c19 | appointment | appointment | unnecessary | n/a |  |  | 6465 | $0.01 |
| c20 | appointment | appointment | none | ok | send_as_is |  | 4877 | $0.01 |
| c21 | documents | documents | none | ok | send_as_is |  | 5593 | $0.02 |
| c22 | documents | services (x) | none | ok | minor_edit |  | 5831 | $0.02 |
| c23 | documents | documents | unnecessary | n/a |  | 30 | 8990 | $0.02 |
| c24 | documents | documents | none | ok | send_as_is |  | 5496 | $0.01 |
| c25 | documents | documents | none | ok | send_as_is |  | 5012 | $0.01 |
| c26 | documents | documents | none | ok | send_as_is |  | 4274 | $0.01 |
| c27 | documents | documents | none | ok | send_as_is |  | 5526 | $0.01 |
| c28 | documents | documents | unnecessary | n/a |  |  | 12099 | $0.02 |
| c29 | services | services | none | ok | send_as_is |  | 5203 | $0.02 |
| c30 | services | services | none | ok | rewrite |  | 6426 | $0.01 |
| c31 | services | services | none | ok | send_as_is |  | 5760 | $0.02 |
| c32 | services | services | none | ok | send_as_is |  | 5607 | $0.01 |
| c33 | services | services | none | ok | minor_edit |  | 5797 | $0.01 |
| c34 | services | pricing (x) | none | ok | send_as_is |  | 5246 | $0.01 |
| c35 | services | services | none | ok | send_as_is |  | 4850 | $0.01 |
| c36 | deadlines | deadlines | none | ok | send_as_is |  | 4836 | $0.01 |
| c37 | deadlines | deadlines | none | ok | rewrite |  | 7878 | $0.02 |
| c38 | deadlines | deadlines | none | ok | rewrite |  | 9246 | $0.02 |
| c39 | deadlines | deadlines | none | ok | send_as_is |  | 9443 | $0.02 |
| c40 | deadlines | deadlines | none | ok | minor_edit |  | 4986 | $0.01 |
| c41 | complaint | complaint | expected, done | n/a |  |  | 8744 | $0.02 |
| c42 | complaint | complaint | expected, done | n/a |  | £360 | 8888 | $0.02 |
| c43 | complaint | complaint | expected, done | n/a |  |  | 9001 | $0.02 |
| c44 | complaint | complaint | expected, done | n/a |  |  | 5214 | $0.01 |
| c45 | services | services | none | missing 1040 | minor_edit |  | 5622 | $0.01 |
| c46 | other | other | expected, done | n/a |  |  | 4979 | $0.01 |
| c47 | services | services | expected, done | n/a |  |  | 7846 | $0.02 |
| c48 | other | deadlines (x) | expected, done | n/a |  |  | 6006 | $0.02 |
| c49 | other | other | expected, done | n/a |  |  | 9119 | $0.02 |
| c50 | other | deadlines (x) | expected, done | n/a |  |  | 5110 | $0.01 |
