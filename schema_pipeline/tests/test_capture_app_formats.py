#!/usr/bin/env python3
"""Formats and spellings the iOS capture app actually emits.

Each case here corresponds to a difference found between a real capture package
and the reference contract, in three groups:

  A. structure  -- the package is a chunk zip; depth payloads are bundled in a
                   nested archive; the intrinsics file is named in the plural
  B. naming     -- start_timestamp / end_timestamp / total_duration / session_id
                   carry data the validator was looking for under other names
  C. vocabulary -- camera_configuration names sensors ("lidar") under
                   "cameras_present"; a placeholder environment label

The fixtures are synthetic, modelled on the real shapes. No capture data is
committed.

Run:  python tests/test_capture_app_formats.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qa import layerc, pkg_ingest, ruleset as R, validate      # noqa: E402
from qa.layerb import Context, _config_streams                 # noqa: E402

FRAMES = 12
FPS = 30.0
START = 1784962877.738904
SPAN = (FRAMES - 1) / FPS


def _timestamps():
    return [round(i / FPS, 6) for i in range(FRAMES)]


def capture_app_metadata(**overrides):
    """metadata.json as the capture app writes it -- a bare object, and every
    time-related field under its own spelling."""
    meta = {
        "metadata": {
            "episode_uuid": "chunk_0000_raw",          # the chunk name, not a UUID
            "session_id": "F2094A73-1309-4A05-B134-F52B5B32CD53",   # the real one
            "operator_id": "d21ce25e-1e9c-4d08-aad3-bcc57acc3510",
            "start_timestamp": START,
            "end_timestamp": START + SPAN,
            "total_duration": SPAN,
            "device_type": "smartphone",
            "device_model": "iPhone 12 Pro",
            "software_version": "26.5.2",
            "camera_configuration": [
                {"mounting_locations": "head mounted",
                 "cameras_present": ["ego_camera", "lidar"]},
            ],
            "task_id": "FAGCS1-001",
            "task_category": "Factory",
            "environment_id": "IN-001",
            "environment_label": "factory floor",
        }
    }
    meta["metadata"].update(overrides)
    return meta


def build_capture_package(dest: str, *, as_zip: bool = True,
                          depth_in_archive: bool = True,
                          plural_intrinsics: bool = True,
                          columnar_imu: bool = True,
                          **meta_overrides) -> str:
    """Materialize a package in the capture app's own shape."""
    vts = _timestamps()
    work = tempfile.mkdtemp()

    files = {
        "metadata.json": capture_app_metadata(**meta_overrides),
        "video_timestamps.json": {"format": "arframe_timestamp", "unit": "seconds",
                                  "timestamps": vts},
        ("camera_intrinsics.json" if plural_intrinsics else "camera_intrinsic.json"): {
            "camera_name": "ego_camera",
            "intrinsic_matrix": [[1479.09, 0, 964.94], [0, 1479.09, 704.24], [0, 0, 1]],
            "focal_length": {"fx": 1479.09, "fy": 1479.09},
            "principal_point": {"cx": 964.94, "cy": 704.24},
            "image_width": 1920, "image_height": 1440,
        },
        "depth_camera_intrinsics.json": {"frames": [
            {"frame": i, "file": f"depth_{i:05d}.bin", "timestamp": vts[i],
             "width": 256, "height": 192, "bytes_per_row": 1024}
            for i in range(FRAMES)]},
        "hands.json": {"frames": [{"timestamp": vts[i], "interactions": [],
                                   "objects": []} for i in range(FRAMES)]},
        "hands_metadata.json": {"objectDetectionEnabled": False, "totalFrames": FRAMES},
        "ar_session.json": {"frames": [{"frame": i} for i in range(FRAMES)]},
    }

    if columnar_imu:
        step = 1.0 / 100.0
        n = int(SPAN / step) + 3
        files["motion_data.json"] = {
            "metadata": {"recording_date": "2026-07-25T07:01:33Z",
                         "columns": ["timestamp", "gyro_x", "accel_x"],
                         "sampling_frequency": 100},
            "data": [[round(-step + i * step, 6), 0.1, 0.2] for i in range(n)],
        }
    else:
        files["motion_data.json"] = {
            "metadata": {"recording_date": "2026-07-25T07:01:33Z"},
            "timestamps": [round(i / 100.0, 6) for i in range(int(SPAN * 100) + 2)],
        }

    for name, obj in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    with open(os.path.join(work, "video.mov"), "wb") as fh:
        fh.write(b"\0" * 2048)

    depth_blob = b"\0" * 4096
    if depth_in_archive:
        with zipfile.ZipFile(os.path.join(work, "depth_maps.zip"), "w") as zf:
            for i in range(FRAMES):
                zf.writestr(f"depth_{i:05d}.bin", depth_blob)
    else:
        for i in range(FRAMES):
            with open(os.path.join(work, f"depth_{i:05d}.bin"), "wb") as fh:
                fh.write(depth_blob)

    if not as_zip:
        return work
    zip_path = os.path.join(dest, "chunk_0000_raw.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in sorted(os.listdir(work)):
            zf.write(os.path.join(work, name), name)
    return zip_path


def validate_path(path, package_id=None):
    pkg = pkg_ingest.load_package(path, package_id=package_id)
    report = validate.validate_package(pkg, Context(now_unix=time.time()),
                                       "2026-07-25T00:00:00Z")
    return pkg, report, {c["id"]: c for c in report["checks"]}


# ===========================================================================
# A -- structure
# ===========================================================================
class TestPackageStructure(unittest.TestCase):
    def test_reads_a_package_shipped_as_a_zip(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, by_id = validate_path(build_capture_package(d))
            self.assertTrue(pkg.has_meta)
            self.assertEqual(pkg.record_count, 1)
            self.assertEqual(len(pkg.video_timestamps), FRAMES)
            self.assertEqual(by_id["D2"]["severity"], R.PASS)

    def test_plural_camera_intrinsics_filename_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d, plural_intrinsics=True))
            self.assertIsNotNone(pkg.camera_intrinsic,
                                 "camera_intrinsics.json (plural) must be read")

    def test_depth_payloads_inside_depth_maps_zip_are_found(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, by_id = validate_path(build_capture_package(d, depth_in_archive=True))
            self.assertEqual(len(pkg.depth_bins_present), FRAMES)
            self.assertEqual(pkg.depth_archives, ["depth_maps.zip"])
            self.assertEqual(by_id["C4"]["severity"], R.PASS,
                             by_id["C4"]["message"])

    def test_loose_depth_bins_still_work(self):
        with tempfile.TemporaryDirectory() as d:
            path = build_capture_package(d, as_zip=False, depth_in_archive=False)
            pkg, _, by_id = validate_path(path)
            self.assertEqual(len(pkg.depth_bins_present), FRAMES)
            self.assertEqual(by_id["C4"]["severity"], R.PASS)

    def test_episode_folder_names_the_package_not_the_chunk(self):
        """<episode>/chunk_0000_raw.zip must not be called "chunk_0000_raw",
        or every episode in a delivery collides."""
        with tempfile.TemporaryDirectory() as root:
            ep = os.path.join(root, "EPISODE-0042")
            os.makedirs(ep)
            build_capture_package(ep)
            packages = pkg_ingest.discover_packages(root)
            self.assertEqual([p.package_id for p in packages], ["EPISODE-0042"])


# ===========================================================================
# B -- naming: data that exists under another spelling
# ===========================================================================
class TestFieldNaming(unittest.TestCase):
    def test_start_timestamp_resolves_as_episode_start_time(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["B1.3"]["severity"], R.PASS,
                             by_id["B1.3"]["message"])

    def test_end_timestamp_resolves_as_episode_end_time(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["B1.11"]["severity"], R.PASS,
                             by_id["B1.11"]["message"])

    def test_declared_episode_uuid_wins_over_the_session_id_fallback(self):
        """Resolution must not depend on JSON key order. A package declaring
        episode_uuid keeps it -- even when the value is a chunk name -- so the
        defect is reported rather than silently patched from session_id."""
        with tempfile.TemporaryDirectory() as d:
            _, report, by_id = validate_path(build_capture_package(d))
            self.assertEqual(report["episode_uuid"], "chunk_0000_raw")
            self.assertEqual(by_id["B1.1"]["severity"], R.ERROR)
            self.assertIn("does not parse as a UUID", by_id["B1.1"]["message"])

    def test_session_id_is_used_when_episode_uuid_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            _, report, by_id = validate_path(
                build_capture_package(d, episode_uuid=""))
            self.assertEqual(report["episode_uuid"],
                             "F2094A73-1309-4A05-B134-F52B5B32CD53")
            self.assertEqual(by_id["B1.1"]["severity"], R.PASS)

    def test_resolution_is_independent_of_key_order(self):
        """The same two fields in either order must resolve identically."""
        from qa import normalize as N
        order_a = {"episode_uuid": "chunk_0000_raw", "session_id": "F2094A73"}
        order_b = {"session_id": "F2094A73", "episode_uuid": "chunk_0000_raw"}
        canons = R.FIELD_CANON_ORDER["episode_uuid"]
        self.assertEqual(N.value_in_order(N.flatten(order_a), canons),
                         N.value_in_order(N.flatten(order_b), canons))

    def test_total_duration_is_tested_for_concordance(self):
        """The whole point of aliasing it: a wrong duration must now fail."""
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["C2"]["severity"], R.PASS)
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(
                build_capture_package(d, total_duration=SPAN + 9.87))
            self.assertEqual(by_id["C2"]["severity"], R.ERROR)
            self.assertIn("conflict", by_id["C2"]["message"])

    def test_columnar_imu_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, by_id = validate_path(build_capture_package(d, columnar_imu=True))
            self.assertIsNotNone(layerc._imu_timestamps(pkg),
                                 "columns/data IMU layout must be readable")
            self.assertEqual(by_id["C3"]["severity"], R.PASS, by_id["C3"]["message"])

    def test_named_imu_timestamps_still_work(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d, columnar_imu=False))
            self.assertEqual(by_id["C3"]["severity"], R.PASS)


# ===========================================================================
# C -- vocabulary
# ===========================================================================
class TestVocabulary(unittest.TestCase):
    def test_lidar_counts_as_a_depth_stream(self):
        self.assertEqual(_config_streams([{"cameras_present": ["lidar"]}]), {"depth"})

    def test_cameras_present_key_is_understood(self):
        cfg = [{"mounting_locations": "head mounted",
                "cameras_present": ["ego_camera", "lidar"]}]
        self.assertEqual(_config_streams(cfg), {"rgb", "depth"})

    def test_existing_config_shapes_still_parse(self):
        self.assertEqual(_config_streams({"streams": ["rgb", "depth"]}), {"rgb", "depth"})
        self.assertEqual(_config_streams([{"stream": "ego_camera"}]), {"rgb"})
        self.assertEqual(_config_streams("depth"), {"depth"})

    def test_camera_config_passes_when_both_sensors_are_declared(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["B1.6"]["severity"], R.PASS,
                             by_id["B1.6"]["message"])

    def test_camera_config_still_fails_when_a_sensor_is_undeclared(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(
                d, camera_configuration=[{"cameras_present": ["lidar"]}]))
            self.assertEqual(by_id["B1.6"]["severity"], R.ERROR)
            self.assertIn("rgb", by_id["B1.6"]["message"])

    def test_placeholder_environment_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(
                d, environment_label="Unknown"))
            self.assertEqual(by_id["B1.9"]["severity"], R.ERROR)
            self.assertIn("sentinel", by_id["B1.9"]["message"])

    def test_real_environment_label_passes(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["B1.9"]["severity"], R.PASS)


# ===========================================================================
# Presence-only checks (C8, C9) -- declared, never value-validated
# ===========================================================================
class TestDeclarationChecks(unittest.TestCase):
    def test_intrinsics_declared_passes(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["C8"]["severity"], R.PASS, by_id["C8"]["message"])

    def test_intrinsics_values_are_not_validated(self):
        """image_height contradicting the real video is out of scope by
        decision -- C8 must still pass."""
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            pkg.camera_intrinsic["image_height"] = 99999
            self.assertEqual(layerc.c8_camera_intrinsics_declared(pkg)["severity"],
                             R.PASS)

    def test_missing_intrinsics_with_a_video_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            pkg.camera_intrinsic = None
            rec = layerc.c8_camera_intrinsics_declared(pkg)
            self.assertEqual(rec["severity"], R.ERROR)
            self.assertEqual(rec["code"], "CAMERA_INTRINSICS_MISSING")

    def test_incomplete_intrinsics_names_what_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            del pkg.camera_intrinsic["principal_point"]
            rec = layerc.c8_camera_intrinsics_declared(pkg)
            self.assertEqual(rec["severity"], R.ERROR)
            self.assertIn("principal_point", rec["message"])

    def test_depth_geometry_declared_passes(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, by_id = validate_path(build_capture_package(d))
            self.assertEqual(by_id["C9"]["severity"], R.PASS, by_id["C9"]["message"])

    def test_depth_stride_value_is_not_validated(self):
        """bytes_per_row disagreeing with the payload's real size is out of
        scope by decision -- C9 checks only that it is declared."""
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            for frame in pkg.depth_frames:
                frame["bytes_per_row"] = 999999
            self.assertEqual(layerc.c9_depth_geometry_declared(pkg)["severity"], R.PASS)

    def test_undeclared_depth_geometry_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            for frame in pkg.depth_frames:
                del frame["bytes_per_row"]
            rec = layerc.c9_depth_geometry_declared(pkg)
            self.assertEqual(rec["severity"], R.ERROR)
            self.assertIn("bytes_per_row", rec["message"])

    def test_no_depth_stream_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            pkg, _, _ = validate_path(build_capture_package(d))
            pkg.depth_frames = None
            self.assertEqual(layerc.c9_depth_geometry_declared(pkg)["severity"], R.PASS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
