"""Smoke tests for SCHEMADRIFT. Standard library only, no network."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemadrift import (
    TOOL_NAME,
    TOOL_VERSION,
    infer_schema,
    diff_schemas,
    check_contract,
    load_records,
)
from schemadrift.cli import main

DEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demos", "01-basic")


class TestInfer(unittest.TestCase):
    def test_basic_inference(self):
        recs = [
            {"id": 1, "name": "a", "score": 1.5},
            {"id": 2, "name": "b", "score": 2.0},
            {"id": 3, "name": None, "score": 3.0},
        ]
        s = infer_schema(recs)
        self.assertEqual(s.row_count, 3)
        self.assertEqual(s.fields["id"].dominant_type, "int")
        self.assertEqual(s.fields["score"].dominant_type, "float")
        self.assertTrue(s.fields["name"].nullable(3))
        self.assertFalse(s.fields["id"].nullable(3))
        self.assertEqual(s.fields["id"].distinct_sample, 3)

    def test_csv_type_recovery(self):
        recs = load_records("x.csv", text="id,active\n1,true\n2,false\n")
        s = infer_schema(recs)
        self.assertEqual(s.fields["id"].dominant_type, "int")
        self.assertEqual(s.fields["active"].dominant_type, "bool")


class TestDrift(unittest.TestCase):
    def test_breaking_drift(self):
        old = infer_schema(load_records(os.path.join(DEMO, "users_baseline.json")))
        new = infer_schema(load_records(os.path.join(DEMO, "users_current.json")))
        rep = diff_schemas(old, new)
        d = rep.to_dict()
        self.assertTrue(d["has_breaking"])
        removed = {r["field"] for r in d["removed"]}
        self.assertIn("country", removed)
        added = {a["field"] for a in d["added"]}
        self.assertIn("is_premium", added)
        tc = {t["field"]: t for t in d["type_changed"]}
        self.assertIn("signup_ts", tc)
        self.assertFalse(tc["signup_ts"]["widening"])

    def test_no_drift_identical(self):
        recs = [{"a": 1, "b": "x"}]
        rep = diff_schemas(infer_schema(recs), infer_schema(recs))
        self.assertFalse(rep.has_drift)
        self.assertEqual(rep.breaking, [])

    def test_widening_not_breaking(self):
        old = infer_schema([{"v": 1}, {"v": 2}])
        new = infer_schema([{"v": 1.5}, {"v": 2.5}])
        rep = diff_schemas(old, new)
        self.assertTrue(rep.has_drift)
        self.assertEqual(rep.breaking, [])
        self.assertTrue(rep.type_changed[0]["widening"])


class TestContract(unittest.TestCase):
    def test_passes_clean_data(self):
        contract = {"fields": {"id": {"type": "int", "required": True, "unique": True}}}
        res = check_contract([{"id": 1}, {"id": 2}], contract)
        self.assertTrue(res.passed)
        self.assertEqual(res.violations, [])

    def test_catches_violations(self):
        contract = {
            "allow_extra_fields": False,
            "fields": {
                "id": {"type": "int", "required": True, "unique": True},
                "email": {"type": "string", "required": True, "regex": "@"},
                "age": {"type": "int", "min": 0, "max": 130},
                "status": {"type": "string", "enum": ["active", "closed"]},
            },
        }
        recs = [
            {"id": 1, "email": "ok@x.com", "age": 40, "status": "active"},
            {"id": 1, "email": "bad", "age": 999, "status": "weird", "extra": 1},
            {"email": "missing-id@x.com", "age": 30, "status": "closed"},
        ]
        res = check_contract(recs, contract)
        self.assertFalse(res.passed)
        rules = {v["rule"] for v in res.violations}
        self.assertIn("unique", rules)
        self.assertIn("regex", rules)
        self.assertIn("max", rules)
        self.assertIn("enum", rules)
        self.assertIn("required", rules)
        self.assertIn("unexpected_field", rules)


class TestCli(unittest.TestCase):
    def test_version_constants(self):
        self.assertEqual(TOOL_NAME, "schemadrift")
        self.assertTrue(TOOL_VERSION)

    def test_drift_exit_code_breaking(self):
        code = main([
            "--format", "json", "drift",
            os.path.join(DEMO, "users_baseline.json"),
            os.path.join(DEMO, "users_current.json"),
        ])
        self.assertEqual(code, 3)

    def test_infer_exit_zero(self):
        code = main(["infer", os.path.join(DEMO, "users_current.json")])
        self.assertEqual(code, 0)

    def test_contract_exit_two_on_violation(self):
        code = main([
            "contract",
            os.path.join(DEMO, "users_current.json"),
            "--contract", os.path.join(DEMO, "users_contract.json"),
        ])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
