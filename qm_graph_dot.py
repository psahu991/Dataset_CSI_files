import pandas as pd
from collections import defaultdict


# ------------------------------------------------------------
# 1. HELPERS
# ------------------------------------------------------------

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


def dot_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


# ------------------------------------------------------------
# 2. REFINED FLOW EXTRACTOR
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# 3. BUILD GRAPH DATA STRUCTURES (WITHOUT NETWORKX)
# ------------------------------------------------------------

def build_qm_graph_data(flows: dict):
    node_data = defaultdict(lambda: {
        "hosted_apps": set(),
        "produced_flows": set(),
        "consumed_flows": set(),
        "local_only_flows": set(),
        "routing_only": False,
    })

    physical_edges = defaultdict(lambda: {
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

    logical_edges = defaultdict(lambda: {
        "flow_ids": set(),
        "producer_apps": set(),
        "consumer_apps": set(),
        "record_count": 0,
        "edge_type": "logical",
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

        # physical edges
        if flow["has_remote"]:
            for src_qm in prod_qms:
                for tgt_qm in route_qms:
                    if not src_qm or not tgt_qm:
                        continue

                    meta = physical_edges[(src_qm, tgt_qm)]
                    meta["flow_ids"].add(flow_id)
                    meta["producer_apps"].add(flow["producer_app"])
                    meta["consumer_apps"].add(flow["consumer_app"])
                    meta["source_queues"].update(flow["source_queues"])
                    meta["target_queues"].update(flow["target_queues"])
                    meta["has_alias"] = meta["has_alias"] or flow["has_alias"]
                    meta["record_count"] += flow["record_count"]
                    meta["distinct_flow_count"] += flow["distinct_flow_count"]

                    existing = {
                        (
                            qp["source_queue"],
                            qp["target_queue"],
                            qp["source_queue_type"],
                            qp["target_queue_type"]
                        )
                        for qp in meta["queue_pairs"]
                    }

                    for qp in flow["queue_pairs"]:
                        key = (
                            qp["source_queue"],
                            qp["target_queue"],
                            qp["source_queue_type"],
                            qp["target_queue_type"]
                        )
                        if key not in existing:
                            meta["queue_pairs"].append(qp)
                            existing.add(key)

        # logical edges
        for src_qm in prod_qms:
            for tgt_qm in cons_qms:
                if not src_qm or not tgt_qm:
                    continue
                meta = logical_edges[(src_qm, tgt_qm)]
                meta["flow_ids"].add(flow_id)
                meta["producer_apps"].add(flow["producer_app"])
                meta["consumer_apps"].add(flow["consumer_app"])
                meta["record_count"] += flow["record_count"]

    # normalize sets to sorted lists
    normalized_nodes = {}
    for qm, meta in node_data.items():
        normalized_nodes[qm] = {
            "hosted_apps": sorted(meta["hosted_apps"]),
            "produced_flows": sorted(meta["produced_flows"]),
            "consumed_flows": sorted(meta["consumed_flows"]),
            "local_only_flows": sorted(meta["local_only_flows"]),
            "routing_only": meta["routing_only"],
        }

    normalized_physical_edges = {}
    for edge, meta in physical_edges.items():
        normalized_physical_edges[edge] = {
            "flow_ids": sorted(meta["flow_ids"]),
            "producer_apps": sorted(meta["producer_apps"]),
            "consumer_apps": sorted(meta["consumer_apps"]),
            "source_queues": sorted(meta["source_queues"]),
            "target_queues": sorted(meta["target_queues"]),
            "queue_pairs": meta["queue_pairs"],
            "has_alias": meta["has_alias"],
            "record_count": meta["record_count"],
            "distinct_flow_count": meta["distinct_flow_count"],
            "edge_type": "physical",
        }

    normalized_logical_edges = {}
    for edge, meta in logical_edges.items():
        normalized_logical_edges[edge] = {
            "flow_ids": sorted(meta["flow_ids"]),
            "producer_apps": sorted(meta["producer_apps"]),
            "consumer_apps": sorted(meta["consumer_apps"]),
            "record_count": meta["record_count"],
            "edge_type": "logical",
        }

    return normalized_nodes, normalized_physical_edges, normalized_logical_edges


# ------------------------------------------------------------
# 4. DOT EXPORT
# ------------------------------------------------------------

def graph_data_to_dot(nodes: dict, physical_edges: dict, logical_edges: dict, graph_name="QMGraph") -> str:
    lines = []
    lines.append(f'digraph {graph_name} {{')
    lines.append('    rankdir=LR;')
    lines.append('    graph [fontsize=10, labelloc="t"];')
    lines.append('    node [shape=box, style="rounded,filled", fillcolor=lightblue, fontsize=10];')
    lines.append('    edge [fontsize=9];')
    lines.append('')

    # nodes
    for qm in sorted(nodes.keys()):
        meta = nodes[qm]
        apps = ", ".join(meta["hosted_apps"]) if meta["hosted_apps"] else "None"
        local_only = ", ".join(meta["local_only_flows"]) if meta["local_only_flows"] else "None"
        routing_only = "Yes" if meta["routing_only"] else "No"

        label = (
            f"{qm}\\n"
            f"Apps: {apps}\\n"
            f"Local-only: {local_only}\\n"
            f"Routing-only: {routing_only}"
        )

        lines.append(f'    "{dot_escape(qm)}" [label="{dot_escape(label)}"];')

    lines.append('')

    # physical edges = solid
    for (src, tgt), meta in sorted(physical_edges.items()):
        flow_ids = ", ".join(meta["flow_ids"]) if meta["flow_ids"] else "None"
        alias_txt = "Yes" if meta["has_alias"] else "No"
        label = (
            f"PHYSICAL\\n"
            f"Flows: {flow_ids}\\n"
            f"Alias: {alias_txt}\\n"
            f"Records: {meta['record_count']}\\n"
            f"Distinct flows: {meta['distinct_flow_count']}"
        )

        lines.append(
            f'    "{dot_escape(src)}" -> "{dot_escape(tgt)}" '
            f'[label="{dot_escape(label)}", style=solid, color=black];'
        )

    lines.append('')

    # logical edges = dashed
    for (src, tgt), meta in sorted(logical_edges.items()):
        # if already represented physically, still show logical overlay in dotted blue
        if (src, tgt) in physical_edges:
            flow_ids = ", ".join(meta["flow_ids"]) if meta["flow_ids"] else "None"
            label = f"LOGICAL\\nFlows: {flow_ids}"
            lines.append(
                f'    "{dot_escape(src)}" -> "{dot_escape(tgt)}" '
                f'[label="{dot_escape(label)}", style=dashed, color=blue, constraint=false];'
            )
        else:
            flow_ids = ", ".join(meta["flow_ids"]) if meta["flow_ids"] else "None"
            label = f"LOGICAL\\nFlows: {flow_ids}"
            lines.append(
                f'    "{dot_escape(src)}" -> "{dot_escape(tgt)}" '
                f'[label="{dot_escape(label)}", style=dashed, color=blue];'
            )

    lines.append('}')
    return "\n".join(lines)


# ------------------------------------------------------------
# 5. OPTIONAL: ROUTING ANALYSIS WITHOUT NETWORKX
# ------------------------------------------------------------

def build_adjacency_from_physical_edges(physical_edges: dict):
    adjacency = defaultdict(set)
    for (src, tgt) in physical_edges.keys():
        adjacency[src].add(tgt)
    return adjacency


def path_exists(adjacency: dict, start: str, target: str) -> bool:
    if start == target:
        return True
    visited = set()
    stack = [start]

    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for nxt in adjacency.get(node, []):
            if nxt == target:
                return True
            if nxt not in visited:
                stack.append(nxt)
    return False


def analyze_routing_without_networkx(flows: dict, physical_edges: dict):
    adjacency = build_adjacency_from_physical_edges(physical_edges)
    analysis = {}

    for flow_id, flow in flows.items():
        producer_qms = as_list(flow["producer_home_qm"])
        consumer_qms = as_list(flow["consumer_home_qm"])
        routing_qms = as_list(flow["routing_target_qm"])

        result = {
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
            result["routing_aligned"] = True
            result["consumer_reachable_from_routing"] = True
            analysis[flow_id] = result
            continue

        if set(routing_qms) & set(consumer_qms):
            result["routing_aligned"] = True
            result["consumer_reachable_from_routing"] = True
        else:
            reachable = False
            for rq in routing_qms:
                for cq in consumer_qms:
                    if path_exists(adjacency, rq, cq):
                        reachable = True
                        break
                if reachable:
                    break

            result["consumer_reachable_from_routing"] = reachable

            if not reachable:
                result["analysis_issues"].append("incomplete_routing_path")
                for rq in routing_qms:
                    for cq in consumer_qms:
                        if rq != cq:
                            result["missing_links"].append((rq, cq))

        if not result["routing_aligned"]:
            result["analysis_issues"].append("routing_not_aligned_with_consumer")

        analysis[flow_id] = result

    return analysis


# ------------------------------------------------------------
# 6. HOW TO USE
# ------------------------------------------------------------

# Example:
#
# df = pd.read_csv("input.csv")
# flows = extract_flows_refined(df)
# nodes, physical_edges, logical_edges = build_qm_graph_data(flows)
# dot_text = graph_data_to_dot(nodes, physical_edges, logical_edges, graph_name="QMGraph")
#
# print(dot_text)
#
# with open("qm_graph.dot", "w", encoding="utf-8") as f:
#     f.write(dot_text)
#
# routing_analysis = analyze_routing_without_networkx(flows, physical_edges)
# for flow_id, result in routing_analysis.items():
#     print(flow_id, result)
