"""Core engine for SCHEMADRIFT.

Three capabilities:
  1. infer_schema(records)  -> Schema   (data-driven schema inference)
  2. diff_schemas(a, b)     -> DriftReport  (breaking vs additive drift)
  3. check_contract(records, contract) -> ContractResult  (enforce a contract)

No third-party deps.
"""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Ordering used to pick a single dominant type and to judge type widening.
# Only value-preserving numeric widening (int->float) is non-breaking, matching
# Avro/BigQuery-style schema compatibility. A numeric/bool field turning into a
# string is a contract break for typed consumers, not a widening.
_TYPE_RANK = {"null": 0, "bool": 1, "int": 2, "float": 3, "string": 4, "object": 5, "array": 6}
_WIDENS = {
    ("int", "float"),
}


def _classify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        # Try to recover a richer type from a string cell (CSV has no types).
        s = value.strip()
        if s == "":
            return "null"
        low = s.lower()
        if low in ("true", "false"):
            return "bool"
        try:
            int(s)
            return "int"
        except ValueError:
            pass
        try:
            f = float(s)
            if not math.isnan(f):
                return "float"
        except ValueError:
            pass
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "string"


@dataclass
class FieldStats:
    name: str
    types: Dict[str, int] = field(default_factory=dict)
    present: int = 0
    nulls: int = 0
    distinct_sample: int = 0

    @property
    def dominant_type(self) -> str:
        non_null = {t: c for t, c in self.types.items() if t != "null"}
        if not non_null:
            return "null"
        return max(non_null.items(), key=lambda kv: (kv[1], _TYPE_RANK.get(kv[0], 0)))[0]

    def nullable(self, total: int) -> bool:
        return self.nulls > 0 or self.present < total

    def to_dict(self, total: int) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.dominant_type,
            "types_seen": self.types,
            "present": self.present,
            "nulls": self.nulls,
            "nullable": self.nullable(total),
            "required": self.present == total and self.nulls == 0,
            "distinct_sample": self.distinct_sample,
        }


@dataclass
class Schema:
    row_count: int
    fields: Dict[str, FieldStats]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": self.row_count,
            "fields": {n: f.to_dict(self.row_count) for n, f in self.fields.items()},
        }


def infer_schema(records: List[Dict[str, Any]]) -> Schema:
    """Infer a Schema from a list of record dicts."""
    fields: Dict[str, FieldStats] = {}
    distinct: Dict[str, set] = {}
    for rec in records:
        if not isinstance(rec, dict):
            raise ValueError("each record must be an object/dict")
        for key, val in rec.items():
            fs = fields.get(key)
            if fs is None:
                fs = FieldStats(name=key)
                fields[key] = fs
                distinct[key] = set()
            t = _classify(val)
            fs.types[t] = fs.types.get(t, 0) + 1
            fs.present += 1
            if t == "null":
                fs.nulls += 1
            else:
                ds = distinct[key]
                if len(ds) < 1024:
                    try:
                        ds.add(val if not isinstance(val, (dict, list)) else json.dumps(val, sort_keys=True))
                    except TypeError:
                        pass
    for key, fs in fields.items():
        fs.distinct_sample = len(distinct[key])
    return Schema(row_count=len(records), fields=fields)


@dataclass
class DriftReport:
    added: List[Dict[str, Any]] = field(default_factory=list)
    removed: List[Dict[str, Any]] = field(default_factory=list)
    type_changed: List[Dict[str, Any]] = field(default_factory=list)
    nullability_changed: List[Dict[str, Any]] = field(default_factory=list)
    requiredness_changed: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def breaking(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        out.extend({**c, "kind": "removed_field"} for c in self.removed)
        out.extend(
            {**c, "kind": "type_change"} for c in self.type_changed if not c["widening"]
        )
        # A field that became required (newly mandatory) breaks producers.
        out.extend(
            {**c, "kind": "now_required"}
            for c in self.requiredness_changed
            if c["to_required"]
        )
        # A field that became nullable breaks consumers expecting non-null.
        out.extend(
            {**c, "kind": "now_nullable"}
            for c in self.nullability_changed
            if c["to_nullable"]
        )
        return out

    @property
    def has_drift(self) -> bool:
        return any(
            [
                self.added,
                self.removed,
                self.type_changed,
                self.nullability_changed,
                self.requiredness_changed,
            ]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "type_changed": self.type_changed,
            "nullability_changed": self.nullability_changed,
            "requiredness_changed": self.requiredness_changed,
            "breaking": self.breaking,
            "has_drift": self.has_drift,
            "has_breaking": bool(self.breaking),
        }


def diff_schemas(old: Schema, new: Schema) -> DriftReport:
    """Diff two inferred schemas (old=baseline, new=current)."""
    rep = DriftReport()
    old_fields = old.fields
    new_fields = new.fields

    for name, nf in new_fields.items():
        if name not in old_fields:
            rep.added.append({"field": name, "type": nf.dominant_type})

    for name, of in old_fields.items():
        if name not in new_fields:
            rep.removed.append({"field": name, "type": of.dominant_type})
            continue
        nf = new_fields[name]
        ot, nt = of.dominant_type, nf.dominant_type
        if ot != nt and ot != "null" and nt != "null":
            rep.type_changed.append(
                {
                    "field": name,
                    "from": ot,
                    "to": nt,
                    "widening": (ot, nt) in _WIDENS,
                }
            )
        o_null = of.nullable(old.row_count)
        n_null = nf.nullable(new.row_count)
        if o_null != n_null:
            rep.nullability_changed.append(
                {"field": name, "from_nullable": o_null, "to_nullable": n_null}
            )
        o_req = of.present == old.row_count and of.nulls == 0
        n_req = nf.present == new.row_count and nf.nulls == 0
        if o_req != n_req:
            rep.requiredness_changed.append(
                {"field": name, "from_required": o_req, "to_required": n_req}
            )
    return rep


@dataclass
class ContractResult:
    passed: bool
    violations: List[Dict[str, Any]] = field(default_factory=list)
    checked_rows: int = 0
    checked_fields: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_contract(records: List[Dict[str, Any]], contract: Dict[str, Any]) -> ContractResult:
    """Enforce a declarative data contract against records.

    Contract shape:
      {
        "fields": {
          "id":    {"type": "int", "required": true, "nullable": false, "unique": true},
          "email": {"type": "string", "required": true, "regex": "@"},
          "age":   {"type": "int", "min": 0, "max": 130, "nullable": true},
          "status":{"type": "string", "enum": ["active", "closed"]}
        },
        "allow_extra_fields": false
      }
    """
    import re

    spec_fields: Dict[str, Any] = contract.get("fields", {})
    allow_extra = contract.get("allow_extra_fields", True)
    violations: List[Dict[str, Any]] = []
    seen_unique: Dict[str, set] = {n: set() for n, s in spec_fields.items() if s.get("unique")}

    declared = set(spec_fields.keys())

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            violations.append({"row": i, "rule": "record_type", "detail": "row is not an object"})
            continue
        if not allow_extra:
            for extra in set(rec.keys()) - declared:
                violations.append({"row": i, "field": extra, "rule": "unexpected_field"})

        for fname, spec in spec_fields.items():
            present = fname in rec
            val = rec.get(fname)
            actual_type = _classify(val) if present else "absent"

            if not present:
                if spec.get("required"):
                    violations.append({"row": i, "field": fname, "rule": "required", "detail": "missing"})
                continue

            if actual_type == "null":
                if not spec.get("nullable", False):
                    violations.append({"row": i, "field": fname, "rule": "not_nullable"})
                continue

            want = spec.get("type")
            if want and actual_type != want and (actual_type, want) not in _WIDENS:
                violations.append(
                    {"row": i, "field": fname, "rule": "type", "expected": want, "actual": actual_type}
                )
                # type mismatch makes range/regex checks unreliable; skip them
                _track_unique(seen_unique, fname, val, i, violations)
                continue

            num = _as_number(val)
            if "min" in spec and num is not None and num < spec["min"]:
                violations.append({"row": i, "field": fname, "rule": "min", "expected": spec["min"], "actual": num})
            if "max" in spec and num is not None and num > spec["max"]:
                violations.append({"row": i, "field": fname, "rule": "max", "expected": spec["max"], "actual": num})

            if "enum" in spec and val not in spec["enum"]:
                violations.append({"row": i, "field": fname, "rule": "enum", "expected": spec["enum"], "actual": val})

            if "regex" in spec and isinstance(val, str) and not re.search(spec["regex"], val):
                violations.append({"row": i, "field": fname, "rule": "regex", "pattern": spec["regex"], "actual": val})

            _track_unique(seen_unique, fname, val, i, violations)

    return ContractResult(
        passed=len(violations) == 0,
        violations=violations,
        checked_rows=len(records),
        checked_fields=len(spec_fields),
    )


def _track_unique(seen: Dict[str, set], fname: str, val: Any, row: int, violations: List[Dict[str, Any]]) -> None:
    if fname not in seen:
        return
    key = val if not isinstance(val, (dict, list)) else json.dumps(val, sort_keys=True)
    if key in seen[fname]:
        violations.append({"row": row, "field": fname, "rule": "unique", "detail": f"duplicate value {val!r}"})
    else:
        seen[fname].add(key)


def _as_number(val: Any) -> Optional[float]:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            return None
    return None


def load_records(path: str, text: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load records from a .json (array or NDJSON) or .csv source.

    If `text` is given, `path` is only used to pick the format by extension.
    """
    if text is None:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    lower = path.lower()
    if lower.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    # JSON or NDJSON
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("top-level JSON must be an array of objects")
        return data
    # NDJSON fallback
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
