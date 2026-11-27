"""The one reader of os-optics imaging analyses (os_optics_kit.imaging_results), on saved documents: no engine run.

Fixtures: tests/data/mtf-sonnar-imaging.json is the untrimmed os-optics 0.14.0 analysis of the sonnar-telephoto-lens
case (its imaging-request.json); tests/data/mtf-edge-imaging.json is a small synthetic analysis of the same shape with
the cases a reader must not get wrong (unconverged, beyond Nyquist, stand-in pupil, declined with an array, absent,
no arrival, not a beam source, a requested wavelength without a line).
"""

from __future__ import annotations

import json
import unittest

from os_optics_kit import imaging_results as ir
from tests.workflow_fixtures import HAS_ENGINE, ROOT, TempDir

SONNAR = ROOT / "tests" / "data" / "mtf-sonnar-imaging.json"
EDGE = ROOT / "tests" / "data" / "mtf-edge-imaging.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class LoadTests(unittest.TestCase):
    def test_path_dict_and_utf16_give_the_same_document(self):
        document = ir.load(SONNAR)
        self.assertEqual(ir.load(read(SONNAR)), document)
        with TempDir() as tmp:
            utf16 = tmp / "imaging.json"
            utf16.write_bytes(SONNAR.read_text(encoding="utf-8").encode("utf-16"))  # a Windows '>' redirect
            self.assertEqual(ir.load(utf16), document)

    def test_an_object_with_model_dump_is_read_as_json(self):
        class Result:
            def model_dump(self, mode, by_alias=False):
                assert (mode, by_alias) == ("json", True)  # the JSON 'os-optics results imaging' prints
                return read(EDGE)

        self.assertEqual(ir.load(Result()), read(EDGE))

    @unittest.skipUnless(HAS_ENGINE, "needs os-optics for its AnalysisResultV1")
    def test_the_engine_result_object_and_both_fixtures_validate(self):
        from os_optics.contracts.analysis import AnalysisResultV1

        for path in (SONNAR, EDGE):
            with self.subTest(path=path.name):
                model = AnalysisResultV1.model_validate(read(path))
                self.assertEqual(ir.load(model)["quantities"], ir.load(path)["quantities"])

    def test_refusals(self):
        with TempDir() as tmp:
            (tmp / "text.json").write_text("not json", encoding="utf-8")
            cases = {"missing file": tmp / "absent.json", "not JSON": tmp / "text.json",
                     "another document": ROOT / "baseline.json", "not a document": 3,
                     "no quantities": {**read(EDGE), "quantities": None}}
            for what, source in cases.items():
                with self.subTest(what=what), self.assertRaises(ir.ImagingDocumentError) as caught:
                    ir.load(source)
                self.assertEqual(caught.exception.as_dict()["code"], "IMAGING_INVALID")


class LineTests(unittest.TestCase):
    def test_sonnar_lines_identity_and_axes(self):
        document = ir.load(SONNAR)
        lines = ir.lines(document)
        self.assertEqual([line["line_id"] for line in lines], ["SRC0#0", "SRC1#0", "SRC2#0"])
        self.assertEqual({line["wavelength_nm"] for line in lines}, {587.5618})
        self.assertEqual(lines[0]["frequencies_cyc_per_mm"], [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0])
        self.assertEqual(lines[2]["rays"]["reference_point_mm"][2], -21.1921651)
        identity = ir.identity(document)
        self.assertEqual(identity["engine_input_hash"][:12], "a6e2023bcc4f")
        self.assertEqual((identity["monitor"], identity["analysis_version"], identity["execution_outcome"]),
                         ("image_plane", "1.2", "COMPLETED"))
        self.assertEqual(identity["request"]["launch_grid"], 256)

    def test_no_arrival_and_not_a_beam_source(self):
        document = ir.load(EDGE)
        lines = {line["line_id"]: line for line in ir.lines(document)}
        self.assertEqual(list(lines), ["A#0", "B#0", "B#1"])
        self.assertEqual((lines["B#1"]["field_state"], lines["B#1"]["field_reason"]), ("UNKNOWN", "NO_ARRIVAL"))
        self.assertIsNone(lines["B#1"]["frequencies_cyc_per_mm"])
        self.assertEqual(ir.unanalysed_fields(document), [
            {"source_id": "NOPE", "state": "NOT_APPLICABLE", "reason": "FIELD_NOT_A_BEAM_SOURCE",
             "message": "NOPE is not a beam source of this run (an extended emitter, or no such source)"}])

    def test_line_of_path(self):
        for path, line in (("SRC#0", "SRC#0"), ("SRC#0:p1", "SRC#0"), ("SRC#0.1.2", "SRC#0"),
                           ("F1#12~0123456789abcdef0123456789abcdef.1", "F1#12"), ("SRC", None), ("SRC#0x", None),
                           (None, None)):
            with self.subTest(path=path):
                self.assertEqual(ir.line_of_path(path), line)


class CurveTests(unittest.TestCase):
    def setUp(self):
        self.edge = ir.load(EDGE)
        self.sonnar = ir.load(SONNAR)

    def test_a_known_curve_is_the_stored_array(self):
        record = ir.curve(self.sonnar, "SRC0#0", "mtf", "tangential")
        arrays = self.sonnar["arrays"]
        self.assertEqual(record["values"], arrays["SRC0#0.mtf_tangential"])
        self.assertEqual(record["frequencies_cyc_per_mm"], arrays["SRC0#0.frequencies_cyc_per_mm"])
        self.assertEqual((record["status"], record["value_kind"], record["usable"], record["converged"]),
                         ("KNOWN", "EXACT", True, True))
        self.assertEqual(record["model_basis"], "SRC0#0.diffraction")

    def test_a_declined_curve_with_an_array_gives_no_values(self):
        self.assertIn("SRC0#0.geometric_mtf_tangential", self.sonnar["arrays"])  # the Engine keeps the estimate
        record = ir.curve(self.sonnar, "SRC0#0", "geometric_mtf", "tangential")
        self.assertEqual((record["usable"], record["values"], record["status"], record["reason"], record["value_kind"]),
                         (False, None, "OUT_OF_MODEL_SCOPE", "GEOMETRIC_REGIME_NOT_MET", "OUTSIDE_MODEL"))
        self.assertTrue(ir.curve(self.sonnar, "SRC1#0", "geometric_mtf", "tangential")["usable"])

    def test_unconverged_values_are_kept_and_flagged(self):
        record = ir.curve(self.edge, "A#0", "mtf", "sagittal")
        self.assertEqual((record["status"], record["value_kind"], record["converged"]),
                         ("NOT_CONVERGED", "QUANTITY_UNKNOWN", False))
        self.assertEqual(record["values"], [0.9962, 0.9, 0.78, 0.5])
        self.assertEqual(record["convergence_difference"], 0.061)

    def test_beyond_nyquist_value_absent_and_stand_in_pupil(self):
        limit = ir.curve(self.edge, "A#0", "diffraction_limited_mtf", "tangential")
        self.assertEqual(ir.value_status(limit, 2), (0.9, "KNOWN"))
        self.assertEqual(ir.value_status(limit, 3), (None, "BEYOND_NYQUIST"))
        absent = ir.curve(self.edge, "A#0", "geometric_mtf", "sagittal")
        self.assertEqual((absent["status"], absent["reason"], ir.value_status(absent, 0)),
                         ("ABSENT", "ABSENT", (None, "ABSENT")))
        stand_in = ir.curve(self.edge, "B#0", "mtf", "tangential")
        self.assertEqual((stand_in["usable"], stand_in["status"], stand_in["reason"], stand_in["value_kind"]),
                         (False, "UNKNOWN", "PUPIL_NOT_BOUNDED", "QUANTITY_UNKNOWN"))

    def test_value_at_reads_grid_points_only(self):
        record = ir.curve(self.sonnar, "SRC2#0", "mtf", "sagittal")
        self.assertEqual(ir.value_at(record, 40.0), (record["values"][3], "KNOWN"))
        self.assertEqual(ir.value_at(record, 40.0 + 1e-12), (record["values"][3], "KNOWN"))
        self.assertEqual(ir.value_at(record, 45.0), (None, "FREQUENCY_NOT_SAMPLED"))
        declined = ir.curve(self.sonnar, "SRC2#0", "geometric_mtf", "sagittal")
        self.assertEqual(ir.value_at(declined, 45.0), (None, "OUT_OF_MODEL_SCOPE"))

    def test_curves_order_and_selection(self):
        records = ir.curves(self.sonnar, kinds=("mtf",), line_ids=["SRC1#0"])
        self.assertEqual([(r["line_id"], r["kind"], r["direction"]) for r in records],
                         [("SRC1#0", "mtf", "tangential"), ("SRC1#0", "mtf", "sagittal")])
        self.assertEqual(len(ir.curves(self.sonnar)), 3 * 3 * 2)
        with self.assertRaises(ValueError):
            ir.curve(self.sonnar, "SRC0#0", "psf", "tangential")

    def test_scalars(self):
        strehl = ir.scalar(self.edge, "A#0", "strehl_ratio")
        self.assertEqual((strehl["value"], strehl["value_kind"]), (0.93, "EXACT"))
        missing = ir.scalar(self.edge, "A#0", "encircled_radius_80")
        self.assertEqual((missing["value"], missing["reason"]), (None, "ABSENT"))
        field = ir.scalar(self.edge, "B#1", "field")
        self.assertEqual((field["value"], field["state"], field["reason"]), (None, "UNKNOWN", "NO_ARRIVAL"))


if __name__ == "__main__":
    unittest.main()
