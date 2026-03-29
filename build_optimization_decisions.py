def decide_flow_optimization(flow_id, flow, flow_metrics, node_metrics, routing_analysis, nodes):
    decision = {
        "flow_id": flow_id,
        "strategy": None,
        "remove_alias": False,
        "remove_intermediate_qms": [],
        "manual_review": False,
        "reasons": [],
    }

    producer_qm = flow["producer_home_qm"]
    consumer_qm = flow["consumer_home_qm"]
    routing_qm = flow["routing_target_qm"]

    analysis = routing_analysis.get(flow_id, {})
    incomplete = flow_metrics.get(flow_id, {}).get("incomplete", False)

    # Unknown endpoints
    if flow["producer_app"] == "UNKNOWN_PRODUCER" or flow["consumer_app"] == "UNKNOWN_CONSUMER":
        decision["strategy"] = "manual_review_shell"
        decision["manual_review"] = True
        decision["reasons"].append("unknown_endpoint")
        return decision

    # Local-only flow
    if flow["has_local_only"]:
        decision["strategy"] = "preserve_logically_local"
        decision["reasons"].append("local_only_flow")
        return decision

    # Alias simplification
    if flow["has_alias"]:
        decision["remove_alias"] = True
        decision["reasons"].append("target_alias_present")

    # Incomplete routing / non-aligned routing
    if incomplete or "incomplete_routing_path" in analysis.get("analysis_issues", []):
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["reasons"].append("incomplete_routing_path")

    if "routing_not_aligned_with_consumer" in analysis.get("analysis_issues", []):
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["reasons"].append("routing_not_aligned_with_consumer")

    # Transit/routing-only QM removal
    routing_qms = routing_qm if isinstance(routing_qm, list) else [routing_qm]
    for rq in routing_qms:
        if rq in nodes and nodes[rq].get("routing_only", False):
            decision["remove_intermediate_qms"].append(rq)
            decision["reasons"].append("routing_only_qm")

    # Multiple routing targets
    if "multiple_routing_target_qms" in flow["issues"]:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["reasons"].append("multiple_routing_target_qms")

    # Ambiguous ownership
    if "multiple_producer_home_qms" in flow["issues"] or "multiple_consumer_home_qms" in flow["issues"]:
        if decision["strategy"] is None:
            decision["strategy"] = "normalize_ownership_then_route"
        decision["reasons"].append("ambiguous_home_qm")

    # Default for cross-QM
    if decision["strategy"] is None:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["reasons"].append("deterministic_target_routing")

    # Deduplicate
    decision["remove_intermediate_qms"] = sorted(set(decision["remove_intermediate_qms"]))
    decision["reasons"] = sorted(set(decision["reasons"]))
    return decision
	
def build_optimization_decisions(flows, flow_metrics, node_metrics, routing_analysis, nodes):
    decisions = {}
    for flow_id, flow in flows.items():
        decisions[flow_id] = decide_flow_optimization(
            flow_id=flow_id,
            flow=flow,
            flow_metrics=flow_metrics,
            node_metrics=node_metrics,
            routing_analysis=routing_analysis,
            nodes=nodes,
        )
    return decisions
