from collections import defaultdict


# ------------------------------------------------------------
# 1. TARGET GLOBAL METRICS
# ------------------------------------------------------------

def compute_target_global_metrics(target_flows, target_nodes, target_edges):
    total_qms = len(target_nodes)
    total_physical_edges = len(target_edges)

    local_only_flows = sum(1 for tf in target_flows.values() if tf["producer_target_qm"] == tf["consumer_target_qm"])
    cross_qm_flows = sum(1 for tf in target_flows.values() if tf["producer_target_qm"] != tf["consumer_target_qm"])
    invalid_flows = sum(1 for tf in target_flows.values() if tf.get("constraint_valid") is False)
    manual_review_flows = sum(1 for tf in target_flows.values() if tf.get("manual_review", False))

    # In target, routing should always be deterministic
    incomplete_flows = 0
    routing_only_qms = 0  # target model should not need routing-only QMs unless you explicitly keep them

    return {
        "total_qms": total_qms,
        "total_physical_edges": total_physical_edges,
        "local_only_flows": local_only_flows,
        "cross_qm_flows": cross_qm_flows,
        "invalid_flows": invalid_flows,
        "manual_review_flows": manual_review_flows,
        "incomplete_flows": incomplete_flows,
        "routing_only_qms": routing_only_qms,
    }


# ------------------------------------------------------------
# 2. TARGET NODE METRICS
# ------------------------------------------------------------

def compute_target_node_metrics(target_nodes, target_edges):
    fan_out = defaultdict(int)
    fan_in = defaultdict(int)

    for (src, tgt) in target_edges.keys():
        fan_out[src] += 1
        fan_in[tgt] += 1

    node_metrics = {}

    for qm, meta in target_nodes.items():
        node_metrics[qm] = {
            "fan_out": fan_out[qm],
            "fan_in": fan_in[tgt] if False else fan_in[qm],
            "num_apps": len(meta["hosted_apps"]),
            "produced_flows": len(meta["produced_flows"]),
            "consumed_flows": len(meta["consumed_flows"]),
            "local_only_flows": len(meta["local_only_flows"]),
        }

    return node_metrics


# ------------------------------------------------------------
# 3. TARGET FLOW METRICS
# ------------------------------------------------------------

def compute_target_flow_metrics(target_flows):
    flow_metrics = {}

    for flow_id, tf in target_flows.items():
        is_cross_qm = tf["producer_target_qm"] != tf["consumer_target_qm"]

        flow_metrics[flow_id] = {
            "hops": 1 if is_cross_qm else 0,
            "cross_qm": is_cross_qm,
            "local_only": not is_cross_qm,
            "has_alias": tf.get("remove_alias", False) is False and False,  # target should not keep alias in this model
            "incomplete": False,
            "manual_review": tf.get("manual_review", False),
            "constraint_valid": tf.get("constraint_valid", None),
        }

    return flow_metrics


# ------------------------------------------------------------
# 4. BUILD TARGET COMPLEXITY MATRIX
# ------------------------------------------------------------

def build_target_complexity_matrix(target_flows, target_nodes, target_edges):
    global_metrics = compute_target_global_metrics(target_flows, target_nodes, target_edges)
    node_metrics = compute_target_node_metrics(target_nodes, target_edges)
    flow_metrics = compute_target_flow_metrics(target_flows)

    return {
        "global": global_metrics,
        "nodes": node_metrics,
        "flows": flow_metrics
    }
