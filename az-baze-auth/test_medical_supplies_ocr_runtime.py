"""Bounded OCR runtime, safe failure reporting and lossless raster regressions.

All documents are synthetic. No private PDF or production service is used.
"""
import copy
import errno
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

import medical_supplies_ocr as ocr
import medical_supplies_ocr_image as image
import test_medical_supplies_ocr as fixtures
from test_medical_supplies_ocr_image import synthetic_image


PRIVATE = "PRIVATE_DOCUMENT_TEXT_TOKEN_73"


def raster_pdf(rgb, dpi=336.5001):
    """A coloured scan whose physical size exercises fractional raster DPI."""
    height, width, channels = rgb.shape
    assert channels == 3
    page_width = 3508 * 72 / dpi
    page_height = page_width * height / width
    writer = PdfWriter()
    page = writer.add_blank_page(width=page_width, height=page_height)
    pixels = DecodedStreamObject()
    pixels.set_data(rgb.tobytes())
    pixels.update({NameObject("/Type"): NameObject("/XObject"),
                   NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): NumberObject(width),
                   NameObject("/Height"): NumberObject(height),
                   NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                   NameObject("/BitsPerComponent"): NumberObject(8)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
        NameObject("/Im0"): writer._add_object(pixels.flate_encode())})})
    stream = DecodedStreamObject()
    stream.set_data(f"q {page_width:.10f} 0 0 {page_height:.10f} 0 0 cm /Im0 Do Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def raster_metadata(raw):
    """Read only lossless output DPI/compression tags; no imaging dependency."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        offset = 8
        while offset + 12 <= len(raw):
            size = struct.unpack_from(">I", raw, offset)[0]
            kind = raw[offset + 4:offset + 8]
            if kind == b"pHYs":
                x, y, units = struct.unpack_from(">IIB", raw, offset + 8)
                assert units == 1
                return {"dpi": (x * .0254, y * .0254), "pixels_per_meter": (x, y), "format": "PNG"}
            offset += size + 12
        raise AssertionError("PNG resolution is absent")
    order = "<" if raw[:2] == b"II" else ">"
    assert raw[:2] in (b"II", b"MM") and struct.unpack_from(order + "H", raw, 2)[0] == 42
    offset = struct.unpack_from(order + "I", raw, 4)[0]
    count = struct.unpack_from(order + "H", raw, offset)[0]
    values = {}
    for index in range(count):
        entry = offset + 2 + index * 12
        tag, kind, length = struct.unpack_from(order + "HHI", raw, entry)
        if tag in (282, 283):
            assert kind == 5 and length == 1
            pointer = struct.unpack_from(order + "I", raw, entry + 8)[0]
            numerator, denominator = struct.unpack_from(order + "II", raw, pointer)
            values[tag] = numerator / denominator
        elif tag in (259, 296):
            assert kind == 3 and length == 1
            values[tag] = struct.unpack_from(order + "H", raw, entry + 8)[0]
    assert values[296] in (2, 3)
    factor = 1 if values[296] == 2 else 2.54
    return {"dpi": (values[282] * factor, values[283] * factor),
            "format": "TIFF", "compression": values[259]}


def tiff_resolution_fixture(x, y=None, *, endian="<", unit=2, zero_denominator=False):
    """Classic TIFF resolution records; the helper never needs raster pixels."""
    y = x if y is None else y
    header = (b"II" if endian == "<" else b"MM") + struct.pack(endian + "HI", 42, 8)
    rationals = []
    for value in (x, y):
        # LibTIFF receives resolution as float32 before emitting its rational.
        stored = struct.unpack("f", struct.pack("f", value))[0]
        numerator, denominator = stored.as_integer_ratio()
        rationals.append(struct.pack(endian + "II", numerator,
                                     0 if zero_denominator else denominator))
    start = 8 + 2 + 3 * 12 + 4
    entries = [struct.pack(endian + "HHII", 282, 5, 1, start),
               struct.pack(endian + "HHII", 283, 5, 1, start + 8),
               struct.pack(endian + "HHI", 296, 3, 1) + struct.pack(endian + "H", unit) + b"\0\0"]
    return header + struct.pack(endian + "H", 3) + b"".join(entries) + b"\0" * 4 + b"".join(rationals)


class RasterResolutionTests(unittest.TestCase):
    def result(self, raw):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "page.tif"
            path.write_bytes(raw)
            return ocr._tiff_png_resolution(path)

    def test_dpi_preserves_legacy_png_rounding_in_both_tiff_byte_orders(self):
        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                self.assertEqual(self.result(tiff_resolution_fixture(300, endian=endian)), (11811, 11811))
                self.assertEqual(self.result(tiff_resolution_fixture(150.501, endian=endian)), (5925, 5925))
                self.assertEqual(self.result(tiff_resolution_fixture(336.5001, endian=endian)), (13248, 13248))
                self.assertEqual(self.result(tiff_resolution_fixture(300, 301, endian=endian)), (11811, 11850))

    def test_uncertain_png_rounding_or_unsupported_resolution_requires_fallback(self):
        for raw in (tiff_resolution_fixture(150.5204),
                    tiff_resolution_fixture(50), tiff_resolution_fixture(2500),
                    tiff_resolution_fixture(300, unit=3)):
            with self.subTest(raw=raw[:8]):
                self.assertIsNone(self.result(raw))

    def test_invalid_or_unbounded_resolution_records_require_fallback(self):
        for raw in (b"", b"not a TIFF", tiff_resolution_fixture(300)[:-3],
                    tiff_resolution_fixture(300, zero_denominator=True),
                    b"II" + struct.pack("<HI", 42, 0x7fffffff),
                    b"II" + struct.pack("<HIH", 42, 8, 0xffff)):
            with self.subTest(raw=raw[:8]):
                self.assertIsNone(self.result(raw))


class RuntimeCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "page.tsv"
        self.argv = ["tesseract", str(self.root / "page.tif"), "stdout", "-l", "rus+eng", "--psm", "3", "tsv"]

    def test_child_keeps_existing_resource_environment_and_no_shell(self):
        with patch.object(ocr.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(ocr.subprocess, "run") as run:
            ocr._command(self.argv, self.root, self.output, 12, ocr.MAX_TSV)
        arguments, options = run.call_args
        self.assertEqual(arguments[0], ["/usr/bin/prlimit", "--as=805306368", "--fsize=4194304",
            "--nofile=128", "--cpu=14", "--", "/usr/bin/tesseract", *self.argv[1:]])
        self.assertEqual(options["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8",
            "HOME": str(self.root), "TMPDIR": str(self.root), "OMP_THREAD_LIMIT": "1"})
        self.assertEqual(options["timeout"], 12)
        self.assertEqual(options["cwd"], self.root)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertTrue(options["check"])
        self.assertTrue(options["close_fds"])
        self.assertFalse(options.get("shell", False))

    def test_distinct_failure_causes_have_safe_messages_and_logs(self):
        cases = [
            (subprocess.TimeoutExpired([PRIVATE], 12, output=PRIVATE, stderr=PRIVATE), "TIMEOUT"),
            (subprocess.CalledProcessError(17, [PRIVATE], output=PRIVATE, stderr=PRIVATE), "TOOL_EXIT"),
            (PermissionError(errno.EACCES, PRIVATE), "OS_ERROR"),
        ]
        for cause, expected in cases:
            with self.subTest(reason=expected), \
                 patch.object(ocr.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
                 patch.object(ocr.subprocess, "run", side_effect=cause), \
                 self.assertLogs(ocr.__name__, "WARNING") as logs, \
                 self.assertRaises(ocr.OCRUnavailable) as caught:
                ocr._command(self.argv, self.root, self.output, 12, ocr.MAX_TSV)
            self.assertEqual((caught.exception.stage, caught.exception.reason), ("INITIAL_OCR", expected))
            self.assertIs(caught.exception.__cause__, cause)
            self.assertNotIn(PRIVATE, str(caught.exception) + "\n".join(logs.output))
            self.assertNotIn(str(self.root), "\n".join(logs.output))
            self.assertTrue(any("stage=INITIAL_OCR" in line and "reason=" + expected in line for line in logs.output))

    def test_missing_tool_is_reported_before_child_execution(self):
        with patch.object(ocr.shutil, "which", return_value=None), \
             patch.object(ocr.subprocess, "run") as run, \
             self.assertLogs(ocr.__name__, "WARNING"), \
             self.assertRaises(ocr.OCRUnavailable) as caught:
            ocr._command(["pdfinfo", PRIVATE], self.root, self.output, 3, 65536)
        run.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertEqual((caught.exception.stage, caught.exception.reason), ("PDF_INFO", "TOOL_UNAVAILABLE"))
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_oversized_output_is_a_separate_limit_failure(self):
        def oversized(*args, **kwargs):
            kwargs["stdout"].write(b"x" * 33)
        with patch.object(ocr.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(ocr.subprocess, "run", side_effect=oversized), \
             self.assertLogs(ocr.__name__, "WARNING"), \
             self.assertRaises(ocr.OCRUnavailable) as caught:
            ocr._command(self.argv, self.root, self.output, 12, 32)
        self.assertEqual((caught.exception.stage, caught.exception.reason), ("INITIAL_OCR", "OUTPUT_LIMIT"))


class PageBudgetTests(unittest.TestCase):
    def run_page(self, remaining, resolution=(11811, 11811), fast_png_ok=True, encode_seconds=0,
                 render_failure=None, observed_calls=None):
        clock = [100.0]
        calls = [] if observed_calls is None else observed_calls
        def command(argv, directory, output, seconds, maximum):
            calls.append((list(argv), seconds, maximum))
            if argv[0] == "pdfinfo":
                output.write_text("Pages: 1\n")
                clock[0] = 101.0
            elif argv[0] == "pdftoppm":
                if "-tiff" in argv:
                    self.assertEqual(argv[7:10], ["-tiff", "-tiffcompression", "none"])
                    suffix = ".tif"
                    clock[0] = 125.0 - remaining
                    if render_failure:
                        raise ocr.OCRUnavailable("Synthetic renderer failure", stage="PDF_RENDER",
                                                 reason=render_failure)
                else:
                    self.assertIn("-png", argv)
                    suffix = ".png"
                    clock[0] += 1
                Path(argv[-1]).with_suffix(suffix).write_bytes(b"synthetic image placeholder")
            else:
                self.assertEqual(Path(argv[1]).suffix, ".png")
                self.assertNotEqual(Path(argv[1]).read_bytes(), b"partial PNG")
                output.write_text(fixtures.tsv_fixture())
        def encode(source, destination, resolved):
            self.assertEqual(resolved, resolution)
            self.assertEqual(source.suffix, ".tif")
            self.assertEqual(destination.suffix, ".png")
            clock[0] += encode_seconds
            destination.write_bytes(b"synthetic PNG placeholder" if fast_png_ok else b"partial PNG")
            return fast_png_ok
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(ocr.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(ocr, "_command", side_effect=command), \
             patch.object(ocr, "_tiff_png_resolution", return_value=resolution), \
             patch.object(ocr, "_fast_png", side_effect=encode), \
             patch.object(image, "enhance_page", side_effect=lambda path, directory, run, parsed: parsed):
            result = ocr.recognize_pdf_page(b"%PDF-synthetic", 1, Path(temporary))
        return result, calls

    def test_initial_ocr_cap_uses_saved_time_but_is_clipped_by_page_deadline(self):
        for remaining, expected in [(20, 12), (4, 4)]:
            with self.subTest(remaining=remaining):
                result, calls = self.run_page(remaining)
                self.assertEqual([call[1] for call in calls], [3, 6, expected])
                self.assertEqual(calls[1][2], 64 * 1024 * 1024)
                self.assertEqual(calls[1][0][5:7], ["-scale-to", "3508"])
                self.assertEqual(result[0], 1)
                self.assertEqual(result[1]["recognition_version"], 2)

    def test_exhausted_total_budget_starts_no_initial_ocr_process(self):
        with self.assertRaises(ocr.OCRUnavailable) as caught:
            self.run_page(0)
        self.assertEqual((caught.exception.stage, caught.exception.reason), ("PAGE", "BUDGET_EXHAUSTED"))

    def test_uncertain_resolution_uses_original_png_within_the_existing_page_budget(self):
        result, calls = self.run_page(remaining=20, resolution=None)
        self.assertEqual([call[0][0] for call in calls], ["pdfinfo", "pdftoppm", "pdftoppm", "tesseract"])
        self.assertIn("-tiff", calls[1][0])
        self.assertIn("-png", calls[2][0])
        self.assertNotIn("--dpi", calls[3][0])
        self.assertEqual([call[1] for call in calls], [3, 6, 6, 12])
        self.assertEqual(result[0], 1)

    def test_encoding_failure_uses_original_png_and_does_not_accept_partial_pixels(self):
        result, calls = self.run_page(remaining=20, fast_png_ok=False)
        self.assertEqual([call[0][0] for call in calls], ["pdfinfo", "pdftoppm", "pdftoppm", "tesseract"])
        self.assertIn("-png", calls[2][0])
        self.assertEqual(Path(calls[3][0][1]).suffix, ".png")
        self.assertEqual(result[0], 1)

    def test_encoding_time_consumes_the_same_total_page_budget(self):
        _, calls = self.run_page(remaining=4, encode_seconds=3)
        self.assertEqual(calls[-1][1], 1)
        with self.assertRaises(ocr.OCRUnavailable) as caught:
            self.run_page(remaining=4, encode_seconds=4)
        self.assertEqual(caught.exception.reason, "BUDGET_EXHAUSTED")

    def test_unsupported_tiff_renderer_uses_original_png(self):
        result, calls = self.run_page(remaining=20, render_failure="TOOL_EXIT")
        self.assertEqual([call[0][0] for call in calls], ["pdfinfo", "pdftoppm", "pdftoppm", "tesseract"])
        self.assertIn("-png", calls[2][0])
        self.assertEqual([call[1] for call in calls], [3, 6, 6, 12])
        self.assertEqual(result[0], 1)

    def test_renderer_timeout_or_unavailability_starts_no_fallback_or_ocr(self):
        for reason in ("TIMEOUT", "TOOL_UNAVAILABLE", "OS_ERROR", "OUTPUT_LIMIT"):
            calls = []
            with self.subTest(reason=reason), self.assertRaises(ocr.OCRUnavailable) as caught:
                self.run_page(remaining=20, render_failure=reason, observed_calls=calls)
            self.assertEqual([call[0][0] for call in calls], ["pdfinfo", "pdftoppm"])
            self.assertEqual((caught.exception.stage, caught.exception.reason), ("PDF_RENDER", reason))


class FailureRouteTests(unittest.TestCase):
    setUp = fixtures.OCRRouteTests.setUp
    client = fixtures.OCRRouteTests.client
    upload = fixtures.OCRRouteTests.upload
    get_doc = fixtures.OCRRouteTests.get_doc

    def test_typed_runtime_failures_preserve_saved_document_history_other_page_and_stock(self):
        self.assertEqual(self.upload().status_code, 303)
        saved = fixtures.data_fixture()
        saved.update(rows=[dict(saved["candidates"][0], name="Saved manual goods", value="91.01")],
                     header={"counterparty": "Saved supplier"}, checked=True, recognition_version=1)
        state = {"page_count": 2, "pages": {"1": saved, "2": copy.deepcopy(saved)}}
        self.conn.execute("UPDATE med_supply_documents SET fields_json=?,version=2,status='reviewed' WHERE id=1",
                          (json.dumps({ocr.KEY: state, "outside_ocr": PRIVATE}),))
        self.conn.commit()
        before = list(self.conn.iterdump())
        cases = [
            (subprocess.TimeoutExpired([PRIVATE], 12, output=PRIVATE, stderr=PRIVATE), "TIMEOUT"),
            (subprocess.CalledProcessError(17, [PRIVATE], output=PRIVATE, stderr=PRIVATE), "TOOL_EXIT"),
            (PermissionError(errno.EACCES, PRIVATE), "OS_ERROR"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def failed_engine(*args):
                ocr._command(["tesseract", str(root / "page.tif"), "stdout", "-l", "rus+eng", "tsv"],
                             root, root / "page.tsv", 12, ocr.MAX_TSV)
            for cause, reason in cases:
                with self.subTest(reason=reason), \
                     patch.object(ocr, "recognize_pdf_page", side_effect=failed_engine), \
                     patch.object(ocr.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
                     patch.object(ocr.subprocess, "run", side_effect=cause), \
                     self.assertLogs(ocr.__name__, "WARNING") as logs:
                    response = self.client(2).post(fixtures.PREFIX + "/page", data={
                        "csrf": "token", "version": "2", "page": "1", "reread": "1"})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json["ocr_stage"], "INITIAL_OCR")
                self.assertEqual(response.json["ocr_reason"], reason)
                self.assertNotIn(PRIVATE, response.get_data(as_text=True) + "\n".join(logs.output))
                self.assertEqual(list(self.conn.iterdump()), before)

    def test_existing_unclassified_ocr_error_remains_a_compatible_503(self):
        self.assertEqual(self.upload().status_code, 303)
        before = list(self.conn.iterdump())
        with patch.object(ocr, "recognize_pdf_page", side_effect=ocr.OCRUnavailable("Исходный PDF недоступен.")):
            response = self.client(2).post(fixtures.PREFIX + "/page", data={"csrf": "token", "version": "1", "page": "1"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"error": "Исходный PDF недоступен."})
        self.assertEqual(list(self.conn.iterdump()), before)


@unittest.skipUnless(os.environ.get("AZ_TEST_REAL_OCR") == "1", "Real raster parity is required by OCR CI")
class RealRasterParityTests(unittest.TestCase):
    def test_fast_png_preserves_colour_pixels_dpi_all_ocr_evidence_and_money(self):
        gray = synthetic_image(with_price=True)
        rgb = np.repeat(gray[:, :, None], 3, axis=2)
        rgb[gray > 245] = [255, 250, 245]
        source = raster_pdf(rgb)
        actual = ocr._command
        models = os.environ.get("AZ_OCR_TESSDATA") or os.environ.get("TESSDATA_PREFIX")

        def recognize(format_name):
            rasters, tsv = {"render_formats": []}, {}
            def command(argv, directory, output, seconds, maximum):
                argv = list(argv)
                if argv[0] == "tesseract":
                    if Path(argv[1]).stem == "page":
                        path = Path(argv[1])
                        rasters["bytes"] = path.read_bytes()
                        rasters["pixels"] = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                        rasters["grayscale"] = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                        self.assertNotIn("--dpi", argv)
                        current = rasters["metadata"] = raster_metadata(rasters["bytes"])
                        rasters["effective_dpi"] = (int(argv[argv.index("--dpi") + 1]) if "--dpi" in argv
                            else int(current["pixels_per_meter"][1] / 39.37 + .5)
                            if current["format"] == "PNG" else int(current["dpi"][1] + .5))
                    if models:
                        argv[3:3] = ["--tessdata-dir", models]
                actual(argv, directory, output, seconds, maximum)
                if argv[0] == "pdftoppm":
                    output_format = "PNG" if "-png" in argv else "TIFF"
                    rasters["render_formats"].append(output_format)
                    path = Path(argv[-1]).with_suffix(".png" if output_format == "PNG" else ".tif")
                    if output_format == "TIFF":
                        raw = path.read_bytes()
                        rasters["intermediate"] = {"bytes": len(raw), "metadata": raster_metadata(raw)}
                elif argv[0] == "tesseract":
                    tsv[output.name] = output.read_bytes()
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                if format_name == "TIFF":
                    with patch.object(ocr, "_command", side_effect=command):
                        result = ocr.recognize_pdf_page(source, 1, directory)
                else:
                    # Independent baseline: the installed pre-change PNG steps,
                    # including the original 8-second primary OCR cap and the
                    # same initial evidence consumed by the unchanged enhancer.
                    deadline = time.monotonic() + 25
                    def bounded(argv, root, output, seconds, maximum):
                        remaining = int(deadline - time.monotonic())
                        self.assertGreaterEqual(remaining, 1)
                        command(argv, root, output, min(seconds, remaining), maximum)
                    pdf = directory / "source.pdf"
                    pdf.write_bytes(source)
                    bounded(["pdfinfo", str(pdf)], directory, directory / "info.txt", 3, 65536)
                    prefix = directory / "page"
                    bounded(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", "3508", "-png",
                             "-singlefile", str(pdf), str(prefix)], directory, directory / "render.log",
                            6, 64 * 1024 * 1024)
                    initial = directory / "page.tsv"
                    bounded(["tesseract", str(prefix.with_suffix(".png")), "stdout", "-l", "rus+eng",
                             "--psm", "3", "tsv"], directory, initial, 8, ocr.MAX_TSV)
                    parsed = ocr.parse_tsv(initial.read_text())
                    parsed = image.enhance_page(prefix.with_suffix(".png"), directory, bounded, parsed)
                    parsed.setdefault("metadata_first_pass", [])
                    parsed["recognition_version"] = ocr.RECOGNITION_VERSION
                    result = (1, parsed)
            return result, rasters, tsv

        png, tiff = recognize("PNG"), recognize("TIFF")
        self.assertEqual(png[1]["render_formats"], ["PNG"])
        self.assertEqual(tiff[1]["render_formats"], ["TIFF"])
        self.assertEqual(tiff[1]["intermediate"]["metadata"]["compression"], 1)
        self.assertLess(tiff[1]["intermediate"]["bytes"], 64 * 1024 * 1024)
        self.assertLess(len(tiff[1]["bytes"]), 64 * 1024 * 1024)
        self.assertTrue(np.array_equal(png[1]["pixels"], tiff[1]["pixels"]))
        self.assertTrue(np.array_equal(png[1]["grayscale"], tiff[1]["grayscale"]))
        self.assertEqual(png[1]["metadata"], tiff[1]["metadata"])
        self.assertEqual(png[1]["effective_dpi"], tiff[1]["effective_dpi"])
        self.assertIn("page.tsv", png[2])
        self.assertIn("clean-page.tsv", png[2])
        self.assertIn("table-cells.tsv", png[2])
        self.assertIn("table-numbers.tsv", png[2])
        self.assertEqual(png[2], tiff[2])
        self.assertEqual(png[0], tiff[0])
        self.assertEqual(tiff[0][0], 1)
        draft = tiff[0][1]
        self.assertFalse(draft.get("recognition_retryable"))
        self.assertEqual(draft["printed_total"], "52.84")
        self.assertEqual([row["value"] for row in draft["candidates"]], ["12.34", "40.50"])
        self.assertTrue(draft["metadata_first_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
