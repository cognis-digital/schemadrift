"""SCHEMADRIFT MCP server — exposes infer/drift/contract as MCP tools for Cognis.Studio."""
from __future__ import annotations

import json
import sys


def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-schemadrift[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print(
            "error: MCP extra not installed. Run: pip install 'cognis-schemadrift[mcp]'",
            file=sys.stderr,
        )
        return 1

    from schemadrift.core import infer_schema, diff_schemas, load_records

    app = FastMCP("schemadrift")

    @app.tool()
    def schemadrift_infer(path: str) -> str:
        """Infer a schema from a dataset file (.json/.ndjson/.csv). Returns JSON."""
        try:
            records = load_records(path)
            return json.dumps(infer_schema(records).to_dict(), indent=2, default=str)
        except (FileNotFoundError, PermissionError, OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @app.tool()
    def schemadrift_drift(baseline: str, current: str) -> str:
        """Detect schema drift between two dataset files. Returns JSON drift report."""
        try:
            old = infer_schema(load_records(baseline))
            new = infer_schema(load_records(current))
            return json.dumps(diff_schemas(old, new).to_dict(), indent=2, default=str)
        except (FileNotFoundError, PermissionError, OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    app.run()
    return 0
