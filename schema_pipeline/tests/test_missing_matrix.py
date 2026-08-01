"""The missing-parameter matrix: shape, cell semantics, and what gets stored.

The builder is pure, so these run without a MongoDB. The store test drives a
stand-in collection, which checks the write contract (upsert keyed by session
id) without requiring pymongo or a server.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa import missing_matrix as M          # noqa: E402
from qa import ruleset as R                 # noqa: E402
from qa.checks import check, passed         # noqa: E402


def report(checks, **kw):
    base = {
        "package_id": "sess-1",
        "episode_uuid": "F2094A73-1309-4A05-B134-F52B5B32CD53",
        "episode_uuid_source": "manifest.json:doc_id",
        "chunk_count": 2,
        "chunk_names": ["chunk_0000_raw", "chunk_0001_raw"],
        "validated_at": "2026-07-27T00:00:00Z",
        "pipeline_version": R.PIPELINE_VERSION,
        "spec_version": R.SPEC_VERSION,
        "verdict": R.VERDICT_REJECT,
        "counts": {},
        "checks": checks,
    }
    base.update(kw)
    return base


def chunked(rec, chunks):
    rec = dict(rec)
    rec["chunks"] = list(chunks)
    rec["chunk_count"] = len(chunks)
    return rec


class MatrixShapeTest(unittest.TestCase):

    def test_id_is_the_session_id(self):
        doc = M.build_document(report([]))
        self.assertEqual(doc["_id"], "sess-1")
        self.assertEqual(doc["session_id"], "sess-1")

    def test_matrix_is_chunks_by_parameters(self):
        doc = M.build_document(report([]))
        self.assertEqual(doc["rows"], ["chunk_0000_raw", "chunk_0001_raw"])
        self.assertEqual(doc["columns"], M.COLUMNS)
        self.assertEqual(len(doc["matrix"]), 2)
        self.assertTrue(all(len(row) == len(M.COLUMNS) for row in doc["matrix"]))

    def test_columns_are_the_same_vector_for_every_session(self):
        a = M.build_document(report([]), session_id="a")
        b = M.build_document(report([], chunk_count=5, chunk_names=[None] * 5),
                             session_id="b")
        self.assertEqual(a["columns"], b["columns"])
        self.assertEqual(a["column_checks"], b["column_checks"])

    def test_unrun_check_is_not_evaluated_not_a_pass(self):
        doc = M.build_document(report([]))
        col = M.COLUMNS.index("operator_id")
        self.assertEqual(doc["matrix"][0][col], M.NOT_EVALUATED)
        self.assertNotIn("operator_id", doc["missing_parameters"])

    def test_unnamed_chunks_get_positional_row_labels(self):
        doc = M.build_document(report([], chunk_names=[None, None]))
        self.assertEqual(doc["rows"], ["#0", "#1"])

    def test_report_without_chunk_information_gets_one_row(self):
        legacy = report([])
        del legacy["chunk_names"]
        del legacy["chunk_count"]
        doc = M.build_document(legacy)
        self.assertEqual(doc["rows"], ["sess-1"])
        self.assertEqual(len(doc["matrix"]), 1)


class CellSemanticsTest(unittest.TestCase):

    def test_pass_is_ok_and_not_missing(self):
        rec = chunked(passed("B2.1", "task_id_present"), ["chunk_0000_raw"])
        doc = M.build_document(report([rec]))
        col = M.COLUMNS.index("task_id")
        self.assertEqual(doc["matrix"][0][col], M.OK)
        self.assertEqual(doc["missing_parameters"], [])

    def test_error_marks_only_the_chunks_it_names(self):
        rec = chunked(check("B2.1", "task_id_present", R.ERROR, code="TASK_ID_MISSING",
                            message="task_id is missing"), ["chunk_0001_raw"])
        doc = M.build_document(report([rec]))
        col = M.COLUMNS.index("task_id")
        self.assertEqual(doc["matrix"][0][col], M.NOT_EVALUATED)
        self.assertEqual(doc["matrix"][1][col], M.ERROR)
        self.assertEqual(doc["missing_by_row"][0]["missing"], [])
        self.assertEqual(doc["missing_by_row"][1]["missing"], ["task_id"])

    def test_session_scope_finding_applies_to_every_chunk(self):
        rec = check("B1.1", "episode_uuid_valid", R.ERROR, code="EPISODE_UUID_INVALID",
                    message="episode_uuid is missing")
        doc = M.build_document(report([rec]))
        col = M.COLUMNS.index("episode_uuid")
        self.assertEqual([row[col] for row in doc["matrix"]], [M.ERROR, M.ERROR])

    def test_format_gap_and_blocked_count_as_missing_but_warn_does_not(self):
        recs = [
            chunked(check("B2.5", "geo_location_present", R.FORMAT_GAP,
                          code="GEO_LOCATION_MISSING", message="geo lat/lon"),
                    ["chunk_0000_raw"]),
            chunked(check("C6", "sensor_relation_declared", R.WARN,
                          code="SENSOR_RELATION_UNDECLARED", message="no extrinsics"),
                    ["chunk_0000_raw"]),
            chunked(check("C3", "imu_covers_video_span", R.BLOCKED,
                          code="IMU_COVERAGE_GAP", message="no imu timestamps"),
                    ["chunk_0000_raw"]),
        ]
        doc = M.build_document(report(recs))
        self.assertEqual(doc["missing_parameters"], ["geo_location", "imu_coverage"])
        self.assertEqual(doc["matrix"][0][M.COLUMNS.index("sensor_relation")], M.WARN)

    def test_missing_split_by_severity_mirrors_the_delivery_buckets(self):
        recs = [
            chunked(check("B2.1", "task_id_present", R.ERROR, code="TASK_ID_MISSING",
                          message="missing"), ["chunk_0000_raw"]),
            chunked(check("B2.7", "instruction_present", R.FORMAT_GAP,
                          code="INSTRUCTION_MISSING", message="no location"),
                    ["chunk_0000_raw"]),
            chunked(check("C1", "frame_count_concordance", R.BLOCKED,
                          code="FRAME_COUNT_MISMATCH", message="unevaluable"),
                    ["chunk_0000_raw"]),
        ]
        doc = M.build_document(report(recs))
        self.assertEqual(doc["missing_by_severity"], {
            R.ERROR: ["task_id"],
            R.BLOCKED: ["frame_count_concordance"],
            R.FORMAT_GAP: ["instruction"],
        })

    def test_worst_severity_wins_when_two_checks_share_a_column(self):
        # B1.1 (chunk uuid label) and B1.1u (delivery duplicate) are one parameter.
        recs = [
            passed("B1.1", "episode_uuid_valid"),
            check("B1.1u", "episode_uuid_unique", R.ERROR,
                  code="EPISODE_UUID_DUPLICATE", message="shared by sessions: a, b"),
        ]
        doc = M.build_document(report(recs))
        col = M.COLUMNS.index("episode_uuid")
        self.assertEqual(doc["matrix"][0][col], M.ERROR)
        self.assertEqual(doc["details"]["episode_uuid"]["code"], "EPISODE_UUID_DUPLICATE")

    def test_detail_carries_the_reason_the_parameter_is_missing(self):
        rec = chunked(check("B1.4", "device_identified", R.ERROR,
                            code="DEVICE_UNDERSPECIFIED", observed="iPhone",
                            expected="identifies hardware generation",
                            source_file="metadata.json", spec_ref="Doc1 §5.3",
                            message="device_model underspecified: generic"),
                      ["chunk_0000_raw", "chunk_0001_raw"])
        doc = M.build_document(report([rec]))
        detail = doc["details"]["device_identity"]
        self.assertEqual(detail["check"], "B1.4")
        self.assertEqual(detail["code"], "DEVICE_UNDERSPECIFIED")
        self.assertEqual(detail["observed"], "iPhone")
        self.assertEqual(detail["spec_ref"], "Doc1 §5.3")
        self.assertEqual(detail["rows"], ["chunk_0000_raw", "chunk_0001_raw"])


class UnmappedFindingsTest(unittest.TestCase):

    def test_parse_errors_are_kept_out_of_the_columns_but_not_lost(self):
        rec = check("PARSE:metadata.json", "parse:metadata.json", R.ERROR,
                    code="PARSE_ERROR", message="metadata.json failed to parse")
        doc = M.build_document(report([rec]))
        self.assertEqual(len(doc["parse_errors"]), 1)
        self.assertEqual(doc["parse_errors"][0]["check"], "PARSE:metadata.json")
        self.assertEqual(doc["unmapped"], [])

    def test_unknown_check_id_is_recorded_rather_than_dropped(self):
        rec = check("Z9", "future_check", R.ERROR, code="SOMETHING_NEW", message="new")
        doc = M.build_document(report([rec]))
        self.assertEqual([u["check"] for u in doc["unmapped"]], ["Z9"])

    def test_a_finding_naming_an_undeclared_chunk_gets_its_own_row(self):
        rec = chunked(check("B2.1", "task_id_present", R.ERROR, code="TASK_ID_MISSING",
                            message="missing"), ["chunk_0007_raw"])
        doc = M.build_document(report([rec]))
        self.assertEqual(doc["rows"][-1], "chunk_0007_raw")
        self.assertEqual(len(doc["matrix"]), 3)
        self.assertEqual(doc["matrix"][2][M.COLUMNS.index("task_id")], M.ERROR)


class DeliveryTest(unittest.TestCase):

    def _delivery(self):
        a = report([chunked(check("B2.1", "task_id_present", R.ERROR,
                                  code="TASK_ID_MISSING", message="missing"),
                            ["chunk_0000_raw"])], package_id="sess-a")
        b = report([chunked(check("B2.1", "task_id_present", R.ERROR,
                                  code="TASK_ID_MISSING", message="missing"),
                            ["chunk_0000_raw"]),
                    chunked(check("B2.5", "geo_location_present", R.FORMAT_GAP,
                                  code="GEO_LOCATION_MISSING", message="no lat/lon"),
                            ["chunk_0000_raw"])], package_id="sess-b")
        return {"packages": [a, b], "aggregate": {}}

    def test_one_document_per_session(self):
        docs = M.build_documents(self._delivery())
        self.assertEqual([d["_id"] for d in docs], ["sess-a", "sess-b"])

    def test_rollup_ranks_parameters_by_sessions_affected(self):
        rollup = M.delivery_rollup(M.build_documents(self._delivery()))
        self.assertEqual(rollup["sessions"], 2)
        self.assertEqual(rollup["missing_sessions_by_parameter"],
                         [{"parameter": "task_id", "sessions": 2},
                          {"parameter": "geo_location", "sessions": 1}])


class SessionGroupingTest(unittest.TestCase):
    """Per-video reports (pre-session-refactor) group back into one document."""

    def _per_video(self):
        def pkg(pid, checks):
            rep = report(checks, package_id=pid)
            del rep["chunk_names"]
            del rep["chunk_count"]
            return rep

        return {"packages": [
            pkg("a01524-H-0147", [check("B2.1", "task_id_present", R.ERROR,
                                        code="TASK_ID_MISSING", message="missing")]),
            pkg("a01524-H-0148", [check("B2.5", "geo_location_present", R.FORMAT_GAP,
                                        code="GEO_LOCATION_MISSING", message="no lat/lon")]),
            pkg("b02001-H-0001", [passed("B2.1", "task_id_present")]),
        ]}

    def _grouped(self):
        return M.build_documents(self._per_video(),
                                 session_of=lambda pid: pid.split("-")[0])

    def test_one_document_per_session_not_per_video(self):
        docs = self._grouped()
        self.assertEqual([d["_id"] for d in docs], ["a01524", "b02001"])

    def test_each_video_becomes_a_row(self):
        doc = self._grouped()[0]
        self.assertEqual(doc["rows"], ["a01524-H-0147", "a01524-H-0148"])
        self.assertEqual(doc["members"], ["a01524-H-0147", "a01524-H-0148"])
        self.assertEqual(len(doc["matrix"]), 2)
        self.assertTrue(all(len(r) == len(M.COLUMNS) for r in doc["matrix"]))

    def test_missing_is_the_union_but_rows_stay_distinct(self):
        doc = self._grouped()[0]
        self.assertEqual(doc["missing_parameters"], ["geo_location", "task_id"])
        self.assertEqual(doc["missing_by_row"][0]["missing"], ["task_id"])
        self.assertEqual(doc["missing_by_row"][1]["missing"], ["geo_location"])

    def test_verdict_is_the_worst_of_the_members(self):
        docs = self._grouped()
        self.assertEqual(docs[0]["verdict"], R.VERDICT_REJECT)

    def test_ungrouped_report_is_untouched_by_the_merge_path(self):
        plain = M.build_documents({"packages": [report([])]})
        same = M.build_documents({"packages": [report([])]},
                                 session_of=lambda pid: pid)
        self.assertEqual(plain, same)


class SlimShapeTest(unittest.TestCase):
    """The stored key/value pair: session id -> its missing parameters."""

    def _doc(self):
        return M.build_document(report([
            chunked(check("B2.1", "task_id_present", R.ERROR, code="TASK_ID_MISSING",
                          message="missing"), ["chunk_0000_raw"]),
            chunked(check("B2.5", "geo_location_present", R.FORMAT_GAP,
                          code="GEO_LOCATION_MISSING", message="no lat/lon"),
                    ["chunk_0001_raw"]),
            chunked(passed("B1.2", "operator_id_valid"), ["chunk_0000_raw"]),
        ]))

    def test_slim_document_is_only_the_id_and_the_missing_values(self):
        slim = M.slim_document(self._doc())
        self.assertEqual(set(slim), {"_id", "missing_parameters"})
        self.assertEqual(slim["_id"], "sess-1")
        self.assertEqual(slim["missing_parameters"], ["geo_location", "task_id"])

    def test_slim_agrees_with_the_full_document(self):
        full = self._doc()
        self.assertEqual(M.slim_document(full)["missing_parameters"],
                         full["missing_parameters"])

    def test_slim_documents_maps_the_whole_delivery(self):
        docs = M.build_documents({"packages": [report([]), report([], package_id="s2")]})
        slim = M.slim_documents(docs)
        self.assertEqual([d["_id"] for d in slim], ["sess-1", "s2"])
        self.assertTrue(all(set(d) == {"_id", "missing_parameters"} for d in slim))

    def test_a_clean_session_stores_an_empty_list_not_a_missing_key(self):
        clean = M.build_document(report([chunked(passed("B2.1", "task_id_present"),
                                                 ["chunk_0000_raw"])]))
        self.assertEqual(M.slim_document(clean),
                         {"_id": "sess-1", "missing_parameters": []})


class FakeCollection:
    """Enough of a pymongo collection to check the write contract."""

    def __init__(self):
        self.docs = {}
        self.indexes = []

    def create_index(self, keys, **kw):
        self.indexes.append(keys)

    def replace_one(self, flt, doc, upsert=False):
        self.docs[flt["_id"]] = doc

    def bulk_write(self, ops, ordered=True):
        upserted = {}
        matched = modified = 0
        for i, (flt, doc) in enumerate(ops):
            if flt["_id"] in self.docs:
                matched += 1
                modified += 1
            else:
                upserted[i] = flt["_id"]
            self.docs[flt["_id"]] = doc

        class Res:
            matched_count = matched
            modified_count = modified
            upserted_ids = upserted
        return Res()

    def find_one(self, flt, projection=None):
        return self.docs.get(flt["_id"])


class StoreContractTest(unittest.TestCase):
    """The store's semantics, driven against FakeCollection."""

    def setUp(self):
        self.col = FakeCollection()

    def _save_many(self, docs):
        # Mirrors MissingParamStore.save_many without importing pymongo.
        return self.col.bulk_write([({"_id": d["_id"]}, d) for d in docs])

    def test_rewriting_a_session_replaces_it_rather_than_duplicating(self):
        docs = M.build_documents({"packages": [report([])]})
        self._save_many(docs)
        res = self._save_many(docs)
        self.assertEqual(len(self.col.docs), 1)
        self.assertEqual(res.modified_count, 1)
        self.assertEqual(len(res.upserted_ids), 0)

    def test_document_keys_are_mongo_safe(self):
        doc = M.build_document(report([
            chunked(check("B2.5", "geo_location_present", R.FORMAT_GAP,
                          code="GEO_LOCATION_MISSING", message="no lat/lon"),
                    ["chunk_0000_raw"])]))

        def walk(obj, path="root"):
            if isinstance(obj, dict):
                for key, val in obj.items():
                    self.assertIsInstance(key, str, path)
                    self.assertFalse(key.startswith("$"), f"{path}.{key}")
                    self.assertNotIn(".", key, f"{path}.{key}")
                    walk(val, f"{path}.{key}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")

        walk(doc)


if __name__ == "__main__":
    unittest.main()
