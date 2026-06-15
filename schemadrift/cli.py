"""Command-line interface for SCHEMADRIFT.

Subcommands:
  infer    <data>                  infer + print a schema
  drift    <baseline> <current>    detect schema drift between two datasets
  contract <data> --contract C     enforce a declarative data contract

Global flags: --version, --format {table,json}

Exit codes:
  0  success / no drift / contract passed
  1  bad usage or runtime error
  2  drift detected (drift) or contract violations (contract)
  3  BREAKING drift detected (drift only)
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import infer_schema, diff_schemas, check_contract, load_records


def _emit(obj: Dict[str, Any], fmt: str, table_fn) -> None:
    if fmt == "json":
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    else:
        print(table_fn(obj))


def _schema_table(d: Dict[str, Any]) -> str:
    rows = [f"rows: {d['row_count']}", ""]
    rows.append(f"{'FIELD':<24} {'TYPE':<8} {'REQ':<4} {'NULL':<5} {'DISTINCT':>8}")
    rows.append("-" * 54)
    for name, f in d["fields"].items():
        rows.append(
            f"{name:<24} {f['type']:<8} {('yes' if f['required'] else 'no'):<4} "
            f"{('yes' if f['nullable'] else 'no'):<5} {f['distinct_sample']:>8}"
        )
    return "\n".join(rows)


def _drift_table(d: Dict[str, Any]) -> str:
    lines: List[str] = []
    def section(title: str, items: List[Dict[str, Any]], render) -> None:
        if items:
            lines.append(f"== {title} ({len(items)}) ==")
            for it in items:
                lines.append("  " + render(it))
    section("ADDED", d["added"], lambda i: f"+ {i['field']} ({i['type']})")
    section("REMOVED", d["removed"], lambda i: f"- {i['field']} ({i['type']})")
    section(
        "TYPE CHANGED",
        d["type_changed"],
        lambda i: f"~ {i['field']}: {i['from']} -> {i['to']}" + (" (widening)" if i["widening"] else " (BREAKING)"),
    )
    section(
        "NULLABILITY",
        d["nullability_changed"],
        lambda i: f"? {i['field']}: nullable {i['from_nullable']} -> {i['to_nullable']}",
    )
    section(
        "REQUIREDNESS",
        d["requiredness_changed"],
        lambda i: f"! {i['field']}: required {i['from_required']} -> {i['to_required']}",
    )
    if not d["has_drift"]:
        return "no drift detected"
    lines.append("")
    lines.append(f"breaking changes: {len(d['breaking'])}")
    return "\n".join(lines)


def _contract_table(d: Dict[str, Any]) -> str:
    head = (
        f"contract: {'PASS' if d['passed'] else 'FAIL'}  "
        f"(rows={d['checked_rows']}, fields={d['checked_fields']}, violations={len(d['violations'])})"
    )
    lines = [head]
    for v in d["violations"][:200]:
        loc = f"row {v.get('row')}"
        fld = f".{v['field']}" if "field" in v else ""
        extra = {k: val for k, val in v.items() if k not in ("row", "field", "rule")}
        lines.append(f"  [{v['rule']}] {loc}{fld} {extra if extra else ''}".rstrip())
    if len(d["violations"]) > 200:
        lines.append(f"  ... {len(d['violations']) - 200} more")
    return "\n".join(lines)


def _cmd_infer(args) -> int:
    records = load_records(args.data)
    schema = infer_schema(records)
    _emit(schema.to_dict(), args.format, _schema_table)
    return 0


def _cmd_drift(args) -> int:
    old = infer_schema(load_records(args.baseline))
    new = infer_schema(load_records(args.current))
    report = diff_schemas(old, new)
    d = report.to_dict()
    _emit(d, args.format, _drift_table)
    if d["has_breaking"]:
        return 3
    if d["has_drift"]:
        return 2
    return 0


def _cmd_contract(args) -> int:
    records = load_records(args.data)
    with open(args.contract, "r", encoding="utf-8") as fh:
        contract = json.load(fh)
    result = check_contract(records, contract)
    _emit(result.to_dict(), args.format, _contract_table)
    return 0 if result.passed else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description="Schema-change detector and data-contract tests.")
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    p.add_argument("--format", choices=["table", "json"], default="table", help="output format")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("infer", help="infer a schema from a dataset")
    pi.add_argument("data", help="path to .json / .ndjson / .csv")
    pi.set_defaults(func=_cmd_infer)

    pd = sub.add_parser("drift", help="detect drift between baseline and current datasets")
    pd.add_argument("baseline", help="baseline dataset path")
    pd.add_argument("current", help="current dataset path")
    pd.set_defaults(func=_cmd_drift)

    pc = sub.add_parser("contract", help="enforce a data contract against a dataset")
    pc.add_argument("data", help="dataset path")
    pc.add_argument("--contract", required=True, help="path to contract JSON")
    pc.set_defaults(func=_cmd_contract)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse copies --format onto the namespace; ensure it exists for subparsers.
    if not hasattr(args, "format"):
        args.format = "table"
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"error: file not found: {e.filename}", file=sys.stderr)
        return 1
    except IsADirectoryError as e:
        print(f"error: path is a directory, not a file: {e.filename}", file=sys.stderr)
        return 1
    except PermissionError as e:
        print(f"error: permission denied: {e.filename}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: I/O error: {e}", file=sys.stderr)
        return 1
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
