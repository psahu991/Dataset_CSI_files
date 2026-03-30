def as_list(value):
    if isinstance(value, list):
        return [v for v in value if str(v).strip()]
    if str(value).strip():
        return [value]
    return []


def decide_flow_optimization(flow_id, flow, flow_metrics, node_metrics, routing_analysis, nodes):
    """
    Build optimization decision for one refined flow.

    This version is aligned to the latest refined extractor and uses:
    - resolution_status
    - ambiguity_type
    - match_confidence
    - exact_qm_match / exact_queue_match
    - producer_apps / consumer_apps
    """

    decision = {
        "flow_id": flow_id,
        "strategy": None,
        "remove_alias": False,
        "remove_intermediate_qms": [],
        "manual_review": False,
        "exclude_from_target": False,
        "normalize_producer_ownership": False,
        "normalize_consumer_ownership": False,
        "target_producer_qm": None,
        "target_consumer_qm": None,
        "target_route_type": None,
        "confidence": flow.get("match_confidence", "LOW"),
        "reasons": [],
    }

    producer_qm = flow.get("producer_home_qm", "")
    consumer_qm = flow.get("consumer_home_qm", "")
    routing_qm = flow.get("routing_target_qm", "")

    producer_qms = as_list(producer_qm)
    consumer_qms = as_list(consumer_qm)
    routing_qms = as_list(routing_qm)

    producer_apps = flow.get("producer_apps", [])
    consumer_apps = flow.get("consumer_apps", [])

    resolution_status = flow.get("resolution_status", "UNRESOLVED")
    ambiguity_type = flow.get("ambiguity_type")
    incomplete = flow_metrics.get(flow_id, {}).get("incomplete", False)

    analysis = routing_analysis.get(flow_id, {})
    analysis_issues = set(analysis.get("analysis_issues", []))
    flow_issues = set(flow.get("issues", []))

    # ------------------------------------------------------------
    # 1. Hard stop cases: unknown endpoints
    # ------------------------------------------------------------
    if flow.get("producer_app") == "UNKNOWN_PRODUCER" or flow.get("consumer_app") == "UNKNOWN_CONSUMER":
        decision["strategy"] = "manual_review_shell"
        decision["manual_review"] = True
        decision["exclude_from_target"] = True
        decision["reasons"].append("unknown_endpoint")
        return decision

    # ------------------------------------------------------------
    # 2. Resolution-based gating
    # ------------------------------------------------------------
    if resolution_status in {"ORPHAN", "UNRESOLVED"}:
        decision["strategy"] = "exclude_from_target"
        decision["exclude_from_target"] = True
        decision["manual_review"] = True
        decision["reasons"].append(resolution_status.lower())
        if ambiguity_type:
            decision["reasons"].append(ambiguity_type)
        return decision

    if resolution_status in {"QM_ONLY_MATCH", "QUEUE_ONLY_MATCH", "MULTI_CANDIDATE"}:
        decision["strategy"] = "manual_review_before_target"
        decision["manual_review"] = True
        decision["exclude_from_target"] = True
        decision["reasons"].append("ambiguous_flow")
        decision["reasons"].append(resolution_status.lower())
        if ambiguity_type:
            decision["reasons"].append(ambiguity_type)
        return decision

    # ------------------------------------------------------------
    # 3. Local-only complete
    # ------------------------------------------------------------
    if flow.get("has_local_only", False) or resolution_status == "LOCAL_COMPLETE":
        decision["strategy"] = "canonicalize_local_flow"
        decision["target_route_type"] = "local_only"
        decision["reasons"].append("local_complete_flow")

        if len(producer_qms) != 1:
            decision["normalize_producer_ownership"] = True
            decision["manual_review"] = True
            decision["reasons"].append("multiple_producer_home_qms")

        if len(consumer_qms) != 1:
            decision["normalize_consumer_ownership"] = True
            decision["manual_review"] = True
            decision["reasons"].append("multiple_consumer_home_qms")

        if len(producer_apps) > 1:
            decision["manual_review"] = True
            decision["reasons"].append("multiple_producer_apps")

        if len(consumer_apps) > 1:
            decision["manual_review"] = True
            decision["reasons"].append("multiple_consumer_apps")

        decision["target_producer_qm"] = producer_qms[0] if len(producer_qms) == 1 else None
        decision["target_consumer_qm"] = consumer_qms[0] if len(consumer_qms) == 1 else None

        return finalize_decision(decision)

    # ------------------------------------------------------------
    # 4. Indirect complete: valid AS-IS understanding, but still redesign
    # ------------------------------------------------------------
    if resolution_status == "INDIRECT_COMPLETE":
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["reasons"].append("indirect_complete_flow")
        decision["reasons"].append("canonical_target_redesign_required")

    # ------------------------------------------------------------
    # 5. Alias simplification
    # ------------------------------------------------------------
    if flow.get("has_alias", False):
        decision["remove_alias"] = True
        decision["reasons"].append("target_alias_present")

    # ------------------------------------------------------------
    # 6. Constraint-driven ownership normalization
    # ------------------------------------------------------------
    if len(producer_qms) != 1 or "multiple_producer_home_qms" in flow_issues:
        decision["normalize_producer_ownership"] = True
        decision["manual_review"] = True
        decision["reasons"].append("ambiguous_producer_home_qm")

    if len(consumer_qms) != 1 or "multiple_consumer_home_qms" in flow_issues:
        decision["normalize_consumer_ownership"] = True
        decision["manual_review"] = True
        decision["reasons"].append("ambiguous_consumer_home_qm")

    if len(producer_apps) > 1:
        decision["manual_review"] = True
        decision["reasons"].append("multiple_producer_apps")

    if len(consumer_apps) > 1:
        decision["manual_review"] = True
        decision["reasons"].append("multiple_consumer_apps")

    # Candidate target QMs only if uniquely known
    if len(producer_qms) == 1:
        decision["target_producer_qm"] = producer_qms[0]

    if len(consumer_qms) == 1:
        decision["target_consumer_qm"] = consumer_qms[0]

    # ------------------------------------------------------------
    # 7. Routing quality / incompleteness
    # ------------------------------------------------------------
    if incomplete or "incomplete_routing_path" in analysis_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["reasons"].append("incomplete_routing_path")

    if "routing_not_aligned_with_consumer" in analysis_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["reasons"].append("routing_not_aligned_with_consumer")

    if "multiple_routing_target_qms" in flow_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["manual_review"] = True
        decision["reasons"].append("multiple_routing_target_qms")

    # ------------------------------------------------------------
    # 8. Remove routing-only intermediate QMs where safe
    # ------------------------------------------------------------
    for rq in routing_qms:
        if rq in nodes and nodes[rq].get("routing_only", False):
            # remove only if it is not the final consumer QM
            if rq not in consumer_qms:
                decision["remove_intermediate_qms"].append(rq)
                decision["reasons"].append("routing_only_qm")

    # ------------------------------------------------------------
    # 9. Default strategy if still unset
    # ------------------------------------------------------------
    if decision["strategy"] is None:
        if flow.get("has_remote", False):
            decision["strategy"] = "direct_route_to_consumer_qm"
            decision["target_route_type"] = "cross_qm_canonical"
            decision["reasons"].append("deterministic_target_routing")
        else:
            decision["strategy"] = "canonicalize_local_flow"
            decision["target_route_type"] = "local_only"
            decision["reasons"].append("default_local_canonicalization")

    return finalize_decision(decision)


def finalize_decision(decision):
    """
    Deduplicate and stabilize one decision object.
    """
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
	
flows = extract_flows_refined(df)
nodes, physical_edges, logical_edges = build_qm_graph_data(flows)
matrix = build_complexity_matrix(flows, nodes, physical_edges, logical_edges)

flow_metrics = matrix["flows"]
node_metrics = matrix["nodes"]

routing_analysis = {}  # or your existing routing analysis output

optimization_decisions = build_optimization_decisions(
    flows=flows,
    flow_metrics=flow_metrics,
    node_metrics=node_metrics,
    routing_analysis=routing_analysis,
    nodes=nodes,
)
