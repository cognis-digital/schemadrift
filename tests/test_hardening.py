"""Hardening tests: error paths, edge cases, and bad-input handling."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemadrift.cli import main
from schemadrift.core import (
    check_contract,
    diff_schemas,
    infer_schema,
    load_records,
)


# ---------------------------------------------------------------------------
# load_records edge cases
# ---------------------------------------------------------------------------

class TestLoadRecordsEdgeCases(unittest.TestCase):
    def _tmp(self, suffix: str, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return path

    def test_unsupported_extension_raises(self):
        path = self._tmp(".xml", "<data/>")
        try:
            with self.assertRaises(ValueError) as ctx:
                load_records(path)
            self.assertIn("unsupported file type", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_malformed_ndjson_includes_line_number(self):
        path = self._tmp(".ndjson", '{"id": 1}\n{bad\n{"id": 3}\n')
        try:
            with self.assertRaises(ValueError) as ctx:
                load_records(path)
            msg = str(ctx.exception)
            self.assertIn("line 2", msg)
        finally:
            os.unlink(path)

    def test_empty_json_array_returns_empty_list(self):
        recs = load_records("x.json", text="[]")
        self.assertEqual(recs, [])

    def test_json_object_root_not_array_raises(self):
        path = self._tmp(".json", '{"key": "value"}')
        try:
            # A plain object is loaded as NDJSON (single-line), yielding a dict as
            # the sole record — infer_schema accepts dicts, so the load succeeds.
            # But a top-level array containing non-dicts should raise.
            path2 = self._tmp(".json", '[1, 2, 3]')
            recs = load_records(path2)
            with self.assertRaises(ValueError):
                infer_schema(recs)
        finally:
            os.unlink(path)
            os.unlink(path2)

    def test_csv_header_only_returns_empty(self):
        recs = load_records("x.csv", text="id,name\n")
        self.assertEqual(recs, [])


# ---------------------------------------------------------------------------
# infer_schema edge cases
# ---------------------------------------------------------------------------

class TestInferSchemaEdgeCases(unittest.TestCase):
    def test_empty_records_returns_zero_row_schema(self):
        s = infer_schema([])
        self.assertEqual(s.row_count, 0)
        self.assertEqual(s.fields, {})

    def test_non_dict_record_raises(self):
        with self.assertRaises(ValueError) as ctx:
            infer_schema([{"id": 1}, "not-a-dict"])
        self.assertIn("object/dict", str(ctx.exception))

    def test_all_null_field_dominant_type_is_null(self):
        recs = [{"v": None}, {"v": None}]
        s = infer_schema(recs)
        self.assertEqual(s.fields["v"].dominant_type, "null")


# ---------------------------------------------------------------------------
# check_contract validation
# ---------------------------------------------------------------------------

class TestCheckContractValidation(unittest.TestCase):
    def test_invalid_regex_raises_value_error(self):
        contract = {"fields": {"email": {"type": "string", "regex": "[unclosed"}}}
        with self.assertRaises(ValueError) as ctx:
            check_contract([{"email": "x@y.com"}], contract)
        self.assertIn("invalid regex", str(ctx.exception).lower())

    def test_invalid_min_type_raises_value_error(self):
        contract = {"fields": {"age": {"type": "int", "min": "not-a-number"}}}
        with self.assertRaises(ValueError) as ctx:
            check_contract([{"age": 25}], contract)
        self.assertIn("min", str(ctx.exception))

    def test_invalid_max_type_raises_value_error(self):
        contract = {"fields": {"age": {"type": "int", "max": "oops"}}}
        with self.assertRaises(ValueError) as ctx:
            check_contract([{"age": 25}], contract)
        self.assertIn("max", str(ctx.exception))

    def test_non_dict_contract_raises(self):
        with self.assertRaises(ValueError):
            check_contract([{"id": 1}], "not-a-dict")  # type: ignore[arg-type]

    def test_empty_records_passes_contract(self):
        contract = {"fields": {"id": {"type": "int", "required": True}}}
        res = check_contract([], contract)
        self.assertTrue(res.passed)
        self.assertEqual(res.checked_rows, 0)


# ---------------------------------------------------------------------------
# CLI error handling
# ---------------------------------------------------------------------------

class TestCliErrorHandling(unittest.TestCase):
    def test_missing_data_file_exits_1(self):
        code = main(["infer", "/nonexistent/path/file.json"])
        self.assertEqual(code, 1)

    def test_missing_contract_file_exits_1(self):
        fd, data_path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b'[{"id": 1}]')
        os.close(fd)
        try:
            code = main(["contract", data_path, "--contract", "/nonexistent/contract.json"])
            self.assertEqual(code, 1)
        finally:
            os.unlink(data_path)

    def test_malformed_data_json_exits_1(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b"{bad json")
        os.close(fd)
        try:
            code = main(["infer", path])
            self.assertEqual(code, 1)
        finally:
            os.unlink(path)

    def test_unsupported_extension_exits_1(self):
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.write(fd, b"<data/>")
        os.close(fd)
        try:
            code = main(["infer", path])
            self.assertEqual(code, 1)
        finally:
            os.unlink(path)

    def test_directory_as_input_exits_1(self):
        tmpdir = tempfile.mkdtemp()
        # rename to have .json extension so the extension check passes but open fails
        json_dir = tmpdir + ".json"
        os.rename(tmpdir, json_dir)
        try:
            code = main(["infer", json_dir])
            self.assertEqual(code, 1)
        finally:
            os.rmdir(json_dir)

    def test_empty_input_json_exits_0(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b"[]")
        os.close(fd)
        try:
            code = main(["infer", path])
            self.assertEqual(code, 0)
        finally:
            os.unlink(path)

    def test_malformed_contract_json_exits_1(self):
        fd, data_path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b'[{"id": 1}]')
        os.close(fd)
        fd2, contract_path = tempfile.mkstemp(suffix=".json")
        os.write(fd2, b"{invalid}")
        os.close(fd2)
        try:
            code = main(["contract", data_path, "--contract", contract_path])
            self.assertEqual(code, 1)
        finally:
            os.unlink(data_path)
            os.unlink(contract_path)

    def test_drift_missing_baseline_exits_1(self):
        fd, current = tempfile.mkstemp(suffix=".json")
        os.write(fd, b'[{"id": 1}]')
        os.close(fd)
        try:
            code = main(["drift", "/nonexistent/baseline.json", current])
            self.assertEqual(code, 1)
        finally:
            os.unlink(current)


# ---------------------------------------------------------------------------
# diff_schemas edge cases
# ---------------------------------------------------------------------------

class TestDiffSchemasEdgeCases(unittest.TestCase):
    def test_empty_vs_empty_no_drift(self):
        rep = diff_schemas(infer_schema([]), infer_schema([]))
        self.assertFalse(rep.has_drift)

    def test_all_fields_added(self):
        old = infer_schema([])
        new = infer_schema([{"x": 1, "y": "a"}])
        rep = diff_schemas(old, new)
        self.assertTrue(rep.has_drift)
        added = {a["field"] for a in rep.added}
        self.assertIn("x", added)
        self.assertIn("y", added)

    def test_all_fields_removed_breaking(self):
        old = infer_schema([{"x": 1}])
        new = infer_schema([])
        rep = diff_schemas(old, new)
        self.assertTrue(rep.has_drift)
        self.assertTrue(bool(rep.breaking))


if __name__ == "__main__":
    unittest.main()
