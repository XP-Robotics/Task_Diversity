# Input reference — what the QA pipeline expects

The contract for anything feeding either the batch CLI (`run.py`) or the streaming
watcher (`qa_watch/`). Every rule below is enforced by code in `qa/`; the check id in
each row is where.

---

## 1. Source layout

One **package** is one episode. Each immediate sub-folder of the source prefix is a
package:

```
s3://<bucket>/<prefix>/                 ← SRC
├── a01524-H-0147/                      ← package_id  (becomes the Mongo _id)
│   ├── metadata.json                   ← required
│   ├── manifest.json
│   ├── video.mov
│   ├── video_timestamps.json
│   ├── camera_intrinsic.json
│   ├── motion_data.json
│   ├── depth_camera_intrinsics.json
│   ├── depth_00000.bin …
│   ├── hands.json
│   ├── hands_metadata.json
│   └── ar_session.json
├── a01524-H-0148/
└── …
```

A folder is recognised as a package if it contains **`metadata.json` or `manifest.json`**.
The batch CLI also accepts a single package directory as its root.

**Packages may also ship as a zip.** The capture app bundles an episode into
`chunk_0000_raw.zip`, and both layouts are read without unpacking:

```
s3://<bucket>/<prefix>/
├── EPISODE-F2094A73/
│   └── chunk_0000_raw.zip          ← package named after the EPISODE folder
└── another-episode.zip             ← package named after the zip stem
```

Members must sit at the archive root; nested directories are ignored, matching the
flat-package rule for directories.

**`session_id`** is derived from `package_id` by the `SESSION_RE` regex, default
`^([^-]+)-` → `a01524-H-0147` yields `a01524`. Grouping only; nothing validates it.

---

## 2. The chunk-envelope convention

Every JSON file **except `manifest.json`** is expected as a single-record array:

```json
[ { "chunk": "chunk_0000_raw", "source": ".../chunk_0000_raw.zip", "data": { … } } ]
```

The payload is `[0].data`. For `metadata.json` the episode object is one level deeper —
`[0].data.metadata` — though a payload that is already the object is accepted.

A bare object (no envelope) also works. More than one record in the array fails **D2**
(`MULTI_CHUNK_PACKAGE`, ERROR): one episode must be one chunk.

`manifest.json` is read as a plain object, never an envelope.

---

## 3. How field names are matched

You do **not** have to match spelling exactly. Before any check runs:

1. **Flatten** — nested objects become dotted paths: `data.metadata.task_id`. Lists and
   scalars stay as leaf values.
2. **Canonicalise** — keys are lowercased with `-`, `_`, `.` and whitespace stripped. So
   `task-id`, `task_id`, `Task_ID` and `taskId` all collapse to `taskid`.
3. **Resolve**, in this precedence order: full dotted path → last segment → any interior
   segment. Only non-empty values count.
4. **Alias priority.** Where a record carries two accepted spellings of the same field, the
   one listed first in the registry wins — never whichever key happens to appear first in
   the JSON. So a package declaring both `episode_uuid` and `session_id` keeps
   `episode_uuid`, and a bad value there is reported rather than patched over.

An empty string, `null`, or an empty list/object counts as **absent**.

---

## 4. `metadata.json` — field reference

### 4.1 Required — missing or invalid is an `ERROR` (rejects the package)

| Field | Also accepted | Type | Must be | Check |
|---|---|---|---|---|
| `episode_uuid` | `session_uuid`, `uuid`, `session_id` ¹ | string | parseable by `uuid.UUID()`, **and unique across all packages** | B1.1, B1.1u |
| `operator_id` | — | string | non-empty, not a sentinel | B1.2 |
| `episode_start_time` | `start_time_unix`, `start_time`, `start_timestamp` | number \| ISO-8601 | within `1704067200` (2024-01-01) … now + 24 h | B1.3 |
| `device_type` | — | string | non-empty, not generic (bare `"iPhone"` fails) | B1.4 |
| `device_model` | — | string | non-empty, not generic | B1.4 |
| `software_version` | `app_version` | string | non-empty. Semver-ish → PASS; otherwise WARN, not ERROR | B1.5 |
| `camera_configuration` | `camera_config` | list \| object \| string | must name exactly the streams the package contains | B1.6 |
| `task_category` | — | string | non-empty, not a sentinel | B1.8 |
| `environment_label` | `environment_l1` | string | non-empty **and not a placeholder** — `"Unknown"` is rejected | B1.9 |
| `environment_id` | — | string | non-empty, not a placeholder | B1.9, B2.2 |
| `task_id` | — | string | non-empty | B2.1 |

**`camera_configuration` matching (B1.6).** The pipeline infers which streams are present —
`camera_intrinsic.json` present → `rgb`, `depth_camera_intrinsics.json` present → `depth` —
and requires the declaration to match in both directions. Accepted forms:

```json
"camera_configuration": ["ego_camera", "depth"]
"camera_configuration": { "streams": ["rgb", "depth"] }
"camera_configuration": [ { "stream": "ego_camera" }, { "stream": "depth" } ]
```

Stream names are read from any of `stream`, `streams`, `name`, `type`, `cameras_present`,
`camera_name` or `sensors`, recursing into nested lists — so the capture app's own shape
works unchanged:

```json
"camera_configuration": [
  { "mounting_locations": "head mounted", "cameras_present": ["ego_camera", "lidar"] }
]
```

Tokens containing `rgb`, `ego`, `color`, `wide` or `camera` count as **rgb**; `depth`,
**`lidar`** or `tof` count as **depth** — the app names the sensor, not the stream it
produces. A stream present but undeclared — or declared but absent — is an ERROR.

### 4.2 Expected by spec, but no slot exists → `FORMAT_GAP`

Absent is **not** a vendor defect; it is a capture-app change request. Present but wrong is
still an ERROR.

| Field | Also accepted | If present, must be | Check |
|---|---|---|---|
| `task_description` | `description` | non-empty, not a sentinel | B1.7 |
| `indoor_outdoor` | `indoor_or_outdoor`, `scene_setting` | one of `indoor`, `outdoor`, `mixed` | B1.10 |
| `episode_end_time` | `end_time_unix`, `end_time`, `end_timestamp` | **strictly greater than** `episode_start_time` | B1.11 |
| `latitude` + `longitude` | `lat`; `lon`, `lng` | both present (no range check today) | B2.5 |
| `subtask_annotations` | `subtasks`, `annotations` | non-empty | B2.6 |
| `instruction` | — | non-empty, not a sentinel | B2.7 |

### 4.3 Conditional — only checked when relevant

| Field | Also accepted | Required when | Check |
|---|---|---|---|
| `duration` | `duration_seconds`, `duration_s`, `length_seconds`, `total_duration`, `recording_duration` | declared anywhere — must be within **0.1 s** of the measured timestamp span | C2 |
| `authoritative_video` | `primary_video` | two video files exist | C5 (WARN) |
| `sensor_relation` | `registration`, `extrinsics`, `depth_registration`, `rgb_depth_extrinsic` | both RGB **and** depth present | C6 (WARN) |

¹ `session_id` is a **fallback**, tried only when no `episode_uuid` is declared.

### 4.4 Values that are rejected as placeholders

Presence alone is not enough. Any string field checked for presence is screened against a
deny-list, **fuzzy-matched by Levenshtein distance ≤ 2** for terms of 5+ characters — so
`"Unknown_Catagory"` (a misspelling) is still caught:

```
unknown · unknowncatagory · unknowncategory · na · none · null
tbd · todo · test · default · placeholder · undefined
```

Comparison strips all non-alphanumerics and lowercases, so `"N/A"`, `"n-a"` and `"NA"` are
the same token. A value equal to its own key name is also a sentinel. For `device_type` /
`device_model` these are additionally rejected as too generic:

```
iphone · android · phone · device
```

---

## 5. Sidecar files

### `video_timestamps.json` — the reference clock

Per-frame timestamps in **relative seconds**. This file is the authority everything else is
measured against; without it C1, C2 and C3 all go `BLOCKED`.

```json
[ { "chunk": "…", "data": { "timestamps": [0.0, 0.0332, 0.0664, …] } } ]
```

Accepted payload shapes: a bare list, or an object keyed `timestamps`, `relative_seconds`
or `frames`. List items may be numbers, or objects with a `timestamp` or `t` key.

Must be non-empty and **strictly increasing** — any step ≤ 0 fails B2.3.

### `motion_data.json` — IMU + UTC anchor

```json
[ { "chunk": "…", "data": {
    "metadata": {
      "recording_date": "2026-07-08T10:57:09Z",
      "coordinate_system": "gravity-aligned, Y up, X right",
      "duration": 8.1343
    },
    "timestamps": [0.0, 0.0098, 0.0196, …]
} } ]
```

- **UTC anchor** (B2.4): any of `recording_date`, `utc`, `utc_time`, `recorded_at`,
  `capture_time`. Must agree with `episode_start_time` within **1.0 s**. Falls back to
  `manifest.json → generated_at` if absent here.
- **IMU timestamps** (C3): any of `timestamps`, `imu_timestamps`, `sample_timestamps`,
  `times`. A **columnar dump** is also read — `{"columns": ["timestamp", "gyro_x", …],
  "data": [[…], …]}` — with the timestamp column located by name. Samples must span at
  least the video span at both ends, and no inter-sample gap may exceed **5×** the median
  video frame interval.
- Any `duration`-named key here is subject to the same 0.1 s concordance rule as metadata.

### `camera_intrinsic.json` — RGB stream marker

**Also accepted: `camera_intrinsics.json` (plural).**

```json
[ { "chunk": "…", "data": {
    "focal_length": [1456.254, 1456.254],
    "principal_point": [957.116, 710.713],
    "intrinsic_matrix": [1456.254, 0, 957.116, 0, 1456.254, 710.713, 0, 0, 1],
    "image_width": 1920, "image_height": 1440, "stream": "ego_camera"
} } ]
```

The file must exist whenever a video does, and must **declare** `focal_length`,
`principal_point`, `image_width`, `image_height` and `intrinsic_matrix` (C8). Nested forms
are fine — `"focal_length": {"fx": …, "fy": …}` resolves.

⚠️ **Declaration only — values are not validated.** Whether `image_width`/`image_height`
match the real video, or `intrinsic_matrix` agrees with `focal_length`/`principal_point`,
is out of scope by decision.

### `depth_camera_intrinsics.json` — depth stream index

```json
[ { "chunk": "…", "data": { "frames": [
    { "file": "depth_00000.bin", "bytes_per_row": 1024,
      "height": 192, "width": 256, "frame": 0, "timestamp": 0.0 }, …
] } } ]
```

Every record must **declare** `file`, `bytes_per_row`, `width` and `height` (C9), and every
referenced `.file` must exist in the package (C4).

⚠️ **Declaration only for the geometry — stride is not validated.** Whether
`bytes_per_row × height` equals the payload's real byte size is out of scope by decision.

**Payloads may be bundled.** `depth_*.bin` files are found either loose in the package or
inside **`depth_maps.zip`** (or `depth.zip`); C4 is satisfied by either.

### `hands.json` / `hands_metadata.json`

```json
// hands.json
[ { "chunk": "…", "data": { "frames": [ { "interactions": [], "objects": [], "hands": [] }, … ] } } ]

// hands_metadata.json
[ { "chunk": "…", "data": { "graspClassificationEnabled": true, "objectDetectionEnabled": true } } ]
```

If any key containing `enabled` is true, `hands.json` must exist with at least one frame
carrying non-empty `interactions`, `objects` or `hands` — otherwise C7 WARNs.

### `ar_session.json`

Only `len(frames)` is retained; the payload is dropped immediately to bound memory. Counts
toward C1 concordance.

### `manifest.json` — plain object, no envelope

```json
{ "doc_id": "…", "bucket_id": "…", "source_prefix": "…",
  "generated_at": "2026-07-08T10:57:10Z" }
```

`generated_at` is the fallback UTC anchor for B2.4.

---

## 6. Binary payloads

| File | Requirement |
|---|---|
| `video.mp4` / `video.mov` | **exactly one** present, or one declared via `authoritative_video` (C5). Content is never decoded — that is Layer A, out of scope. |
| `depth_*.bin` | every file named in `depth_camera_intrinsics.json` must exist (C4), whether loose or inside `depth_maps.zip`. Size and content are not checked. |

The streaming watcher never downloads these — it creates sparse stand-ins of the true size.
See `qa_watch/README.md`.

---

## 7. Cross-file rules

| Rule | Requirement | Check |
|---|---|---|
| Frame-count concordance | `video_timestamps`, `hands`, `ar_session` and `depth` frame counts must all be **equal**. Fewer than 2 streams → BLOCKED, not PASS. | C1 |
| Duration concordance | every declared `duration*` must be within **0.1 s** of `ts[-1] − ts[0]` | C2 |
| IMU coverage | IMU spans the video at both ends; max gap ≤ 5 × median frame interval | C3 |
| Referenced files | every `depth_*.bin` named in the index exists on disk | C4 |
| Single video | exactly one video, or an authoritative one declared | C5 |
| Sensor relation | with both RGB and depth, declare the extrinsic transform | C6 |
| Non-empty payloads | enabled hand streams must carry data | C7 |
| Camera intrinsics | present when a video is, declaring all five geometry fields | C8 |
| Depth geometry | every depth record declares `file`, `bytes_per_row`, `width`, `height` | C9 |
| Single chunk | `metadata.json` holds exactly one record | D2 |

---

## 8. A package that passes everything

```
PKG-0001/
├── metadata.json  manifest.json  video.mov  video_timestamps.json
├── camera_intrinsic.json  motion_data.json
├── depth_camera_intrinsics.json  depth_00000.bin … depth_00244.bin
└── hands.json  hands_metadata.json  ar_session.json
```

```json
[ { "chunk": "chunk_0000_raw", "data": { "metadata": {
  "episode_uuid":        "23DE884C-74DA-4BB1-A2E9-7E5B434CE7A3",
  "operator_id":         "OP-1837",
  "episode_start_time":  1784876817.882431,
  "episode_end_time":    1784876826.016731,
  "device_type":         "Apple iPhone 15 Pro",
  "device_model":        "iPhone15,2",
  "software_version":    "1.4.2",
  "camera_configuration": { "streams": ["ego_camera", "depth"] },
  "task_id":             "TASK-552",
  "task_category":       "Packaging",
  "task_description":    "Place mechanical parts into designated boxes",
  "instruction":         "Place mechanical parts into designated boxes",
  "environment_id":      "ENV-334",
  "environment_label":   "warehouse",
  "indoor_outdoor":      "indoor",
  "latitude":            12.9716,
  "longitude":           77.5946,
  "subtask_annotations": [ { "t0": 0.0, "t1": 3.2, "label": "pick" } ],
  "duration":            8.1343,
  "sensor_relation":     { "rgb_to_depth": [1,0,0,0, 0,1,0,0, 0,0,1,0] }
} } } ]
```

With `video_timestamps` spanning 8.1343 s across 245 frames, `hands`/`ar_session`/`depth`
also at 245 frames, IMU covering `[0, 8.30]`, and all 245 `depth_*.bin` present, this
package returns **ACCEPT** with zero ERRORs.

Drop `episode_end_time`, `task_description`, `indoor_outdoor`, `latitude`/`longitude` and
`subtask_annotations` and it still **ACCEPTs** — those five emit `FORMAT_GAP`, which never
blocks.

---

## 9. Verdicts

| Any check at | Verdict | Meaning |
|---|---|---|
| `ERROR` | **REJECT** | data defect at the source |
| `BLOCKED` (no ERROR) | **REVIEW** | could not evaluate — missing prerequisite |
| otherwise | **ACCEPT** | `WARN`, `INFO`, `CEILING` and `FORMAT_GAP` never block |

Exit codes for the batch CLI: `0` nothing rejected · `1` at least one REJECT · `2` bad path
or no packages found.

---

## 10. Known gaps in the input contract

Things the pipeline reads but does **not** validate — relevant if you are producing data
against this contract and assuming silence means correctness:

- Intrinsic **values** are not checked — C8 confirms they are declared, but
  `image_width`/`image_height` are never compared against the real video resolution, and
  `intrinsic_matrix` is never checked against `focal_length` / `principal_point`.
  *Out of scope by decision.*
- Depth **stride** is not checked — C9 confirms `bytes_per_row` is declared, never that
  `bytes_per_row × height` equals the payload's real size. A 0-byte `.bin` still passes C4.
  *Out of scope by decision.*
- IMU sample **rate** is not validated; only gaps relative to the video frame interval.
- Frame-rate **stability** is not checked — B2.3 only requires timestamps to increase, so a
  clip dropping 60 fps → 5 fps mid-recording passes.
- `latitude` / `longitude` have no range check.
- `episode_end_time − episode_start_time` is never compared to the measured video span; only
  ordering is enforced.
- Four checks (B1.6, C4, C6, C7) return `PASS` when their inputs are absent rather than
  `BLOCKED`, so an empty package scores passes it did not earn.
