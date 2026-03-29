import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
from collections import defaultdict


# -------------------------------------------------------------------
# 1. HELPERS
# -------------------------------------------------------------------

def normalize_q_type(value: str) -> str:
    if pd.isna(value):
        return ""
    parts = [p.strip() for p in str(value).split(";") if p.strip()]
    return ";".join(sorted(set(parts)))


def pick_single_or_list(values):
    vals = sorted(set(v for v in values if str(v).strip() != ""))
    if len(vals) == 0:
        return ""
    if len(vals) == 1:
        return vals[0]
    return vals


def as_list(value):
    if value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


# -------------------------------------------------------------------
# 2. REFINED FLOW EXTRACTOR
# -------------------------------------------------------------------

def extract_flows_refined(df: pd.DataFrame) -> dict:
    df = df.copy()
    df.columns = df.columns.str.strip()

    required_cols = [
        "Discrete Queue Name",
        "ProducerName",
        "ConsumerName",
        "PrimaryAppRole",
        "app_id",
        "queue_manager_name",
        "q_type",
        "remote_q_mgr_name",
        "remote_q_name",
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    for col in required_cols:
        df[col] = df[col].fillna("").astype(str).str.strip()

    df["q_type_norm"] = df["q_type"].apply(normalize_q_type)
    df = df.reset_index().rename(columns={"index": "record_id"})

    grouped = df.groupby(["ProducerName", "ConsumerName"], dropna=False)
    flows = {}

    for (producer_name, consumer_name), group in grouped:
        producer_rows = group[group["PrimaryAppRole"].str.lower() == "producer"].copy()
        consumer_rows = group[group["PrimaryAppRole"].str.lower() == "consumer"].copy()

        producer_apps = sorted(set(producer_rows["app_id"]) - {""})
        consumer_apps = sorted(set(consumer_rows["app_id"]) - {""})

        producer_app = producer_apps[0] if len(producer_apps) == 1 else "|".join(producer_apps) or "UNKNOWN_PRODUCER"
        consumer_app = consumer_apps[0] if len(consumer_apps) == 1 else "|".join(consumer_apps) or "UNKNOWN_CONSUMER"

        flow_id = f"{producer_app}->{consumer_app}"

        remote_rows = group[group["q_type_norm"].str.contains("Remote", case=False, na=False)].copy()
        alias_rows = group[group["q_type_norm"].str.contains("Alias", case=False, na=False)].copy()
        local_rows = group[group["q_type_norm"].str.contains("Local", case=False, na=False)].copy()

        # Home QM inference: prefer Local/Remote, avoid Alias-only for ownership
        producer_home_candidates = producer_rows[
            producer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
        ]["queue_manager_name"].tolist()
        if not producer_home_candidates:
            producer_home_candidates = producer_rows["queue_manager_name"].tolist()

        consumer_home_candidates = consumer_rows[
            consumer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
        ]["queue_manager_name"].tolist()
        if not consumer_home_candidates:
            consumer_home_candidates = consumer_rows["queue_manager_name"].tolist()

        producer_home_qm = pick_single_or_list(producer_home_candidates)
        consumer_home_qm = pick_single_or_list(consumer_home_candidates)

        routing_target_candidates = remote_rows["remote_q_mgr_name"].tolist()
        if not [x for x in routing_target_candidates if x.strip()]:
            routing_target_qm = producer_home_qm
        else:
            routing_target_qm = pick_single_or_list(routing_target_candidates)

        source_queues = sorted(
            set(
                producer_rows[
                    producer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
                ]["Discrete Queue Name"]
            ) - {""}
        )

        target_queues = sorted(
            (
                set(remote_rows["remote_q_name"]) |
                set(alias_rows["Discrete Queue Name"]) |
                set(
                    consumer_rows[
                        consumer_rows["q_type_norm"].str.contains("Local", case=False, na=False)
                    ]["Discrete Queue Name"]
                )
            ) - {""}
        )

        source_queue_types = sorted(
            set(
                producer_rows[
                    producer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
                ]["q_type_norm"]
            ) - {""}
        )

        target_queue_types = sorted(
            (
                set(alias_rows["q_type_norm"]) |
                set(
                    consumer_rows[
                        consumer_rows["q_type_norm"].str.contains("Local", case=False, na=False)
                    ]["q_type_norm"]
                )
            ) - {""}
        )

        if not target_queue_types and (not remote_rows.empty is True) and (not local_rows.empty):
            target_queue_types = ["Local"]

        queue_pairs_seen = set()
        queue_pairs = []

        alias_queue_names = set(alias_rows["Discrete Queue Name"]) - {""}

        for _, row in remote_rows.iterrows():
            src_q = row["Discrete Queue Name"]
            tgt_q = row["remote_q_name"]
            pair_key = (src_q, tgt_q)
            if pair_key in queue_pairs_seen:
                continue
            queue_pairs_seen.add(pair_key)

            target_q_type = "Alias" if tgt_q in alias_queue_names else "Unknown"

            queue_pairs.append({
                "source_queue": src_q,
                "target_queue": tgt_q,
                "source_queue_type": row["q_type_norm"],
                "target_queue_type": target_q_type
            })

        # QM path: only flat and conservative
        if producer_home_qm == routing_target_qm:
            qm_path = [producer_home_qm] if producer_home_qm else []
        else:
            qm_path = [producer_home_qm, routing_target_qm]

        has_remote = not remote_rows.empty
        has_alias = not alias_rows.empty
        has_local_only = (not local_rows.empty and remote_rows.empty)

        issues = []
        if has_remote:
            issues.append("cross_qm_flow")
        if has_alias:
            issues.append("target_alias_present")
        if has_local_only:
            issues.append("local_only_flow")
        if producer_app == "UNKNOWN_PRODUCER":
            issues.append("missing_producer")
        if consumer_app == "UNKNOWN_CONSUMER":
            issues.append("missing_consumer")
        if producer_home_qm == "" or consumer_home_qm == "":
            issues.append("missing_home_qm")
        if isinstance(producer_home_qm, list):
            issues.append("multiple_producer_home_qms")
        if isinstance(consumer_home_qm, list):
            issues.append("multiple_consumer_home_qms")
        if isinstance(routing_target_qm, list):
            issues.append("multiple_routing_target_qms")

        flows[flow_id] = {
            "flow_id": flow_id,
            "producer_app": producer_app,
            "consumer_app": consumer_app,
            "producer_name": producer_name,
            "consumer_name": consumer_name,

            "producer_home_qm": producer_home_qm,
            "consumer_home_qm": consumer_home_qm,
            "routing_target_qm": routing_target_qm,

            "source_queues": source_queues,
            "target_queues": target_queues,

            "source_queue_types": source_queue_types,
            "target_queue_types": target_queue_types,

            "has_remote": has_remote,
            "has_alias": has_alias,
            "has_local_only": has_local_only,

            "record_ids": sorted(group["record_id"].tolist()),
            "record_count": int(len(group)),

            "qm_path": qm_path,
            "queue_pairs": queue_pairs,
            "distinct_flow_count": len(queue_pairs) if queue_pairs else 1,

            "issues": issues,
        }

    return flows


# -------------------------------------------------------------------
# 3. BUILD PHYSICAL + LOGICAL + COMBINED QM GRAPHS
# -------------------------------------------------------------------

def build_qm_graphs(flows: dict):
    """
    Returns:
        physical_graph: actual remote routing edges from data
        logical_graph: producer_home_qm -> consumer_home_qm
        combined_graph: both edge types together
    """

    physical_graph = nx.DiGraph()
    logical_graph = nx.DiGraph()
    combined_graph = nx.DiGraph()

    # -----------------------------
    # Collect node metadata
    # -----------------------------
    node_data = defaultdict(lambda: {
        "hosted_apps": set(),
        "produced_flows": set(),
        "consumed_flows": set(),
        "local_only_flows": set(),
        "routing_only": False,
    })

    for flow_id, flow in flows.items():
        prod_qms = as_list(flow["producer_home_qm"])
        cons_qms = as_list(flow["consumer_home_qm"])
        route_qms = as_list(flow["routing_target_qm"])

        for qm in prod_qms:
            node_data[qm]["hosted_apps"].add(flow["producer_app"])
            node_data[qm]["produced_flows"].add(flow_id)

        for qm in cons_qms:
            node_data[qm]["hosted_apps"].add(flow["consumer_app"])
            node_data[qm]["consumed_flows"].add(flow_id)

        for qm in route_qms:
            if qm not in prod_qms and qm not in cons_qms:
                node_data[qm]["routing_only"] = True

        if flow["has_local_only"]:
            for qm in prod_qms:
                node_data[qm]["local_only_flows"].add(flow_id)

    # Add all nodes
    for qm, meta in node_data.items():
        for G in (physical_graph, logical_graph, combined_graph):
            G.add_node(
                qm,
                hosted_apps=sorted(meta["hosted_apps"]),
                produced_flows=sorted(meta["produced_flows"]),
                consumed_flows=sorted(meta["consumed_flows"]),
                local_only_flows=sorted(meta["local_only_flows"]),
                routing_only=meta["routing_only"],
            )

    # -----------------------------
    # Build PHYSICAL edges
    # -----------------------------
    physical_edge_data = defaultdict(lambda: {
        "flow_ids": set(),
        "producer_apps": set(),
        "consumer_apps": set(),
        "source_queues": set(),
        "target_queues": set(),
        "queue_pairs": [],
        "has_alias": False,
        "record_count": 0,
        "distinct_flow_count": 0,
        "edge_type": "physical",
    })

    for flow_id, flow in flows.items():
        if not flow["has_remote"]:
            continue

        src_candidates = as_list(flow["producer_home_qm"])
        tgt_candidates = as_list(flow["routing_target_qm"])

        for src_qm in src_candidates:
            for tgt_qm in tgt_candidates:
                if not src_qm or not tgt_qm:
                    continue

                meta = physical_edge_data[(src_qm, tgt_qm)]
                meta["flow_ids"].add(flow_id)
                meta["producer_apps"].add(flow["producer_app"])
                meta["consumer_apps"].add(flow["consumer_app"])
                meta["source_queues"].update(flow["source_queues"])
                meta["target_queues"].update(flow["target_queues"])
                meta["has_alias"] = meta["has_alias"] or flow["has_alias"]
                meta["record_count"] += flow["record_count"]
                meta["distinct_flow_count"] += flow["distinct_flow_count"]

                existing = {
                    (qp["source_queue"], qp["target_queue"], qp["source_queue_type"], qp["target_queue_type"])
                    for qp in meta["queue_pairs"]
                }
                for qp in flow["queue_pairs"]:
                    key = (qp["source_queue"], qp["target_queue"], qp["source_queue_type"], qp["target_queue_type"])
                    if key not in existing:
                        meta["queue_pairs"].append(qp)
                        existing.add(key)

    for (u, v), meta in physical_edge_data.items():
        physical_graph.add_edge(
            u, v,
            flow_ids=sorted(meta["flow_ids"]),
            producer_apps=sorted(meta["producer_apps"]),
            consumer_apps=sorted(meta["consumer_apps"]),
            source_queues=sorted(meta["source_queues"]),
            target_queues=sorted(meta["target_queues"]),
            queue_pairs=meta["queue_pairs"],
            has_alias=meta["has_alias"],
            record_count=meta["record_count"],
            distinct_flow_count=meta["distinct_flow_count"],
            edge_type="physical",
        )

    # -----------------------------
    # Build LOGICAL edges
    # -----------------------------
    logical_edge_data = defaultdict(lambda: {
        "flow_ids": set(),
        "producer_apps": set(),
        "consumer_apps": set(),
        "record_count": 0,
        "edge_type": "logical",
    })

    for flow_id, flow in flows.items():
        prod_qms = as_list(flow["producer_home_qm"])
        cons_qms = as_list(flow["consumer_home_qm"])

        for src_qm in prod_qms:
            for tgt_qm in cons_qms:
                if not src_qm or not tgt_qm:
                    continue

                meta = logical_edge_data[(src_qm, tgt_qm)]
                meta["flow_ids"].add(flow_id)
                meta["producer_apps"].add(flow["producer_app"])
                meta["consumer_apps"].add(flow["consumer_app"])
                meta["record_count"] += flow["record_count"]

    for (u, v), meta in logical_edge_data.items():
        logical_graph.add_edge(
            u, v,
            flow_ids=sorted(meta["flow_ids"]),
            producer_apps=sorted(meta["producer_apps"]),
            consumer_apps=sorted(meta["consumer_apps"]),
            record_count=meta["record_count"],
            edge_type="logical",
        )

    # -----------------------------
    # Build COMBINED graph
    # -----------------------------
    for node, data in physical_graph.nodes(data=True):
        combined_graph.add_node(node, **data)
    for node, data in logical_graph.nodes(data=True):
        if node not in combined_graph:
            combined_graph.add_node(node, **data)

    # physical edges
    for u, v, data in physical_graph.edges(data=True):
        combined_graph.add_edge(u, v, **data)

    # logical edges: if same edge already exists physically, keep as overlay flag
    for u, v, data in logical_graph.edges(data=True):
        if combined_graph.has_edge(u, v):
            combined_graph[u][v]["has_logical_overlay"] = True
            combined_graph[u][v]["logical_flow_ids"] = data["flow_ids"]
        else:
            combined_graph.add_edge(
                u, v,
                **data,
                is_overlay_only=True
            )

    return physical_graph, logical_graph, combined_graph


# -------------------------------------------------------------------
# 4. ANALYSIS: INCOMPLETE ROUTING / UNREACHABLE CONSUMER
# -------------------------------------------------------------------

def analyze_flow_routing(flows: dict, physical_graph: nx.DiGraph):
    """
    Returns per-flow routing analysis.
    """
    results = {}

    for flow_id, flow in flows.items():
        producer_qms = as_list(flow["producer_home_qm"])
        consumer_qms = as_list(flow["consumer_home_qm"])
        routing_qms = as_list(flow["routing_target_qm"])

        analysis = {
            "flow_id": flow_id,
            "producer_qms": producer_qms,
            "consumer_qms": consumer_qms,
            "routing_qms": routing_qms,
            "routing_aligned": False,
            "consumer_reachable_from_routing": False,
            "missing_links": [],
            "analysis_issues": [],
        }

        if flow["has_local_only"]:
            analysis["routing_aligned"] = True
            analysis["consumer_reachable_from_routing"] = True
            results[flow_id] = analysis
            continue

        # direct alignment
        if set(routing_qms) & set(consumer_qms):
            analysis["routing_aligned"] = True
            analysis["consumer_reachable_from_routing"] = True
        else:
            # try path existence in physical graph
            reachable = False
            for rq in routing_qms:
                for cq in consumer_qms:
                    if rq in physical_graph.nodes and cq in physical_graph.nodes:
                        if nx.has_path(physical_graph, rq, cq):
                            reachable = True
                            break
                if reachable:
                    break

            analysis["consumer_reachable_from_routing"] = reachable

            if not reachable:
                analysis["analysis_issues"].append("incomplete_routing_path")
                for rq in routing_qms:
                    for cq in consumer_qms:
                        if rq != cq:
                            analysis["missing_links"].append((rq, cq))

        if not analysis["routing_aligned"]:
            analysis["analysis_issues"].append("routing_not_aligned_with_consumer")

        results[flow_id] = analysis

    return results


# -------------------------------------------------------------------
# 5. VISUALIZATION
# -------------------------------------------------------------------

def visualize_combined_qm_graph(combined_graph: nx.DiGraph, title="Combined QM Graph"):
    """
    Solid edges = physical routing from data
    Dashed edges = logical producer->consumer QM relationship
    """
    plt.figure(figsize=(14, 8))

    # Spring layout works for general cases
    pos = nx.spring_layout(combined_graph, seed=42)

    # Node labels
    node_labels = {}
    for node, data in combined_graph.nodes(data=True):
        apps = ", ".join(data.get("hosted_apps", [])) if data.get("hosted_apps") else "None"
        routing_only = "Yes" if data.get("routing_only", False) else "No"
        node_labels[node] = f"{node}\nApps: {apps}\nRouting-only: {routing_only}"

    # Draw nodes
    nx.draw_networkx_nodes(combined_graph, pos, node_size=7000)
    nx.draw_networkx_labels(combined_graph, pos, labels=node_labels, font_size=9)

    # Separate edge styles
    physical_edges = []
    logical_only_edges = []

    for u, v, data in combined_graph.edges(data=True):
        if data.get("edge_type") == "physical":
            physical_edges.append((u, v))
        elif data.get("edge_type") == "logical":
            logical_only_edges.append((u, v))

    # Physical edges = solid
    nx.draw_networkx_edges(
        combined_graph,
        pos,
        edgelist=physical_edges,
        width=2.5,
        arrows=True,
        arrowstyle='-|>',
        arrowsize=20,
    )

    # Logical-only edges = dashed
    nx.draw_networkx_edges(
        combined_graph,
        pos,
        edgelist=logical_only_edges,
        width=2.0,
        style="dashed",
        arrows=True,
        arrowstyle='-|>',
        arrowsize=20,
    )

    # Edge labels
    edge_labels = {}
    for u, v, data in combined_graph.edges(data=True):
        if data.get("edge_type") == "physical":
            edge_labels[(u, v)] = (
                f"PHYSICAL\n"
                f"Flows: {', '.join(data.get('flow_ids', []))}\n"
                f"Alias: {'Yes' if data.get('has_alias', False) else 'No'}"
            )
        else:
            edge_labels[(u, v)] = (
                f"LOGICAL\n"
                f"Flows: {', '.join(data.get('flow_ids', []))}"
            )

    nx.draw_networkx_edge_labels(
        combined_graph,
        pos,
        edge_labels=edge_labels,
        font_size=8,
        rotate=False
    )

    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.show()


# -------------------------------------------------------------------
# 6. OPTIONAL: VISUALIZE PHYSICAL + LOGICAL SEPARATELY
# -------------------------------------------------------------------

def visualize_qm_graph(graph: nx.DiGraph, title="QM Graph"):
    plt.figure(figsize=(12, 7))
    pos = nx.spring_layout(graph, seed=42)

    node_labels = {}
    for node, data in graph.nodes(data=True):
        apps = ", ".join(data.get("hosted_apps", [])) if data.get("hosted_apps") else "None"
        node_labels[node] = f"{node}\nApps: {apps}"

    nx.draw_networkx_nodes(graph, pos, node_size=6000)
    nx.draw_networkx_labels(graph, pos, labels=node_labels, font_size=9)
    nx.draw_networkx_edges(graph, pos, width=2, arrows=True, arrowstyle='-|>', arrowsize=20)

    edge_labels = {}
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") == "physical":
            edge_labels[(u, v)] = f"{', '.join(data.get('flow_ids', []))}\nAlias={data.get('has_alias', False)}"
        else:
            edge_labels[(u, v)] = f"{', '.join(data.get('flow_ids', []))}"

    nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, font_size=8, rotate=False)

    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.show()


# -------------------------------------------------------------------
# 7. HOW TO RUN
# -------------------------------------------------------------------

# Example:
# df = pd.read_csv("input.csv")
# flows = extract_flows_refined(df)
# physical_graph, logical_graph, combined_graph = build_qm_graphs(flows)
# routing_analysis = analyze_flow_routing(flows, physical_graph)
#
# print("FLOWS")
# for k, v in flows.items():
#     print("=" * 80)
#     print(k)
#     for kk, vv in v.items():
#         print(f"{kk}: {vv}")
#
# print("\nROUTING ANALYSIS")
# for k, v in routing_analysis.items():
#     print("=" * 80)
#     print(k, v)
#
# visualize_qm_graph(physical_graph, "Physical QM Graph")
# visualize_qm_graph(logical_graph, "Logical QM Graph")
# visualize_combined_qm_graph(combined_graph, "Combined QM Graph")
