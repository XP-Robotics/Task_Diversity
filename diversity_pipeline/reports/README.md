# Diversity reports

Generated outputs, checked in deliberately. The rest of the repository ignores
`qa_out*/` because reports are artifacts rather than source; these two
deliveries are kept because they are the reference results people cite.

## `xphi_mcap_v2/` — `s3://xphi/datasets/` (MCAP delivery)

9,185 episodes · 1,025.94 hours · run with `--s3-layout mcap --profile xphi-mcap-v1`.

| File | What it is |
|---|---|
| `diversity_report.json` / `.html` | the pipeline's own output |
| `diversity_report_enriched.json` / `.html` | the same run with checks 7 and 8 resolved |
| `diversity_report_pro.html` | presentation report, built from the enriched result |

**Read the enriched pair, not the raw pair, for checks 7 and 8.** `worker_type`
and `business_size` do not exist inside the MCAP recordings, so the pipeline
reports both checks `NOT_COMPUTABLE`. They were resolved afterwards by joining
`s3://xphi/enrichments/episode_metadata_v1.json`, which matched all 9,185
episodes. That join is a post-processing step, not something the pipeline
reproduces on its own.

Result: **7 PASS · 1 WARN · 1 NOT_COMPUTABLE**. The WARN is the difficulty mix
(no difficulty labels anywhere in the delivery, so the difficulty-keyed caps in
checks 3-5 never bind). The remaining NOT_COMPUTABLE is human-human interaction,
which has no source in either the recordings or the enrichment.

The enrichment is keyed by the *original* filename UUID, which the recordings no
longer carry — the objects were renamed to `xphirobotics-NNNNN.mcap`. The
old-to-new mapping is the only way to reproduce that join.

## `xphi2/` — the Vultr `xphi2` delivery

Includes per-episode, per-task, per-environment and per-provenance duration
breakdowns alongside the report.

---

Both were produced by `diversity_pipeline/run.py`; thresholds live in
`qa/config.py`, so a report is only comparable to another run under the same
config.
