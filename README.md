# Task Diversity QA

Delivery-level diversity QA for human egocentric robotics video — nine aggregate limits
evaluated over a whole delivery, as a **dependency-free Python pipeline**.

A *delivery* is a batch of egocentric video episodes captured by human operators. Ten
thousand structurally-perfect episodes of one operator doing one task in one warehouse are
worthless as training data: diversity is a property of the *set*, not of any member of it,
and it can only be measured in aggregate. This pipeline measures it.

| Component | Answers | Unit of judgement | Output |
|---|---|---|---|
| [`diversity_pipeline/`](diversity_pipeline/) | Is the delivery varied enough? | the whole delivery | **PASS / FAIL** per limit |

> 📖 The full technical documentation lives in
> [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md), also available as a print-ready
> booklet: [`docs/Task-Diversity-QA-Documentation.pdf`](docs/Task-Diversity-QA-Documentation.pdf).

---

## Repository layout

```
.
├── README.md                     ← you are here
├── docs/
│   ├── DOCUMENTATION.md          ← full technical documentation (booklet source)
│   ├── Task-Diversity-QA-Documentation.pdf
│   ├── build_pdf.sh              ← regenerates the PDF from the markdown
│   └── pdf.css
└── diversity_pipeline/
    ├── run.py                    ← batch CLI (local or cloud-direct S3)
    └── qa/                       ← ingest → normalize → metrics → report modules
        ├── ingest.py             ← the only local I/O module
        ├── s3client.py           ← pure-stdlib SigV4 S3 client
        ├── s3ingest.py           ← cloud-direct ingestion
        ├── metrics.py            ← all nine diversity checks (pure, I/O-free)
        ├── config.py             ← every threshold, alias and vocabulary
        ├── profiles.py           ← per-delivery source profiles
        └── report.py, htmlkit.py ← JSON / HTML rendering with inline-SVG charts
```

---

## Requirements

The CLI is **pure Python 3 standard library** — no `requirements.txt`, no `pip install`
step. This is deliberate: the pipeline runs in constrained environments (scheduled tasks,
minimal containers, analyst laptops) where dependency installation is friction.

| Requirement | Optional? |
|---|---|
| Python 3.9+ (developed on 3.12) | no |
| `ffprobe` on `PATH` | yes — durations fall back to metadata timestamps, and each affected episode records that it did |

---

## Quick start

```bash
cd diversity_pipeline

# local delivery
python3 run.py /path/to/delivery --out qa_out

# cloud-direct: no download — metadata fetched in parallel,
# video durations measured remotely via ffprobe range requests
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
export S3_ENDPOINT=sfo3.digitaloceanspaces.com AWS_REGION=sfo3
python3 run.py s3://bucket/prefix --profile generic --out qa_out
```

| Flag | Meaning |
|---|---|
| `--out DIR` | output directory (default `qa_out`) |
| `--profile NAME` | source profile adapting one delivery's metadata shape (`generic`, `xphi-capture-v1`, `licensed-egocentric-v1`, `xphi-episode-v1`) |
| `--s3-layout folder\|paired` | per-episode `<uid>/` folders, or `videos/` + `metadata/` matched by stem |
| `--factory-dynamic` | 2 h dynamic factory cap instead of the 1 h repetitive default |
| `--limit N` | cap episodes ingested (S3 only, natural order) |
| `--quiet` | terser console output |

Exit codes: `0` all checks clear · `1` any FAIL · `2` usage / ingestion error.

---

## The nine checks

| # | Check | Limit (summary) |
|---|---|---|
| 1 | Environment category (L1) | any category ≤ 20 % of hours · top-5 ≤ 60 % · top-10 ≤ 85 % · ≥ 15 distinct |
| 2 | Difficulty mix | easy ≤ 30 % · hard ≥ 5 % · medium 40 % target (INFO) |
| 3 | Per-task share | hard ≤ 6 % · medium ≤ 4 % · easy ≤ 1.5 % (each capped at 200 h) |
| 4 | Task × Environment | hard 40 h · medium 20 h · easy 10 h · **factory pair 1 h** |
| 5 | Operator × Task × Environment | hard 10 h · medium 2 h · easy 1 h |
| 6 | Environment × Operator | 40 h flat |
| 7 | Business-size caps | small 500 h · medium 2 000 h · large 5 000 h |
| 8 | Workforce mix | real employees ≥ 70 % of hours |
| 9 | Human–human interaction | ≥ 5 % of episodes (target — WARN, never FAIL) |

A check whose input field is absent from the delivery reports **NOT_COMPUTABLE**, never a
silent pass — the distinction between *we checked and it was fine* and *we could not check*
is the entire value of the report.

---

## Reports

Each run writes into the output directory:

- **`diversity_report.html`** — a single self-contained file (inline CSS, inline SVG
  charts, no external assets): the nine check cards plus a *delivery composition* section —
  hours by environment level (one chart per `environment_l1` / `l2` / `l3` where the
  delivery declares a hierarchy), hours by task, difficulty and workforce ratio bars,
  operator concentration, episode-length histogram, the environment reference table, and a
  per-episode duration table with provenance.
- **`diversity_report.json`** — the same content machine-readable; its `rows` array is the
  complete per-episode basis for every aggregate — including all three environment levels —
  so any figure can be recomputed or audited independently.

Only `environment_l1` is graded (check 1). `environment_l2` and `environment_l3` are read
and charted but never scored: an L1 category can clear every concentration rule and still
be one venue filmed over and over, and the finer levels are what let a reader see that.

Report artifacts are intentionally **git-ignored** — this repository contains the pipeline,
not its outputs.

---

## Configuration

Credentials are read from the environment and are never hardcoded or committed.

| Variable | Purpose |
|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 credentials |
| `S3_ENDPOINT` | host only, e.g. `sfo3.digitaloceanspaces.com` |
| `AWS_REGION` | e.g. `sfo3` (default `us-east-1`) |
| `AWS_S3_BUCKET_NAME` | default bucket when a path omits it |

Every threshold is **data, not logic** — limits, the environment vocabulary, spelling
synonyms and field aliases all live in `diversity_pipeline/qa/config.py`. Tuning a limit is
a one-line edit that never touches check logic (booklet §8). Teaching the pipeline a
spelling a delivery actually uses — `workforce_composition` for `worker_type`,
`mediumsizedbusiness` for a business size — is the same one-line edit, and it is how a
field that would otherwise report `NOT_COMPUTABLE` starts being read.

---

## Design principles (the short version)

1. **Never infer, only read** — declared fields are normalised for spelling, never guessed
   from context.
2. **Absent is not passing** — a check without its input reports `NOT_COMPUTABLE`, never
   `PASS`.
3. **Fail open on ingestion** — one corrupt sidecar costs a finding, not the run; orphans
   are surfaced, never dropped.
4. **Measure; never trust a declaration** — hours come from the video artefact where
   possible, with per-episode provenance in the report.
5. **Thresholds are data** — versioned separately from check logic.
6. **Report checks that pass** — a check that did not run is distinguishable from one that
   passed.

The full rationale, check-by-check reference, source-profile guide, output schemas and
extension guide are in the [documentation booklet](docs/DOCUMENTATION.md).
