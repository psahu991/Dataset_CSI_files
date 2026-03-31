,
)
def as_list(value):
    """
    Normalize scalar-or-list fields into a clean list of non-empty strings.
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if str(value).strip():
        return [str(value).strip()]
    return []


def finalize_decision(decision):
    """
    Deduplicate and stabilize one decision object.
    """
    decision["remove_intermediate_qms"] = sorted(set(decision["remove_intermediate_qms"]))
    decision["reasons"] = sorted(set(decision["reasons"]))
    return decision


def decide_flow_optimization(flow_id, flow, flow_metrics, node_metrics, routing_analysis, nodes):
    """
    Build optimization decision for one refined flow.

    Design principles:
    - Extractor describes AS-IS truth
    - This function applies TARGET constraints
    - LOCAL_COMPLETE does NOT automatically mean valid target local flow
    - Different applications must be redesigned into separate-QM target architecture
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
        "as_is_flow_type": None,
        "target_validity": None,
    }

    # ------------------------------------------------------------
    # Basic extracted values
    # ------------------------------------------------------------
    producer_app = str(flow.get("producer_app", "")).strip()
    consumer_app = str(flow.get("consumer_app", "")).strip()

    producer_qms = as_list(flow.get("producer_home_qm", ""))
    consumer_qms = as_list(flow.get("consumer_home_qm", ""))
    routing_qms = as_list(flow.get("routing_target_qm", ""))

    producer_apps = flow.get("producer_apps", [])
    consumer_apps = flow.get("consumer_apps", [])

    resolution_status = flow.get("resolution_status", "UNRESOLVED")
    ambiguity_type = flow.get("ambiguity_type")
    has_local_only = bool(flow.get("has_local_only", False))
    has_remote = bool(flow.get("has_remote", False))
    has_alias = bool(flow.get("has_alias", False))

    same_app = (
        producer_app != ""
        and consumer_app != ""
        and producer_app == consumer_app
        and not producer_app.startswith("UNKNOWN")
        and not consumer_app.startswith("UNKNOWN")
    )

    incomplete = flow_metrics.get(flow_id, {}).get("incomplete", False)

    analysis = routing_analysis.get(flow_id, {}) if routing_analysis else {}
    analysis_issues = set(analysis.get("analysis_issues", []))
    flow_issues = set(flow.get("issues", []))

    # ------------------------------------------------------------
    # 1. Hard stop cases: unknown endpoints
    # ------------------------------------------------------------
    if producer_app == "UNKNOWN_PRODUCER" or consumer_app == "UNKNOWN_CONSUMER":
        decision["strategy"] = "manual_review_shell"
        decision["manual_review"] = True
        decision["exclude_from_target"] = True
        decision["target_validity"] = "not_target_ready"
        decision["reasons"].append("unknown_endpoint")
        return finalize_decision(decision)

    # ------------------------------------------------------------
    # 2. Resolution-based gating
    # ------------------------------------------------------------
    if resolution_status in {"ORPHAN", "UNRESOLVED"}:
        decision["strategy"] = "exclude_from_target"
        decision["exclude_from_target"] = True
        decision["manual_review"] = True
        decision["target_validity"] = "not_target_ready"
        decision["reasons"].append(resolution_status.lower())
        if ambiguity_type:
            decision["reasons"].append(ambiguity_type)
        return finalize_decision(decision)

    if resolution_status in {"QM_ONLY_MATCH", "QUEUE_ONLY_MATCH", "MULTI_CANDIDATE"}:
        decision["strategy"] = "manual_review_before_target"
        decision["manual_review"] = True
        decision["exclude_from_target"] = True
        decision["target_validity"] = "not_target_ready"
        decision["reasons"].append("ambiguous_flow")
        decision["reasons"].append(resolution_status.lower())
        if ambiguity_type:
            decision["reasons"].append(ambiguity_type)
        return finalize_decision(decision)

    # ------------------------------------------------------------
    # 3. Record AS-IS flow type
    # ------------------------------------------------------------
    if has_local_only or resolution_status == "LOCAL_COMPLETE":
        decision["as_is_flow_type"] = "local_complete"
    elif resolution_status == "INDIRECT_COMPLETE":
        decision["as_is_flow_type"] = "indirect_complete"
    elif has_remote:
        decision["as_is_flow_type"] = "remote_based"
    else:
        decision["as_is_flow_type"] = "other_complete"

    # ------------------------------------------------------------
    # 4. Alias simplification
    # ------------------------------------------------------------
    if has_alias:
        decision["remove_alias"] = True
        decision["reasons"].append("target_alias_present")

    # ------------------------------------------------------------
    # 5. Ownership normalization flags
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

    if len(producer_qms) == 1:
        decision["target_producer_qm"] = producer_qms[0]

    if len(consumer_qms) == 1:
        decision["target_consumer_qm"] = consumer_qms[0]

    # ------------------------------------------------------------
    # 6. Target strategy decision
    # ------------------------------------------------------------
    # Case A: AS-IS local complete
    if has_local_only or resolution_status == "LOCAL_COMPLETE":
        decision["reasons"].append("local_complete_flow")

        if same_app:
            # Valid local target only for same-app flow
            decision["strategy"] = "canonicalize_local_flow"
            decision["target_route_type"] = "local_only"
            decision["target_validity"] = "valid_target_local"
            decision["reasons"].append("same_app_local_allowed")
        else:
            # Cross-app local flow is valid AS-IS understanding
            # but violates target constraints
            decision["strategy"] = "direct_route_to_consumer_qm"
            decision["target_route_type"] = "cross_qm_canonical"
            decision["target_validity"] = "requires_redesign"
            decision["reasons"].append("cross_app_local_flow_in_as_is")
            decision["reasons"].append("target_constraint_requires_separate_qms")

        return finalize_decision(decision)

    # Case B: Indirect complete
    if resolution_status == "INDIRECT_COMPLETE":
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["target_validity"] = "requires_redesign"
        decision["reasons"].append("indirect_complete_flow")
        decision["reasons"].append("canonical_target_redesign_required")

    # ------------------------------------------------------------
    # 7. Routing quality / incompleteness
    # ------------------------------------------------------------
    if incomplete or "incomplete_routing_path" in analysis_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["target_validity"] = "requires_redesign"
        decision["reasons"].append("incomplete_routing_path")

    if "routing_not_aligned_with_consumer" in analysis_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["target_validity"] = "requires_redesign"
        decision["reasons"].append("routing_not_aligned_with_consumer")

    if "multiple_routing_target_qms" in flow_issues:
        decision["strategy"] = "direct_route_to_consumer_qm"
        decision["target_route_type"] = "cross_qm_canonical"
        decision["target_validity"] = "requires_redesign"
        decision["manual_review"] = True
        decision["reasons"].append("multiple_routing_target_qms")

    # ------------------------------------------------------------
    # 8. Remove routing-only intermediate QMs where safe
    # ------------------------------------------------------------
    for rq in routing_qms:
        if rq in nodes and nodes[rq].get("routing_only", False):
            if rq not in consumer_qms:
                decision["remove_intermediate_qms"].append(rq)
                decision["reasons"].append("routing_only_qm")

    # ------------------------------------------------------------
    # 9. Default strategy if still unset
    # ------------------------------------------------------------
    if decision["strategy"] is None:
        if same_app and not has_remote:
            decision["strategy"] = "canonicalize_local_flow"
            decision["target_route_type"] = "local_only"
            decision["target_validity"] = "valid_target_local"
            decision["reasons"].append("default_same_app_local_canonicalization")
        else:
            decision["strategy"] = "direct_route_to_consumer_qm"
            decision["target_route_type"] = "cross_qm_canonical"
            decision["target_validity"] = "requires_redesign"
            decision["reasons"].append("deterministic_target_routing")

    return finalize_decision(decision)


def build_optimization_decisions(flows, flow_metrics, node_metrics, routing_analysis, nodes):
    """
    Build optimization decisions for all refined flows.
    """
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
