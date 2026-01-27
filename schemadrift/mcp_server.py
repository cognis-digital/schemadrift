"""SCHEMADRIFT MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from schemadrift.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-schemadrift[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-schemadrift[mcp]'")
        return 1
    app = FastMCP("schemadrift")

    @app.tool()
    def schemadrift_scan(target: str) -> str:
        """Schema-change detector and data-contract tests. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
