"""SCHEMADRIFT - Schema-change detector and data-contract tests.

Infer a schema from tabular/JSON data, diff two schemas to detect drift
(added/removed/renamed-ish fields, type changes, nullability and cardinality
shifts), and enforce a declarative data contract against a dataset.

Standard library only. Zero install.
"""
from .core import (
    infer_schema,
    diff_schemas,
    check_contract,
    load_records,
    FieldStats,
    Schema,
    DriftReport,
    ContractResult,
)

TOOL_NAME = "schemadrift"
TOOL_VERSION = "1.0.0"

__all__ = [
    "TOOL_NAME",
    "TOOL_VERSION",
    "infer_schema",
    "diff_schemas",
    "check_contract",
    "load_records",
    "FieldStats",
    "Schema",
    "DriftReport",
    "ContractResult",
]
