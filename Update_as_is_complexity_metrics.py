from collections import defaultdict


# ------------------------------------------------------------
# 1. BUILD ADJACENCY (for path checks)
# ------------------------------------------------------------

def build_adjacency(physical_edges):
    adj = defaultdict(set)
    for (src, tgt) in physical_edges.keys():
        adj[src].add(tgt)
    return adj


def path_exists(adj, start, target):
    if start == target:
        return True

    visited = set()
    stack = [start]

    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)

        for nxt in adj.get(node, []):
            if nxt == target:
                return True
            if nxt not in visited:
                stack.append(nxt)

    return False


def as_list(value):
    if isinstance(value, list):
        return [v for v in value if str(v).strip()]
    if str(value).strip():
        return [value]
    return []


# ------------------------------------------------------------
# 2. GLOBAL METRICS
# ------------------------------------------------------------

def compute_global_metrics(flows, nodes, physical_edges, logical_edges):
    adj = build_adjacency(physical_edges)

    total_qms = len(nodes)
    total_physical_edges = len(physical_edges)
    total_logical_edges = len(logical_edges)

    routing_only_qms = sum(1 for n in nodes.values() if n["routing_only"])
    local_only_flows = sum(1 for f in flows.values() if f["has_local_only"])

    incomplete_flows = 0

    local_complete_flows = 0
    indirect_complete_flows = 0
    ambiguous_flows = 0
    orphan_flows = 0
    unresolved_flows = 0

    qm_only_match_flows = 0
    queue_only_match_flows = 0
    multi_candidate_flows = 0

    high_confidence_flows = 0
    medium_confidence_flows = 0
    low_confidence_flows = 0

    for flow in flows.values():
        status = flow.get("resolution_status", "UNRESOLVED")
        confidence = flow.get("match_confidence", "LOW")

        if confidence == "HIGH":
            high_confidence_flows += 1
        elif confidence == "MEDIUM":
            medium_confidence_flows += 1
        else:
            low_confidence_flows += 1

        if status == "LOCAL_COMPLETE":
            local_complete_flows += 1

        elif status == "INDIRECT_COMPLETE":
            indirect_complete_flows += 1

        elif status == "QM_ONLY_MATCH":
            ambiguous_flows += 1
            qm_only_match_flows += 1

        elif status == "QUEUE_ONLY_MATCH":
            ambiguous_flows += 1
            queue_only_match_flows += 1

        elif status == "MULTI_CANDIDATE":
            ambiguous_flows += 1
            multi_candidate_flows += 1

        elif status == "ORPHAN":
            orphan_flows += 1

        elif status == "UNRESOLVED":
            unresolved_flows += 1

        # keep graph-based incomplete check too
        if flow["has_remote"]:
            cons_list = as_list(flow["consumer_home_qm"])
            route_list = as_list(flow["routing_target_qm"])

            reachable = False
            for rq in route_list:
                for cq in cons_list:
                    if path_exists(adj, rq, cq):
                        reachable = True
                        break
                if reachable:
                    break

            if not reachable:
                incomplete_flows += 1

    total_flows = len(flows)
    ambiguity_ratio = ambiguous_flows / total_flows if total_flows else 0
    orphan_ratio = orphan_flows / total_flows if total_flows else 0
    incomplete_ratio = incomplete_flows / total_flows if total_flows else 0

    return {
        "total_flows": total_flows,
        "total_qms": total_qms,
        "total_physical_edges": total_physical_edges,
        "total_logical_edges": total_logical_edges,
        "routing_only_qms": routing_only_qms,

        "local_only_flows": local_only_flows,
        "local_complete_flows": local_complete_flows,
        "indirect_complete_flows": indirect_complete_flows,

        "ambiguous_flows": ambiguous_flows,
        "qm_only_match_flows": qm_only_match_flows,
        "queue_only_match_flows": queue_only_match_flows,
        "multi_candidate_flows": multi_candidate_flows,

        "orphan_flows": orphan_flows,
        "unresolved_flows": unresolved_flows,
        "incomplete_flows": incomplete_flows,

        "high_confidence_flows": high_confidence_flows,
        "medium_confidence_flows": medium_confidence_flows,
        "low_confidence_flows": low_confidence_flows,

        "ambiguity_ratio": round(ambiguity_ratio, 4),
        "orphan_ratio": round(orphan_ratio, 4),
        "incomplete_ratio": round(incomplete_ratio, 4),
    }


# ------------------------------------------------------------
# 3. NODE-LEVEL METRICS
# ------------------------------------------------------------

def compute_node_metrics(nodes, physical_edges):
    fan_out = defaultdict(int)
    fan_in = defaultdict(int)

    for (src, tgt) in physical_edges.keys():
        fan_out[src] += 1
        fan_in[tgt] += 1

    node_metrics = {}

    for qm, meta in nodes.items():
        node_metrics[qm] = {
            "fan_out": fan_out[qm],
            "fan_in": fan_in[qm],
            "is_routing_only": meta["routing_only"],
            "num_apps": len(meta["hosted_apps"]),
            "produced_flows": len(meta["produced_flows"]),
            "consumed_flows": len(meta["consumed_flows"]),
        }

    return node_metrics


# ------------------------------------------------------------
# 4. FLOW-LEVEL METRICS
# ------------------------------------------------------------

def compute_flow_metrics(flows, physical_edges):
    adj = build_adjacency(physical_edges)
    flow_metrics = {}

    for flow_id, flow in flows.items():
        cons_list = as_list(flow["consumer_home_qm"])
        route_list = as_list(flow["routing_target_qm"])

        # hop count (simple estimation)
        if not flow["has_remote"]:
            hops = 0
        else:
            hops = 1
            multi_hop = False

            for rq in route_list:
                for cq in cons_list:
                    if rq != cq and path_exists(adj, rq, cq):
                        multi_hop = True
                        break
                if multi_hop:
                    break

            if multi_hop:
                hops = 2

        # graph-based incomplete
        incomplete = False
        if flow["has_remote"]:
            reachable = False
            for rq in route_list:
                for cq in cons_list:
                    if path_exists(adj, rq, cq):
                        reachable = True
                        break
                if reachable:
                    break
            incomplete = not reachable

        status = flow.get("resolution_status", "UNRESOLVED")
        ambiguity_type = flow.get("ambiguity_type")
        match_confidence = flow.get("match_confidence", "LOW")

        ambiguous = status in {"QM_ONLY_MATCH", "QUEUE_ONLY_MATCH", "MULTI_CANDIDATE"}

        flow_metrics[flow_id] = {
            "hops": hops,
            "has_remote": flow["has_remote"],
            "has_alias": flow["has_alias"],
            "local_only": flow["has_local_only"],
            "incomplete": incomplete,
            "ambiguous": ambiguous,
            "resolution_status": status,
            "ambiguity_type": ambiguity_type,
            "match_confidence": match_confidence,
            "exact_qm_match": flow.get("exact_qm_match", False),
            "exact_queue_match": flow.get("exact_queue_match", False),
            "record_count": flow["record_count"],
            "distinct_flow_count": flow["distinct_flow_count"],
        }

    return flow_metrics


# ------------------------------------------------------------
# 5. COMBINED COMPLEXITY MATRIX
# ------------------------------------------------------------

def build_complexity_matrix(flows, nodes, physical_edges, logical_edges):
    global_metrics = compute_global_metrics(
        flows, nodes, physical_edges, logical_edges
    )

    node_metrics = compute_node_metrics(nodes, physical_edges)
    flow_metrics = compute_flow_metrics(flows, physical_edges)

    return {
        "global": global_metrics,
        "nodes": node_metrics,
        "flows": flow_metrics
  }

flows = extract_flows_refined(df)
nodes, physical_edges, logical_edges = build_qm_graph_data(flows)

matrix = build_complexity_matrix(flows, nodes, physical_edges, logical_edges)

print(matrix["global"])
