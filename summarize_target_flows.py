import re


# ------------------------------------------------------------
# 1. NAMING HELPERS
# ------------------------------------------------------------

def sanitize_name(value: str) -> str:
    """
    Convert names into deterministic MQ-friendly tokens.
    """
    value = str(value).strip().upper()
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "UNKNOWN"


def default_target_qm_name(app_id: str) -> str:
    """
    Deterministic target QM naming rule: one QM per app.
    """
    return f"QM_{sanitize_name(app_id)}"


def default_local_out_queue(app_id: str, consumer_app: str) -> str:
    return f"LQ_{sanitize_name(app_id)}_TO_{sanitize_name(consumer_app)}_OUT"


def default_local_in_queue(producer_app: str, consumer_app: str) -> str:
    return f"LQ_{sanitize_name(producer_app)}_TO_{sanitize_name(consumer_app)}_IN"


def default_remote_queue(producer_app: str, consumer_app: str) -> str:
    return f"RQ_{sanitize_name(producer_app)}_TO_{sanitize_name(consumer_app)}"


def default_xmit_queue(producer_qm: str, consumer_qm: str) -> str:
    return f"XMIT_{sanitize_name(producer_qm)}_TO_{sanitize_name(consumer_qm)}"


def default_channel_name(producer_qm: str, consumer_qm: str) -> str:
    return f"{sanitize_name(producer_qm)}_TO_{sanitize_name(consumer_qm)}"


# ------------------------------------------------------------
# 2. CONSTRAINT CHECKS FOR TARGET DESIGN
# ------------------------------------------------------------

def validate_target_flow(target_flow: dict) -> list:
    """
    Validate major hackathon constraints on the target flow.
    Returns list of validation errors.
    """
    errors = []

    producer_qm = target_flow["producer_target_qm"]
    consumer_qm = target_flow["consumer_target_qm"]
    routing_qm = target_flow["routing_target_qm"]

    # one QM per app / deterministic ownership
    if not producer_qm:
        errors.append("missing_producer_target_qm")
    if not consumer_qm:
        errors.append("missing_consumer_target_qm")

    # producer routes only toward consumer QM in target
    if target_flow["is_cross_qm"] and routing_qm != consumer_qm:
        errors.append("routing_target_not_equal_consumer_target_qm")

    # producer local queue should exist
    if not target_flow["producer_local_queue"]:
        errors.append("missing_producer_local_queue")

    # consumer local queue should exist
    if not target_flow["consumer_local_queue"]:
        errors.append("missing_consumer_local_queue")

    # if cross-qm, remote/xmit/channel must exist
    if target_flow["is_cross_qm"]:
        if not target_flow["remote_queue"]:
            errors.append("missing_remote_queue")
        if not target_flow["xmit_queue"]:
            errors.append("missing_xmit_queue")
        if not target_flow["sender_channel"]:
            errors.append("missing_sender_channel")
        if not target_flow["receiver_channel"]:
            errors.append("missing_receiver_channel")

    return errors


# ------------------------------------------------------------
# 3. FLOW TRANSFORMATION LOGIC
# ------------------------------------------------------------

def transform_flow_to_target(flow: dict, qm_name_func=default_target_qm_name) -> dict:
    """
    Transform one AS-IS flow into a TARGET flow object.

    Main rules:
    - one QM per app
    - consumer reads from local queue on consumer QM
    - deterministic routing producer QM -> consumer QM
    - alias removed from target
    - local-only stays local
    """
    producer_app = flow["producer_app"]
    consumer_app = flow["consumer_app"]

    producer_target_qm = qm_name_func(producer_app)
    consumer_target_qm = qm_name_func(consumer_app)

    is_same_qm = producer_target_qm == consumer_target_qm
    is_cross_qm = not is_same_qm

    producer_local_queue = default_local_out_queue(producer_app, consumer_app)
    consumer_local_queue = default_local_in_queue(producer_app, consumer_app)

    if is_cross_qm:
        routing_target_qm = consumer_target_qm
        remote_queue = default_remote_queue(producer_app, consumer_app)
        xmit_queue = default_xmit_queue(producer_target_qm, consumer_target_qm)
        channel_name = default_channel_name(producer_target_qm, consumer_target_qm)
        sender_channel = channel_name
        receiver_channel = channel_name
    else:
        routing_target_qm = producer_target_qm
        remote_queue = ""
        xmit_queue = ""
        sender_channel = ""
        receiver_channel = ""

    transformation_actions = []

    # local-only flows remain local
    if flow["has_local_only"]:
        transformation_actions.append("preserve_local_only_pattern")
    else:
        transformation_actions.append("normalize_to_deterministic_qm_to_qm_routing")

    # alias removal
    if flow["has_alias"]:
        transformation_actions.append("remove_alias_from_target")

    # incomplete routing or non-aligned routing in AS-IS becomes direct producer->consumer target routing
    if "cross_qm_flow" in flow["issues"]:
        transformation_actions.append("replace_legacy_routing_with_direct_target_qm_routing")

    # fix ambiguous ownership
    if "multiple_producer_home_qms" in flow["issues"]:
        transformation_actions.append("collapse_multiple_producer_home_qms_to_single_target_qm")
    if "multiple_consumer_home_qms" in flow["issues"]:
        transformation_actions.append("collapse_multiple_consumer_home_qms_to_single_target_qm")
    if "multiple_routing_target_qms" in flow["issues"]:
        transformation_actions.append("replace_multiple_routing_targets_with_single_consumer_target_qm")

    # handle missing producer/consumer explicitly but still produce a target shell
    if "missing_producer" in flow["issues"]:
        transformation_actions.append("flag_missing_producer_for_manual_review")
    if "missing_consumer" in flow["issues"]:
        transformation_actions.append("flag_missing_consumer_for_manual_review")
    if "missing_home_qm" in flow["issues"]:
        transformation_actions.append("assign_target_qm_by_app_id_due_to_missing_home_qm")

    target_flow = {
        "flow_id": flow["flow_id"],

        # app-level identities
        "producer_app": producer_app,
        "consumer_app": consumer_app,
        "producer_name": flow["producer_name"],
        "consumer_name": flow["consumer_name"],

        # as-is references (kept for traceability)
        "as_is_producer_home_qm": flow["producer_home_qm"],
        "as_is_consumer_home_qm": flow["consumer_home_qm"],
        "as_is_routing_target_qm": flow["routing_target_qm"],
        "as_is_source_queues": flow["source_queues"],
        "as_is_target_queues": flow["target_queues"],
        "as_is_queue_pairs": flow["queue_pairs"],
        "as_is_record_ids": flow["record_ids"],
        "as_is_record_count": flow["record_count"],
        "as_is_issues": list(flow["issues"]),

        # target ownership
        "producer_target_qm": producer_target_qm,
        "consumer_target_qm": consumer_target_qm,
        "routing_target_qm": routing_target_qm,

        # target MQ objects
        "producer_local_queue": producer_local_queue,
        "consumer_local_queue": consumer_local_queue,
        "remote_queue": remote_queue,
        "xmit_queue": xmit_queue,
        "sender_channel": sender_channel,
        "receiver_channel": receiver_channel,

        # flags
        "is_cross_qm": is_cross_qm,
        "is_local_only_target": not is_cross_qm,

        # explanation
        "transformation_actions": transformation_actions,
        "transformation_reasoning": [
            "one_qm_per_app_enforced",
            "applications_connect_only_to_own_qm",
            "consumer_reads_from_local_queue",
            "deterministic_routing_applied",
        ],
    }

    validation_errors = validate_target_flow(target_flow)
    target_flow["constraint_validation_errors"] = validation_errors
    target_flow["constraint_valid"] = len(validation_errors) == 0

    return target_flow


# ------------------------------------------------------------
# 4. TRANSFORM ALL FLOWS
# ------------------------------------------------------------

def transform_all_flows(flows: dict, qm_name_func=default_target_qm_name) -> dict:
    """
    Transform all AS-IS flows into TARGET flows.
    """
    target_flows = {}

    for flow_id, flow in flows.items():
        target_flows[flow_id] = transform_flow_to_target(flow, qm_name_func=qm_name_func)

    return target_flows


# ------------------------------------------------------------
# 5. BUILD TARGET GRAPH DATA (NO NETWORKX REQUIRED)
# ------------------------------------------------------------

def build_target_graph_data(target_flows: dict):
    """
    Build simplified target graph data from target flows.
    Returns:
      target_nodes, target_edges
    """
    target_nodes = defaultdict(lambda: {
        "hosted_apps": set(),
        "produced_flows": set(),
        "consumed_flows": set(),
        "local_only_flows": set(),
    })

    target_edges = defaultdict(lambda: {
        "flow_ids": set(),
        "producer_apps": set(),
        "consumer_apps": set(),
        "record_count": 0,
        "requires_xmit": False,
        "requires_channel": False,
    })

    for flow_id, tf in target_flows.items():
        p_qm = tf["producer_target_qm"]
        c_qm = tf["consumer_target_qm"]

        target_nodes[p_qm]["hosted_apps"].add(tf["producer_app"])
        target_nodes[p_qm]["produced_flows"].add(flow_id)

        target_nodes[c_qm]["hosted_apps"].add(tf["consumer_app"])
        target_nodes[c_qm]["consumed_flows"].add(flow_id)

        if tf["is_local_only_target"]:
            target_nodes[p_qm]["local_only_flows"].add(flow_id)
        else:
            edge = (p_qm, c_qm)
            target_edges[edge]["flow_ids"].add(flow_id)
            target_edges[edge]["producer_apps"].add(tf["producer_app"])
            target_edges[edge]["consumer_apps"].add(tf["consumer_app"])
            target_edges[edge]["record_count"] += tf["as_is_record_count"]
            target_edges[edge]["requires_xmit"] = True
            target_edges[edge]["requires_channel"] = True

    # normalize
    target_nodes_norm = {}
    for qm, meta in target_nodes.items():
        target_nodes_norm[qm] = {
            "hosted_apps": sorted(meta["hosted_apps"]),
            "produced_flows": sorted(meta["produced_flows"]),
            "consumed_flows": sorted(meta["consumed_flows"]),
            "local_only_flows": sorted(meta["local_only_flows"]),
        }

    target_edges_norm = {}
    for edge, meta in target_edges.items():
        target_edges_norm[edge] = {
            "flow_ids": sorted(meta["flow_ids"]),
            "producer_apps": sorted(meta["producer_apps"]),
            "consumer_apps": sorted(meta["consumer_apps"]),
            "record_count": meta["record_count"],
            "requires_xmit": meta["requires_xmit"],
            "requires_channel": meta["requires_channel"],
        }

    return target_nodes_norm, target_edges_norm


# ------------------------------------------------------------
# 6. OPTIONAL SUMMARY / PRINT HELPERS
# ------------------------------------------------------------

def summarize_target_flows(target_flows: dict):
    for flow_id, tf in target_flows.items():
        print("=" * 100)
        print(flow_id)
        print(f"producer_target_qm: {tf['producer_target_qm']}")
        print(f"consumer_target_qm: {tf['consumer_target_qm']}")
        print(f"routing_target_qm: {tf['routing_target_qm']}")
        print(f"producer_local_queue: {tf['producer_local_queue']}")
        print(f"consumer_local_queue: {tf['consumer_local_queue']}")
        print(f"remote_queue: {tf['remote_queue']}")
        print(f"xmit_queue: {tf['xmit_queue']}")
        print(f"sender_channel: {tf['sender_channel']}")
        print(f"receiver_channel: {tf['receiver_channel']}")
        print(f"is_cross_qm: {tf['is_cross_qm']}")
        print(f"transformation_actions: {tf['transformation_actions']}")
        print(f"constraint_valid: {tf['constraint_valid']}")
        print(f"constraint_validation_errors: {tf['constraint_validation_errors']}")
