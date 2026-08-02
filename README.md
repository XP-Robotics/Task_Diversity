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

## What the pipeline needs as input

### The unit: one episode = one video + one metadata sidecar

Everything the pipeline reports is an aggregate over **episodes**. An episode is a video
file paired with a JSON sidecar describing it. Pairing is by *stem* (the filename without
its extension) **within the same directory** — `clip_004.mp4` pairs with
`clip_004.meta.json`, and never with a `clip_004.meta.json` sitting in another folder.

Videos are `.mp4` or `.mov` (case-insensitive). Nothing else is treated as a video.

An unpaired file is **never dropped**. A video with no sidecar and a sidecar with no video
both become episodes carrying a warning, so an ingestion gap shows up in the report instead
of quietly shrinking the denominator.

### Accepted layouts

**Local directory** — recursion is allowed, flat is normal. Sidecars must end in
`.meta.json`:

```
delivery/
├── clip_001.mp4
├── clip_001.meta.json
├── clip_002.mov
└── clip_002.meta.json
```

**S3 `folder` layout** (the default, `--s3-layout folder`) — one folder per episode, named
however you like; the sidecar is `metadata.json` or `<stem>.meta.json`:

```
s3://bucket/prefix/
├── episode-0001/
│   ├── video.mp4
│   └── metadata.json
└── episode-0002/
    ├── video.mp4
    └── metadata.json
```

**S3 `paired` layout** (`--s3-layout paired`) — two sibling folders matched by stem:

```
s3://bucket/prefix/
├── videos/    clip_001.mp4, clip_002.mp4, …
└── metadata/  clip_001.json, clip_002.json, …
```

If your delivery matches none of these — sidecars a level above the media, many clips per
folder, a metadata array instead of an object — that is what a **source profile** is for
(see below). Profiles reshape structure; they never invent values.

### The metadata sidecar

A JSON **object** (or something a profile unwraps into one). Nested objects are flattened
to dotted paths, so `data.metadata.task_id` resolves as `task_id`. Key spelling is
normalised — `task-id`, `task_id`, `Task_ID` and `taskId` are the same key — and each
canonical field also accepts these alternates, **first match wins**:

| Canonical field | Also accepted as | Feeds |
|---|---|---|
| `task_id` | `task-id` | checks 3, 4, 5 |
| `operator_id` | `operator-id` | checks 5, 6 |
| `environment_id` | `environment-id` | checks 4, 5, 6, 7 |
| `environment_l1` | `environment-l1`, `environment_category`, `l1` | check 1, and the factory-pair rule in check 4 |
| `environment_l2` | `environment-l2`, `facility_type`, `venue`, `l2` | reported only, never graded |
| `environment_l3` | `environment-l3`, `scene`, `l3` | reported only, never graded |
| `task_difficulty` | `task-difficulty`, `difficulty` | checks 2, 3, 4, 5 |
| `business_size` | `business_type` | check 7 |
| `worker_type` | `operator_type`, `workforce_composition` | check 8 |
| `human_interaction` | `interaction` | check 9 |
| `start_time_unix` / `end_time_unix` | `start-time-unix` / `end-time-unix` | duration fallback |

Values are normalised too, so a delivery's own vocabulary usually just works:

| Field | Accepted values (case- and separator-insensitive) |
|---|---|
| `task_difficulty` | `easy` `e` `1` `simple` · `medium` `med` `2` · `hard` `h` `3` `difficult` `skilled` |
| `business_size` | `small` `sm` `s` · `medium` `med` `m` `mediumsizedbusiness` `mediumbusiness` · `large` `lg` `l` `big` |
| `worker_type` | `real` `employee` `internal` `realworker` `realemployee` · `contractor` `contract` `external` `contractworker` `contractors` |
| `human_interaction` | true: `true` `yes` `y` `t` `1` · false: `false` `no` `n` `f` `0` |

An unrecognised value is **not** guessed at — it is left unnormalised and reported. To
teach the pipeline a new spelling, add one line to the relevant dict in
`diversity_pipeline/qa/config.py`.

**No field is mandatory.** A missing field costs you the checks that need it, and those
report `NOT_COMPUTABLE` rather than passing. A delivery with only `task_id`,
`environment_id` and durations still produces a real report — it just says so about the
rest.

### Where hours come from

Every limit is measured in hours, so duration is the one input that always matters. Per
episode, in order:

1. **Measured from the video** with `ffprobe` — for S3 runs this is a range request against
   a presigned URL, so no download.
2. **`end_time_unix − start_time_unix`** from the sidecar, when ffprobe is unavailable or
   the video is unreadable.
3. **0 hours plus a warning**, when there is neither.

When *both* are available the measurement wins and the two are reconciled: a disagreement
beyond 1 second **and** 2 % is recorded on that episode, because a sidecar that disagrees
with its own video is worth knowing about even when the hours are fine.

Each episode records which source it used (`video` · `metadata` · `both` · `none`), and the
report shows the split — so "1,000 hours" is never an unqualified claim.

---

## Running the pipeline

### A local delivery

```bash
cd diversity_pipeline
python3 run.py /path/to/delivery --out qa_out
```

That is the whole setup — no virtualenv, no install step.

### An S3 / Spaces delivery (no download)

Credentials come from the environment and are never read from a file in the repo:

```bash
export AWS_ACCESS_KEY_ID=…
export AWS_SECRET_ACCESS_KEY=…
export S3_ENDPOINT=sfo3.digitaloceanspaces.com   # host only, no https://
export AWS_REGION=sfo3                            # default us-east-1
export AWS_S3_BUCKET_NAME=my-bucket               # optional: default bucket

cd diversity_pipeline
python3 run.py s3://my-bucket/prefix --out qa_out

# try it on 50 episodes first — same code path, seconds instead of minutes
python3 run.py s3://my-bucket/prefix --limit 50 --out qa_out_sample
```

Metadata is fetched concurrently and durations are probed remotely, so a delivery of tens
of thousands of episodes is a minutes-scale run, not a download.

### Flags

| Flag | Meaning |
|---|---|
| `--out DIR` | output directory (default `qa_out`) |
| `--profile NAME` | source profile adapting one delivery's metadata shape (`generic`, `xphi-capture-v1`, `licensed-egocentric-v1`, `xphi-episode-v1`) |
| `--s3-layout folder\|paired` | per-episode `<uid>/` folders, or `videos/` + `metadata/` matched by stem |
| `--factory-dynamic` | 2 h dynamic factory cap instead of the 1 h repetitive default |
| `--limit N` | cap episodes ingested (S3 only, natural order) |
| `--quiet` | terser console output |

### Choosing a profile

Start with the default (`generic`). If the report comes back with widespread
`NOT_COMPUTABLE` or empty groupings, the metadata is shaped differently from what the
generic reader expects, and a profile is the fix:

| Profile | Use when |
|---|---|
| `generic` | the sidecar is already a flat JSON object (default) |
| `xphi-capture-v1` | `metadata.json` is an array of chunks and the payload is under `data.metadata` |
| `xphi-episode-v1` | each episode folder carries a rich `episode_metadata.json` alongside a sparser `metadata.json` |
| `licensed-egocentric-v1` | split `videos/` + `metadata/` layout using `factory_id` / `worker_id` |

### What you get back

```
qa_out/
├── diversity_report.html   ← self-contained, open in any browser
└── diversity_report.json   ← same content, machine-readable
```

plus a console summary — one line per check, with the offending groups listed under any
that failed.

Exit codes: `0` all checks clear · `1` any FAIL · `2` usage / ingestion error. So
`python3 run.py … || echo "delivery rejected"` works in a scheduled job, and CI can gate on
it directly.

### When something looks wrong

| Symptom | Cause |
|---|---|
| `error: no episodes found under …` | the layout does not match — check `--s3-layout`, or that sidecars end in `.meta.json` for a local run |
| every check `NOT_COMPUTABLE` | metadata parsed but fields unresolved — likely a profile is needed |
| all hours `0.0` | no ffprobe **and** no `start_time_unix`/`end_time_unix` in the sidecars |
| hours look nominal, all identical | durations came from declared timestamps, not measurement — check the provenance section of the report |
| many orphan warnings | videos and sidecars are not in the same folder, or stems disagree |

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
