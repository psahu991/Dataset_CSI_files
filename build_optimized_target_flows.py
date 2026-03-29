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

def apply_optimization_decision(flow, decision):
    producer_app = flow["producer_app"]
    consumer_app = flow["consumer_app"]

    producer_target_qm = target_qm_for_app(producer_app)
    consumer_target_qm = target_qm_for_app(consumer_app)

    producer_local_queue = make_queue_name("LQ_OUT", producer_app, consumer_app)
    consumer_local_queue = make_queue_name("LQ_IN", producer_app, consumer_app)

    # Manual review shell
    if decision["manual_review"]:
        return {
            "flow_id": flow["flow_id"],
            "producer_app": producer_app,
            "consumer_app": consumer_app,
            "producer_target_qm": producer_target_qm,
            "consumer_target_qm": consumer_target_qm,
            "routing_target_qm": consumer_target_qm,
            "producer_local_queue": producer_local_queue,
            "consumer_local_queue": consumer_local_queue,
            "remote_queue": "",
            "xmit_queue": "",
            "sender_channel": "",
            "receiver_channel": "",
            "manual_review": True,
            "optimization_strategy": decision["strategy"],
            "optimization_reasons": decision["reasons"],
            "removed_intermediate_qms": decision["remove_intermediate_qms"],
        }

    # Strict target rule: one QM per app
    is_cross_qm = producer_target_qm != consumer_target_qm

    if is_cross_qm:
        routing_target_qm = consumer_target_qm
        remote_queue = make_queue_name("RQ", producer_app, consumer_app)
        xmit_queue = f"XMIT_{sanitize_name(producer_target_qm)}_TO_{sanitize_name(consumer_target_qm)}"
        channel = make_channel_name(producer_target_qm, consumer_target_qm)
        sender_channel = channel
        receiver_channel = channel
    else:
        routing_target_qm = producer_target_qm
        remote_queue = ""
        xmit_queue = ""
        sender_channel = ""
        receiver_channel = ""

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
        "manual_review": False,
        "optimization_strategy": decision["strategy"],
        "optimization_reasons": decision["reasons"],
        "removed_intermediate_qms": decision["remove_intermediate_qms"],
        "remove_alias": decision["remove_alias"],
    }
def build_optimized_target_flows(flows, decisions):
    target_flows = {}
    for flow_id, flow in flows.items():
        target_flows[flow_id] = apply_optimization_decision(flow, decisions[flow_id])
    return target_flows
