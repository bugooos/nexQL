# NOTE: This module has been moved from the root of the monolith into nexql/runtime/.
# It contains NO UI imports and is callable via the IPC bridge through server_entry.py.
# Future work: further decompose visualization into nexql/schema/ analysis helpers
# and a thin ide/ rendering layer.

"""
Edge Execution features (minimal skeleton for workbench integration and smoke tests).
Provides discovery of edge nodes and a simple routing simulation.
"""

from typing import List

def discover_edge_nodes() -> List[dict]:
    """Return a best-effort list of available edge nodes (simulated).

    This is a lightweight stub used for initial wiring and smoke tests.
    """
    return [
        {"id": "edge_1", "region": "us-east-1", "status": "online"},
        {"id": "edge_2", "region": "eu-west-1", "status": "online"},
    ]


def route_query_to_edge(query: str, edge_id: str, db: dict) -> dict:
    """Simulate routing a query to a remote edge node.

    This stub does not perform network calls; it validates the requested
    edge exists and returns a simulated response envelope.
    """
    nodes = discover_edge_nodes()
    node_ids = {n['id'] for n in nodes}
    if edge_id not in node_ids:
        return {"ok": False, "errors": [{"code": "EDGE_NOT_FOUND", "message": "Edge node not found"}]}

    # Simulated routed execution result
    return {
        "ok": True,
        "edge_id": edge_id,
        "#took": 1,
        "#data": {
            "routed_query": query,
            "result_count": 0,
        }
    }
