# NOTE: This module has been moved from the root of the monolith into nexql/runtime/.
# It contains NO UI imports and is callable via the IPC bridge through server_entry.py.
# Future work: further decompose visualization into nexql/schema/ analysis helpers
# and a thin ide/ rendering layer.

"""
Visualization Features for NexQL Workbench
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Comprehensive visualization system for queries, schemas, performance metrics,
and data flow diagrams.

Features (127–133):
  127. Graph visualization (query/schema as node-link diagram)
  128. Entity relationship diagrams (ERD with connections)
  129. Query flow diagrams (query execution stages as flowchart)
  130. Execution pipeline viewer (step-by-step execution visual)
  131. Live data dashboard widgets (real-time metric cards)
  132. Metrics charts (latency, cost, throughput visualizations)
  133. Streaming event viewer (real-time event timeline)
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional


# ─────────────────────────────────────────────────────────────────────────────
# 127: Graph Visualization (Node-Link Diagram in ASCII/SVG)
# ─────────────────────────────────────────────────────────────────────────────

def generate_graph_visualization(
    nodes: List[Dict[str, Any]],
    edges: List[Tuple[str, str]],
    format: str = "ascii"
) -> str:
    """
    Generate graph visualization as ASCII art or SVG.
    
    Args:
        nodes: List of nodes with 'id' and 'label'
        edges: List of (source_id, target_id) tuples
        format: 'ascii' or 'svg'
    
    Returns:
        ASCII art or SVG string representation
    """
    if format == "svg":
        return _generate_svg_graph(nodes, edges)
    else:
        return _generate_ascii_graph(nodes, edges)


def _generate_ascii_graph(nodes: List[Dict], edges: List[Tuple]) -> str:
    """Generate ASCII art graph."""
    if not nodes:
        return "No nodes to visualize"
    
    # Simple ASCII representation
    lines = ["GRAPH STRUCTURE", "=" * 50]
    lines.append("")
    
    # Draw nodes
    lines.append("NODES:")
    for node in nodes:
        lines.append(f"  ◯ {node.get('label', node['id'])}")
    
    lines.append("")
    
    # Draw edges
    if edges:
        lines.append("CONNECTIONS:")
        for src, dst in edges:
            src_label = next((n.get('label', n['id']) for n in nodes if n['id'] == src), src)
            dst_label = next((n.get('label', n['id']) for n in nodes if n['id'] == dst), dst)
            lines.append(f"  {src_label:20} → {dst_label:20}")
    
    lines.append("")
    lines.append(f"Total Nodes: {len(nodes)}")
    lines.append(f"Total Edges: {len(edges)}")
    
    return "\n".join(lines)


def _generate_svg_graph(nodes: List[Dict], edges: List[Tuple]) -> str:
    """Generate SVG graph."""
    width, height = 800, 600
    node_radius = 30
    
    # Calculate positions (simple grid layout)
    positions = {}
    cols = max(1, len(nodes) ** 0.5)
    for i, node in enumerate(nodes):
        row = i // int(cols)
        col = i % int(cols)
        x = 100 + col * (width - 200) / max(cols, 1)
        y = 100 + row * (height - 200) / max((len(nodes) // int(cols)) + 1, 1)
        positions[node['id']] = (x, y)
    
    svg_lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        '<style>',
        '  .node { fill: #4A90E2; stroke: #2E5C8A; stroke-width: 2; }',
        '  .edge { stroke: #888; stroke-width: 2; }',
        '  .label { font-size: 12px; fill: white; text-anchor: middle; }',
        '  .edge-label { font-size: 10px; fill: #666; }',
        '</style>',
        '',
    ]
    
    # Draw edges first (so they appear behind nodes)
    for src, dst in edges:
        if src in positions and dst in positions:
            x1, y1 = positions[src]
            x2, y2 = positions[dst]
            svg_lines.append(f'<line class="edge" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
            # Arrowhead
            svg_lines.append(f'<polygon points="{x2},{y2} {x2-10},{y2-5} {x2-10},{y2+5}" fill="#888"/>')
    
    # Draw nodes
    for node in nodes:
        if node['id'] in positions:
            x, y = positions[node['id']]
            label = node.get('label', node['id'])[:20]
            svg_lines.append(f'<circle class="node" cx="{x}" cy="{y}" r="{node_radius}"/>')
            svg_lines.append(f'<text class="label" x="{x}" y="{y+5}">{label}</text>')
    
    svg_lines.append('</svg>')
    
    return "\n".join(svg_lines)


# ─────────────────────────────────────────────────────────────────────────────
# 128: Entity Relationship Diagram (ERD)
# ─────────────────────────────────────────────────────────────────────────────

def generate_erd(
    entities: Dict[str, List[str]],
    relationships: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Generate Entity Relationship Diagram.
    
    Args:
        entities: Dict mapping entity name to list of field names
        relationships: List of relationship definitions
    
    Returns:
        Full ERD visualization with ASCII and SVG
    """
    erd = {
        "ascii": _generate_ascii_erd(entities, relationships),
        "entities": len(entities),
        "relationships": len(relationships),
        "generated_at": datetime.now().isoformat(),
    }
    return erd


def _generate_ascii_erd(entities: Dict[str, List[str]], relationships: List[Dict]) -> str:
    """Generate ASCII ERD."""
    lines = ["ENTITY RELATIONSHIP DIAGRAM", "=" * 60]
    lines.append("")
    
    for entity_name, fields in entities.items():
        lines.append(f"┌─────────────────┐")
        lines.append(f"│  {entity_name[:15]:15}  │")
        lines.append(f"├─────────────────┤")
        for field in fields[:5]:  # Show first 5 fields
            lines.append(f"│ • {field[:13]:13} │")
        if len(fields) > 5:
            lines.append(f"│ ... +{len(fields)-5} more  │")
        lines.append(f"└─────────────────┘")
        lines.append("")
    
    if relationships:
        lines.append("RELATIONSHIPS:")
        for rel in relationships:
            from_entity = rel.get('from', 'Unknown')
            to_entity = rel.get('to', 'Unknown')
            rel_type = rel.get('type', 'has')
            lines.append(f"  {from_entity} --[{rel_type}]-- {to_entity}")
    
    lines.append("")
    lines.append(f"Total Entities: {len(entities)}")
    lines.append(f"Total Relationships: {len(relationships)}")
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 129: Query Flow Diagram (Execution Stages)
# ─────────────────────────────────────────────────────────────────────────────

def generate_query_flow_diagram(query: str, stages: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Generate query flow diagram showing execution stages.
    
    Args:
        query: The original query string
        stages: List of execution stages
    
    Returns:
        ASCII and SVG flow diagrams
    """
    return {
        "ascii": _generate_ascii_flow(stages),
        "query": query,
        "stage_count": len(stages),
        "generated_at": datetime.now().isoformat(),
    }


def _generate_ascii_flow(stages: List[Dict]) -> str:
    """Generate ASCII flow diagram."""
    if not stages:
        stages = [
            {"name": "Parse", "duration_ms": 5},
            {"name": "Validate", "duration_ms": 2},
            {"name": "Resolve", "duration_ms": 15},
            {"name": "Execute", "duration_ms": 50},
        ]
    
    lines = ["QUERY EXECUTION FLOW", "=" * 60, ""]
    
    for i, stage in enumerate(stages):
        name = stage.get('name', f'Stage {i+1}')[:15]
        duration = stage.get('duration_ms', 0)
        status = stage.get('status', 'complete')
        
        # Visual bar
        bar_width = min(30, max(1, duration // 5))
        bar = "█" * bar_width + "░" * (30 - bar_width)
        
        lines.append(f"[{i+1}] {name:15} {bar} {duration:5}ms [{status[:10]:10}]")
        
        if i < len(stages) - 1:
            lines.append("    ↓")
    
    lines.append("")
    total_time = sum(s.get('duration_ms', 0) for s in stages)
    lines.append(f"Total Time: {total_time}ms | Stages: {len(stages)}")
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 130: Execution Pipeline Viewer (Step-by-Step Visual)
# ─────────────────────────────────────────────────────────────────────────────

def generate_execution_pipeline(
    query: str,
    execution_steps: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Generate step-by-step execution pipeline visualization.
    
    Args:
        query: Original query
        execution_steps: List of execution steps with details
    
    Returns:
        Pipeline visualization with timeline
    """
    pipeline = {
        "query": query,
        "steps": [],
        "total_steps": len(execution_steps),
        "total_duration_ms": 0,
        "timeline": _generate_pipeline_timeline(execution_steps),
        "success_rate": 1.0,
        "generated_at": datetime.now().isoformat(),
    }
    
    accumulated_time = 0
    for i, step in enumerate(execution_steps, 1):
        duration = step.get('duration_ms', 0)
        accumulated_time += duration
        
        pipeline["steps"].append({
            "number": i,
            "name": step.get('name', f'Step {i}'),
            "status": step.get('status', 'complete'),
            "duration_ms": duration,
            "cumulative_ms": accumulated_time,
            "details": step.get('details', ''),
        })
    
    pipeline["total_duration_ms"] = accumulated_time
    
    return pipeline


def _generate_pipeline_timeline(steps: List[Dict]) -> str:
    """Generate ASCII timeline of execution steps."""
    if not steps:
        return "No execution steps"
    
    lines = ["EXECUTION TIMELINE", "-" * 60, ""]
    
    max_name_len = max(len(s.get('name', 'Step')) for s in steps)
    max_duration = max(s.get('duration_ms', 1) for s in steps)
    
    accumulated = 0
    for i, step in enumerate(steps, 1):
        name = step.get('name', f'Step {i}')
        duration = step.get('duration_ms', 0)
        status_icon = "✓" if step.get('status') == 'complete' else "⚠"
        
        # Timeline bar (scale to max)
        bar_width = int((duration / max_duration) * 40) if max_duration > 0 else 1
        timeline_bar = "=" * bar_width
        
        accumulated += duration
        
        lines.append(f"{i}. {name:20} {status_icon} {timeline_bar:40} {duration:5}ms [t={accumulated:5}ms]")
    
    lines.append("")
    total_duration = sum(s.get('duration_ms', 0) for s in steps)
    lines.append(f"Total: {total_duration}ms across {len(steps)} steps")
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 131: Live Data Dashboard Widgets
# ─────────────────────────────────────────────────────────────────────────────

def create_dashboard_widgets(metrics: Dict[str, Any]) -> Dict[str, Dict]:
    """
    Create dashboard widgets for real-time metrics display.
    
    Returns:
        Dictionary of widget configurations
    """
    return {
        "queries_executed": {
            "title": "Queries Executed",
            "type": "counter",
            "value": metrics.get('queries_executed', 0),
            "unit": "queries",
            "trend": "↑" if metrics.get('trending_up', False) else "→",
        },
        "avg_response_time": {
            "title": "Avg Response Time",
            "type": "gauge",
            "value": metrics.get('avg_response_ms', 0),
            "unit": "ms",
            "min": 0,
            "max": 1000,
        },
        "cache_hit_rate": {
            "title": "Cache Hit Rate",
            "type": "gauge",
            "value": metrics.get('cache_hit_rate', 0) * 100,
            "unit": "%",
            "min": 0,
            "max": 100,
        },
        "error_rate": {
            "title": "Error Rate",
            "type": "gauge",
            "value": metrics.get('error_rate', 0) * 100,
            "unit": "%",
            "min": 0,
            "max": 100,
        },
        "active_connections": {
            "title": "Active Connections",
            "type": "counter",
            "value": metrics.get('active_connections', 0),
            "unit": "connections",
        },
        "throughput": {
            "title": "Throughput",
            "type": "gauge",
            "value": metrics.get('queries_per_second', 0),
            "unit": "qps",
            "trend": "↑",
        },
    }


def render_dashboard_widgets(widgets: Dict[str, Dict]) -> str:
    """Render dashboard widgets as ASCII."""
    lines = ["DASHBOARD METRICS", "=" * 70, ""]
    
    for widget_id, widget in list(widgets.items())[:6]:
        title = widget.get('title', widget_id)
        value = widget.get('value', 0)
        unit = widget.get('unit', '')
        widget_type = widget.get('type', 'gauge')
        
        if widget_type == "counter":
            lines.append(f"┌─ {title:30} ──────────────────┐")
            lines.append(f"│ {value:>15} {unit:20} │")
            lines.append(f"└──────────────────────────────────────────┘")
        else:  # gauge
            max_val = widget.get('max', 100)
            scaled = int((value / max_val) * 30) if max_val > 0 else 0
            bar = "█" * scaled + "░" * (30 - scaled)
            lines.append(f"┌─ {title:30} ──────────┐")
            lines.append(f"│ {bar} {value:>6.1f}{unit} │")
            lines.append(f"└──────────────────────────────────────────┘")
        
        lines.append("")
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 132: Metrics Charts (Latency, Cost, Throughput)
# ─────────────────────────────────────────────────────────────────────────────

def generate_metrics_chart(
    metric_type: str,
    data_points: List[Tuple[str, float]],
    chart_type: str = "line"
) -> str:
    """
    Generate ASCII metrics chart.
    
    Args:
        metric_type: 'latency', 'cost', 'throughput', etc.
        data_points: List of (label, value) tuples
        chart_type: 'line', 'bar', 'area'
    
    Returns:
        ASCII chart representation
    """
    if not data_points:
        return f"No data for {metric_type}"
    
    if chart_type == "bar":
        return _generate_bar_chart(metric_type, data_points)
    elif chart_type == "line":
        return _generate_line_chart(metric_type, data_points)
    else:
        return _generate_area_chart(metric_type, data_points)


def _generate_bar_chart(metric_type: str, data_points: List[Tuple]) -> str:
    """Generate ASCII bar chart."""
    lines = [f"{metric_type.upper()} - BAR CHART", "=" * 60, ""]
    
    if not data_points:
        return "\n".join(lines) + "No data"
    
    max_value = max(v for _, v in data_points)
    max_value = max_value if max_value > 0 else 1
    
    for label, value in data_points[-10:]:  # Show last 10
        bar_width = int((value / max_value) * 40)
        bar = "█" * bar_width
        lines.append(f"{label:15} │ {bar:40} {value:>8.2f}")
    
    lines.append("")
    lines.append(f"Max: {max_value:.2f} | Min: {min(v for _, v in data_points):.2f}")
    
    return "\n".join(lines)


def _generate_line_chart(metric_type: str, data_points: List[Tuple]) -> str:
    """Generate ASCII line chart."""
    lines = [f"{metric_type.upper()} - LINE CHART", "=" * 60, ""]
    
    if not data_points:
        return "\n".join(lines) + "No data"
    
    values = [v for _, v in data_points]
    max_val = max(values)
    min_val = min(values)
    range_val = max_val - min_val if max_val > min_val else 1
    
    height = 10
    for row in range(height - 1, -1, -1):
        threshold = min_val + (range_val * row / height)
        line = f"{threshold:7.1f} │ "
        
        for _, value in data_points[-40:]:
            if value >= threshold:
                line += "█"
            else:
                line += " "
        lines.append(line)
    
    lines.append("       └" + "─" * 40)
    labels = [l[:3] for l, _ in data_points[-40:]]
    lines.append("         " + " ".join(labels))
    
    return "\n".join(lines)


def _generate_area_chart(metric_type: str, data_points: List[Tuple]) -> str:
    """Generate ASCII area chart."""
    lines = [f"{metric_type.upper()} - AREA CHART", "=" * 60, ""]
    
    if not data_points:
        return "\n".join(lines) + "No data"
    
    values = [v for _, v in data_points]
    max_val = max(values)
    min_val = min(values)
    range_val = max_val - min_val if max_val > min_val else 1
    
    height = 8
    for row in range(height - 1, -1, -1):
        threshold = min_val + (range_val * row / height)
        line = f"{threshold:7.1f} │ "
        
        for _, value in data_points[-40:]:
            if value >= threshold:
                line += "▓"
            elif value > threshold - (range_val / height):
                line += "▒"
            else:
                line += " "
        lines.append(line)
    
    lines.append("       └" + "─" * 40)
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 133: Streaming Event Viewer (Real-Time Event Timeline)
# ─────────────────────────────────────────────────────────────────────────────

def create_event_stream_viewer(
    events: List[Dict[str, Any]],
    max_recent: int = 20
) -> Dict[str, Any]:
    """
    Create real-time event stream viewer from event list.
    
    Args:
        events: List of event dictionaries with 'timestamp' and 'type'
        max_recent: Maximum recent events to display
    
    Returns:
        Event stream visualization data
    """
    recent_events = events[-max_recent:] if events else []
    
    return {
        "total_events": len(events),
        "recent_count": len(recent_events),
        "timeline": _generate_event_timeline(recent_events),
        "event_types": _count_event_types(recent_events),
        "generated_at": datetime.now().isoformat(),
    }


def _generate_event_timeline(events: List[Dict]) -> str:
    """Generate ASCII event timeline."""
    lines = ["EVENT STREAM TIMELINE", "=" * 60, ""]
    
    if not events:
        return "\n".join(lines) + "No events"
    
    for i, event in enumerate(events[-15:], 1):
        event_type = event.get('type', 'unknown')
        timestamp = event.get('timestamp', 'n/a')
        details = event.get('details', '')
        
        # Indent based on type
        indent = "  " if event_type in ['cache.hit', 'success'] else "→ "
        
        # Icon based on type
        icon = "✓" if 'success' in event_type else "✗" if 'error' in event_type else "→"
        
        lines.append(f"{i:2}. {icon} {timestamp:20} [{event_type:15}] {details[:30]}")
    
    lines.append("")
    
    return "\n".join(lines)


def _count_event_types(events: List[Dict]) -> Dict[str, int]:
    """Count events by type."""
    counts = {}
    for event in events:
        event_type = event.get('type', 'unknown')
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def render_event_statistics(event_stream: Dict) -> str:
    """Render event statistics."""
    lines = ["EVENT STATISTICS", "=" * 60, ""]
    
    event_types = event_stream.get('event_types', {})
    
    lines.append(f"Total Events: {event_stream.get('total_events', 0)}")
    lines.append(f"Recent Events: {event_stream.get('recent_count', 0)}")
    lines.append("")
    
    if event_types:
        lines.append("Event Type Breakdown:")
        for event_type, count in sorted(event_types.items(), key=lambda x: x[1], reverse=True):
            bar_width = min(30, count)
            bar = "█" * bar_width
            lines.append(f"  {event_type:20} {bar} {count:4}")
    
    lines.append("")
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Utility: Combined Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def generate_comprehensive_dashboard(
    query_data: Dict[str, Any],
    metrics: Dict[str, Any],
    events: List[Dict[str, Any]]
) -> Dict[str, str]:
    """
    Generate comprehensive dashboard with all visualizations.
    """
    dashboard = {
        "query_flow": _generate_ascii_flow(query_data.get('stages', [])),
        "execution_pipeline": _generate_pipeline_timeline(query_data.get('steps', [])),
        "metrics_widgets": render_dashboard_widgets(create_dashboard_widgets(metrics)),
        "event_timeline": _generate_event_timeline(events),
        "generated_at": datetime.now().isoformat(),
    }
    
    return dashboard
