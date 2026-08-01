# Egocentric Capture QA

Quality assurance for deliveries of human egocentric robotics video — **episode validation**
and **delivery diversity limits**, as two independent, dependency-free Python pipelines.

A *delivery* is a batch of egocentric video episodes captured by human operators wearing or
holding an ARKit/iOS device. Before such a delivery can be paid for or trained on, two
independent questions must be answered:

| Question | Component | Unit of judgement | Output |
|---|---|---|---|
| Is each episode structurally sound? | [`schema_pipeline/`](schema_pipeline/) | one capture session | **ACCEPT / REVIEW / REJECT** |
| Is the delivery as a whole varied enough? | [`diversity_pipeline/`](diversity_pipeline/) | the whole delivery | **PASS / FAIL** per limit |

The pipelines share a design lineage but are genuinely independent: neither imports the
other, and either can be run without the other.

> 📖 The full technical documentation lives in
> [`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md) (also available as a print-ready booklet,
> [`docs/Egocentric-Capture-QA-Documentation.pdf`](docs/Egocentric-Capture-QA-Documentation.pdf)).
> The input contract that vendor packages must satisfy is specified in
> [`schema_pipeline/INPUT_REFERENCE.md`](schema_pipeline/INPUT_REFERENCE.md).

---

## Repository layout

```
.
├── README.md                     ← you are here
├── docs/
│   ├── DOCUMENTATION.md          ← full technical documentation (booklet source)
│   ├── Egocentric-Capture-QA-Documentation.pdf
│   ├── pipeline-flowcharts.html  ← architecture flowcharts, self-contained HTML
│   ├── build_pdf.sh              ← regenerates the PDF from the markdown
│   └── pdf.css
├── schema_pipeline/              ← episode validation (Layer B + C + session checks)
│   ├── run.py                    ← batch CLI
│   ├── store_missing.py          ← store per-session missing parameters in MongoDB
│   ├── INPUT_REFERENCE.md        ← the package input contract
│   ├── qa/                       ← ingest → normalize → check → report modules
│   └── tests/
└── diversity_pipeline/           ← delivery diversity limits (9 checks)
    ├── run.py                    ← batch CLI (local or cloud-direct S3)
    └── qa/                       ← ingest → normalize → metrics → report modules
```

Two companion components are documented in the booklet but maintained outside this
repository: `qa_watch` (a continuous streaming validator writing one MongoDB document per
package) and `reference_package` (a synthetic 40-file package that passes every check).

---

## Requirements

Both batch CLIs are **pure Python 3 standard library** — no `requirements.txt`, no
`pip install` step. This is deliberate: the pipelines run in constrained environments
(scheduled tasks, minimal containers, analyst laptops) where dependency installation is
friction.

| Requirement | Needed by | Optional? |
|---|---|---|
| Python 3.9+ (developed on 3.12) | everything | no |
| `ffprobe` on `PATH` | `diversity_pipeline` | yes — durations fall back to metadata timestamps, and each affected episode records that it did |
| `pymongo>=4.6` | `schema_pipeline` MongoDB export only | yes — omit `--mongo-uri` to skip the DB write |

---

## Quick start

### 1 · Validate a delivery (schema pipeline)

```bash
cd schema_pipeline
python3 run.py /path/to/delivery --out qa_out
```

`root` may be a single package directory, a chunk `.zip`, or a delivery directory whose
immediate entries are either. Every episode receives a verdict:

- **ACCEPT** — no data errors (warnings and format gaps never block)
- **REVIEW** — a check could not be evaluated (missing prerequisite; not vendor fault)
- **REJECT** — at least one spec-required condition violated by the delivered data

Options:

| Flag | Meaning |
|---|---|
| `--out DIR` | output directory (default `qa_out`) |
| `--limit N` | cap the number of packages validated |
| `--quiet` | terser console output |
| `--mongo-uri URI` | additionally store each session's missing parameters in MongoDB (`$MONGO_URI` respected; omit both to skip) |
| `--mongo-db / --mongo-collection` | database / collection (defaults `qa` / `missing_parameters`) |
| `--slim` | store only `{_id, missing_parameters}` instead of the full check matrix |

Exit codes: `0` nothing rejected · `1` at least one REJECT · `2` bad path / no packages.

### 2 · Measure delivery diversity (diversity pipeline)

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

Nine aggregate checks are evaluated (environment category distribution, difficulty mix,
per-task share, task × environment, operator × task × environment, environment × operator,
business-size caps, workforce mix, human–human interaction). A check whose input field is
absent from the delivery reports **NOT_COMPUTABLE**, never a silent pass.

Options:

| Flag | Meaning |
|---|---|
| `--profile NAME` | source profile adapting one delivery's metadata shape (`generic`, `xphi-capture-v1`, `licensed-egocentric-v1`, `xphi-episode-v1`) |
| `--s3-layout folder\|paired` | per-episode `<uid>/` folders, or `videos/` + `metadata/` matched by stem |
| `--factory-dynamic` | 2 h dynamic factory cap instead of the 1 h repetitive default |
| `--limit N` / `--quiet` | as above |

Exit codes: `0` all checks clear · `1` any FAIL · `2` usage / ingestion error.

### 3 · Read the reports

Each run writes a machine-readable JSON and a **self-contained HTML report** (inline CSS,
inline SVG charts, no external assets) into the output directory:

- `schema_report.json` / `schema_report.html` — per-package verdicts with per-check detail
- `diversity_report.json` / `diversity_report.html` — the nine check cards plus a delivery
  composition section (hours by environment level, task share, difficulty mix, operator
  concentration, episode lengths)

Report artifacts are intentionally **git-ignored** — this repository contains the
pipelines, not their outputs.

---

## Configuration

Credentials are read from the environment and are never hardcoded or committed.

| Variable | Used by | Purpose |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 ingestion | credentials |
| `S3_ENDPOINT` | S3 ingestion | host only, e.g. `sfo3.digitaloceanspaces.com` |
| `AWS_REGION` | S3 ingestion | e.g. `sfo3` (default `us-east-1`) |
| `AWS_S3_BUCKET_NAME` | S3 ingestion | default bucket when a path omits it |
| `MONGO_URI` / `MONGO_DB` / `MONGO_COLLECTION` | schema pipeline DB export | MongoDB target (defaults `qa` / `missing_parameters`) |

Every threshold is **data, not logic**: diversity limits live in
`diversity_pipeline/qa/config.py`, schema thresholds and field aliases in
`schema_pipeline/qa/ruleset.py`, the placeholder deny-list in
`schema_pipeline/qa/sentinel.py`, accepted side-car filenames in
`schema_pipeline/qa/pkg_ingest.py`. Tuning a limit is a one-line edit that never touches
check logic (see booklet §9).

---

## Testing

```bash
cd schema_pipeline
python3 tests/test_reference_fixture.py      # per-check verdicts on the reference fixture
python3 tests/test_capture_app_formats.py    # formats the iOS capture app actually emits
python3 tests/test_missing_matrix.py         # missing-parameter matrix construction
```

All fixtures are synthetic and built at runtime — no capture data is committed and no live
services are required.

---

## Design principles (the short version)

1. **Never infer, only read** — declared fields are normalised for spelling, never guessed
   from context.
2. **Absent is not passing** — a check without its input reports `BLOCKED` /
   `NOT_COMPUTABLE`, never `PASS`.
3. **Vendor fault ≠ format gap** — a field the capture format has nowhere to put is a
   `FORMAT_GAP` (a capture-app change request), never a vendor data error.
4. **Fail open on ingestion** — one corrupt side-car costs a parse-error finding, not the
   package's verdict.
5. **Measure; never trust a declaration** — rates and spans come from actual timestamps.
6. **Thresholds are data** — versioned separately from check logic.
7. **Report checks that pass** — a check that did not run is distinguishable from one that
   passed.

The full rationale, check-by-check reference, output schemas, extension guide and scope
boundaries are in the [documentation booklet](docs/DOCUMENTATION.md).
