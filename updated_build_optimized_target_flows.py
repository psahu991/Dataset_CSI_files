import re


def sanitize_name(value: str) -> str:
    value = str(value).strip().upper()
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "UNKNOWN"


def target_qm_for_app(app_id: str) -> str:
    return f"QM_{sanitize_name(app_id)}"


def make_queue_name(prefix: str, producer_app: str, consumer_app: str) -> str:
    return f"{prefix}_{sanitize_name(producer_app)}_TO_{sanitize_name(consumer_app)}"


def make_channel_name(producer_qm: str, consumer_qm: str) -> str:
    return f"{sanitize_name(producer_qm)}_TO_{sanitize_name(consumer_qm)}"


def make_xmit_queue_name(producer_qm: str, consumer_qm: str) -> str:
    return f"XMIT_{sanitize_name(producer_qm)}_TO_{sanitize_name(consumer_qm)}"


def apply_optimization_decision(flow, decision):
    """
    Convert one refined AS-IS flow + one optimization decision
    into one canonical target flow object.

    Aligned to latest refined flow + latest optimization decision structure.
    """

    producer_app = flow["producer_app"]
    consumer_app = flow["consumer_app"]

    # ------------------------------------------------------------
    # 1. Determine target owning QMs
    # ------------------------------------------------------------
    producer_target_qm = decision.get("target_producer_qm") or target_qm_for_app(producer_app)
    consumer_target_qm = decision.get("target_consumer_qm") or target_qm_for_app(consumer_app)

    # Canonical queues
    producer_local_queue = make_queue_name("LQ_OUT", producer_app, consumer_app)
    consumer_local_queue = make_queue_name("LQ_IN", producer_app, consumer_app)
    remote_queue = make_queue_name("RQ", producer_app, consumer_app)

    # Decision metadata
    strategy = decision.get("strategy")
    reasons = decision.get("reasons", [])
    removed_intermediate_qms = decision.get("remove_intermediate_qms", [])
    manual_review = decision.get("manual_review", False)
    exclude_from_target = decision.get("exclude_from_target", False)
    remove_alias = decision.get("remove_alias", False)
    confidence = decision.get("confidence", flow.get("match_confidence", "LOW"))
    target_route_type = decision.get("target_route_type")

    # ------------------------------------------------------------
    # 2. Excluded flows
    # ------------------------------------------------------------
    if exclude_from_target:
        return {
            "flow_id": flow["flow_id"],
            "producer_app": producer_app,
            "consumer_app": consumer_app,

            "producer_target_qm": producer_target_qm,
            "consumer_target_qm": consumer_target_qm,
            "routing_target_qm": "",

            "producer_local_queue": "",
            "consumer_local_queue": "",
            "remote_queue": "",
            "xmit_queue": "",
            "sender_channel": "",
            "receiver_channel": "",

            "target_route_type": "excluded",
            "included_in_target": False,
            "manual_review": manual_review,
            "exclude_from_target": True,

            "optimization_strategy": strategy,
            "optimization_reasons": reasons,
            "removed_intermediate_qms": removed_intermediate_qms,
            "remove_alias": remove_alias,
            "confidence": confidence,
            "resolution_status": flow.get("resolution_status"),
        }

    # ------------------------------------------------------------
    # 3. Manual review shell
    # ------------------------------------------------------------
    # Keep structural proposal, but mark it as review-needed
    if manual_review and (
        producer_target_qm is None
        or consumer_target_qm is None
        or strategy in {"manual_review_shell", "manual_review_before_target"}
    ):
        return {
            "flow_id": flow["flow_id"],
            "producer_app": producer_app,
            "consumer_app": consumer_app,

            "producer_target_qm": producer_target_qm or "",
            "consumer_target_qm": consumer_target_qm or "",
            "routing_target_qm": consumer_target_qm or "",

            "producer_local_queue": producer_local_queue,
            "consumer_local_queue": consumer_local_queue,
            "remote_queue": remote_queue if producer_target_qm != consumer_target_qm else "",
            "xmit_queue": "",
            "sender_channel": "",
            "receiver_channel": "",

            "target_route_type": target_route_type or "manual_review",
            "included_in_target": False,
            "manual_review": True,
            "exclude_from_target": False,

            "optimization_strategy": strategy,
            "optimization_reasons": reasons,
            "removed_intermediate_qms": removed_intermediate_qms,
            "remove_alias": remove_alias,
            "confidence": confidence,
            "resolution_status": flow.get("resolution_status"),
        }

    # ------------------------------------------------------------
    # 4. Canonical target pattern
    # Strict rule: one QM per app, app connects only to own QM
    # ------------------------------------------------------------
    is_cross_qm = producer_target_qm != consumer_target_qm

    if is_cross_qm:
        routing_target_qm = consumer_target_qm
        xmit_queue = make_xmit_queue_name(producer_target_qm, consumer_target_qm)
        channel = make_channel_name(producer_target_qm, consumer_target_qm)
        sender_channel = channel
        receiver_channel = channel

        final_route_type = target_route_type or "cross_qm_canonical"

    else:
        routing_target_qm = producer_target_qm
        remote_queue = ""
        xmit_queue = ""
        sender_channel = ""
        receiver_channel = ""

        final_route_type = target_route_type or "local_only"

    # ------------------------------------------------------------
    # 5. Build target flow object
    # ------------------------------------------------------------
    return {
        "flow_id": flow["flow_id"],
        "producer_app": producer_app,
        "consumer_app": consumer_app,

        "producer_target_qm": producer_target_qm,
        "consumer_target_qm": consumer_target_qm,
        "routing_target_qm": routing_target_qm,

        "producer_local_queue": producer_local_queue,
        "consumer_local_queue": consumer_local_queue,
        "remote_queue": remote_queue,
        "xmit_queue": xmit_queue,
        "sender_channel": sender_channel,
        "receiver_channel": receiver_channel,

        "target_route_type": final_route_type,
        "included_in_target": True,
        "manual_review": manual_review,
        "exclude_from_target": False,

        "optimization_strategy": strategy,
        "optimization_reasons": reasons,
        "removed_intermediate_qms": removed_intermediate_qms,
        "remove_alias": remove_alias,
        "confidence": confidence,
        "resolution_status": flow.get("resolution_status"),
    }


def build_optimized_target_flows(flows, decisions):
    """
    Build canonical target flows from refined AS-IS flows
    and optimization decisions.
    """
    target_flows = {}

    for flow_id, flow in flows.items():
        decision = decisions.get(flow_id, {})
        target_flows[flow_id] = apply_optimization_decision(flow, decision)

    return target_flows

flows = extract_flows_refined(df)
nodes, physical_edges, logical_edges = build_qm_graph_data(flows)
matrix = build_complexity_matrix(flows, nodes, physical_edges, logical_edges)

flow_metrics = matrix["flows"]
node_metrics = matrix["nodes"]
routing_analysis = {}

optimization_decisions = build_optimization_decisions(
    flows=flows,
    flow_metrics=flow_metrics,
    node_metrics=node_metrics,
    routing_analysis=routing_analysis,
    nodes=nodes,
)

target_flows = build_optimized_target_flows(flows, optimization_decisions)
