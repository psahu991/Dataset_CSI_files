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

    for flow in flows.values():
        if not flow["has_remote"]:
            continue

        prod = flow["producer_home_qm"]
        cons = flow["consumer_home_qm"]
        route = flow["routing_target_qm"]

        prod_list = prod if isinstance(prod, list) else [prod]
        cons_list = cons if isinstance(cons, list) else [cons]
        route_list = route if isinstance(route, list) else [route]

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

    return {
        "total_qms": total_qms,
        "total_physical_edges": total_physical_edges,
        "total_logical_edges": total_logical_edges,
        "routing_only_qms": routing_only_qms,
        "local_only_flows": local_only_flows,
        "incomplete_flows": incomplete_flows,
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

        prod = flow["producer_home_qm"]
        cons = flow["consumer_home_qm"]
        route = flow["routing_target_qm"]

        prod_list = prod if isinstance(prod, list) else [prod]
        cons_list = cons if isinstance(cons, list) else [cons]
        route_list = route if isinstance(route, list) else [route]

        # hop count (simple estimation)
        if not flow["has_remote"]:
            hops = 0
        else:
            hops = 1  # base
            # check if multi-hop
            multi_hop = False
            for rq in route_list:
                for cq in cons_list:
                    if rq != cq:
                        if path_exists(adj, rq, cq):
                            multi_hop = True
            if multi_hop:
                hops = 2

        # incomplete
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

        flow_metrics[flow_id] = {
            "hops": hops,
            "has_remote": flow["has_remote"],
            "has_alias": flow["has_alias"],
            "local_only": flow["has_local_only"],
            "incomplete": incomplete,
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
