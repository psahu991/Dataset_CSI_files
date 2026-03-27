import networkx as nx
from collections import defaultdict

def build_qm_graph(flows: dict) -> nx.DiGraph:
    """
    Build a directed QM graph from refined flow objects.

    Nodes:
        Queue Managers

    Edges:
        producer_home_qm -> routing_target_qm
        only for remote / inter-QM flows

    Node metadata:
        hosted_apps
        produced_flows
        consumed_flows
        local_only_flows

    Edge metadata:
        flow_ids
        producer_apps
        consumer_apps
        source_queues
        target_queues
        queue_pairs
        has_alias
        has_remote
        record_count
        distinct_flow_count
    """

    G = nx.DiGraph()

    # -----------------------------
    # 1. First pass: collect node metadata
    # -----------------------------
    node_data = defaultdict(lambda: {
        "hosted_apps": set(),
        "produced_flows": set(),
        "consumed_flows": set(),
        "local_only_flows": set(),
    })

    for flow_id, flow in flows.items():
        producer_qm = flow["producer_home_qm"]
        consumer_qm = flow["consumer_home_qm"]

        if producer_qm:
            node_data[producer_qm]["hosted_apps"].add(flow["producer_app"])
            node_data[producer_qm]["produced_flows"].add(flow_id)

        if consumer_qm:
            node_data[consumer_qm]["hosted_apps"].add(flow["consumer_app"])
            node_data[consumer_qm]["consumed_flows"].add(flow_id)

        if flow["has_local_only"]:
            if producer_qm:
                node_data[producer_qm]["local_only_flows"].add(flow_id)

    # Add nodes to graph
    for qm, meta in node_data.items():
        G.add_node(
            qm,
            hosted_apps=sorted(meta["hosted_apps"]),
            produced_flows=sorted(meta["produced_flows"]),
            consumed_flows=sorted(meta["consumed_flows"]),
            local_only_flows=sorted(meta["local_only_flows"]),
        )

    # -----------------------------
    # 2. Second pass: build edge metadata
    # -----------------------------
    edge_data = defaultdict(lambda: {
        "flow_ids": set(),
        "producer_apps": set(),
        "consumer_apps": set(),
        "source_queues": set(),
        "target_queues": set(),
        "queue_pairs": [],
        "has_alias": False,
        "has_remote": False,
        "record_count": 0,
        "distinct_flow_count": 0,
    })

    for flow_id, flow in flows.items():
        if not flow["has_remote"]:
            continue

        src_qm = flow["producer_home_qm"]
        tgt_qm = flow["routing_target_qm"]

        # Skip malformed cases
        if not src_qm or not tgt_qm:
            continue

        edge_key = (src_qm, tgt_qm)
        edge_meta = edge_data[edge_key]

        edge_meta["flow_ids"].add(flow_id)
        edge_meta["producer_apps"].add(flow["producer_app"])
        edge_meta["consumer_apps"].add(flow["consumer_app"])
        edge_meta["source_queues"].update(flow["source_queues"])
        edge_meta["target_queues"].update(flow["target_queues"])
        edge_meta["has_alias"] = edge_meta["has_alias"] or flow["has_alias"]
        edge_meta["has_remote"] = edge_meta["has_remote"] or flow["has_remote"]
        edge_meta["record_count"] += flow["record_count"]
        edge_meta["distinct_flow_count"] += flow["distinct_flow_count"]

        # Deduplicate queue_pairs
        existing_pairs = {
            (
                qp["source_queue"],
                qp["target_queue"],
                qp["source_queue_type"],
                qp["target_queue_type"],
            )
            for qp in edge_meta["queue_pairs"]
        }

        for qp in flow["queue_pairs"]:
            pair_key = (
                qp["source_queue"],
                qp["target_queue"],
                qp["source_queue_type"],
                qp["target_queue_type"],
            )
            if pair_key not in existing_pairs:
                edge_meta["queue_pairs"].append(qp)
                existing_pairs.add(pair_key)

    # Add edges to graph
    for (src_qm, tgt_qm), meta in edge_data.items():
        G.add_edge(
            src_qm,
            tgt_qm,
            flow_ids=sorted(meta["flow_ids"]),
            producer_apps=sorted(meta["producer_apps"]),
            consumer_apps=sorted(meta["consumer_apps"]),
            source_queues=sorted(meta["source_queues"]),
            target_queues=sorted(meta["target_queues"]),
            queue_pairs=meta["queue_pairs"],
            has_alias=meta["has_alias"],
            has_remote=meta["has_remote"],
            record_count=meta["record_count"],
            distinct_flow_count=meta["distinct_flow_count"],
        )

    return G
