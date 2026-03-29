import pandas as pd

def normalize_q_type(value: str) -> str:
    """
    Normalize queue type values like:
    'Remote', 'Alias', 'Remote;Alias', 'Local'
    """
    if pd.isna(value):
        return ""
    parts = [p.strip() for p in str(value).split(";") if p.strip()]
    return ";".join(sorted(set(parts)))


def pick_single_or_list(values):
    """
    Return:
    - single value if exactly one unique non-empty value
    - sorted list if multiple
    - "" if none
    """
    vals = sorted(set(v for v in values if str(v).strip() != ""))
    if len(vals) == 0:
        return ""
    if len(vals) == 1:
        return vals[0]
    return vals


def extract_flows_refined(df: pd.DataFrame) -> dict:
    """
    Build one refined flow object per producer_app -> consumer_app relationship.

    Key ideas:
    - producer_home_qm is derived from producer rows, ignoring alias-only routing rows
    - consumer_home_qm is derived from consumer rows, ignoring alias-only routing rows
    - routing_target_qm is derived from remote_q_mgr_name on remote rows
    - source_queues come from producer-side remote/local rows
    - target_queues come from remote_q_name and destination alias/local rows
    """

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

        # -----------------------------
        # 1. Derive producer home QM
        # -----------------------------
        # Prefer producer rows with Remote or Local.
        producer_home_candidates = producer_rows[
            producer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
        ]["queue_manager_name"].tolist()

        # Fallback if needed
        if not producer_home_candidates:
            producer_home_candidates = producer_rows["queue_manager_name"].tolist()

        producer_home_qm = pick_single_or_list(producer_home_candidates)

        # -----------------------------
        # 2. Derive consumer home QM
        # -----------------------------
        # Prefer consumer rows with Remote or Local.
        consumer_home_candidates = consumer_rows[
            consumer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
        ]["queue_manager_name"].tolist()

        # Fallback if needed
        if not consumer_home_candidates:
            consumer_home_candidates = consumer_rows["queue_manager_name"].tolist()

        consumer_home_qm = pick_single_or_list(consumer_home_candidates)

        # -----------------------------
        # 3. Derive routing target QM
        # -----------------------------
        routing_target_candidates = remote_rows["remote_q_mgr_name"].tolist()

        # For local-only flows, target is same as producer home QM
        if not [x for x in routing_target_candidates if x.strip()]:
            routing_target_qm = producer_home_qm
        else:
            routing_target_qm = pick_single_or_list(routing_target_candidates)

        # -----------------------------
        # 4. Source queues
        # -----------------------------
        # Producer-side queues that initiate the flow.
        source_queues = sorted(
            set(
                producer_rows[
                    producer_rows["q_type_norm"].str.contains("Remote|Local", case=False, na=False)
                ]["Discrete Queue Name"]
            ) - {""}
        )

        # -----------------------------
        # 5. Target queues
        # -----------------------------
        # Target queue names come from remote_q_name on remote rows,
        # plus destination alias/local objects if present.
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

        # -----------------------------
        # 6. Queue types
        # -----------------------------
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

        # For local-only flows, if target types are empty, use Local
        if not target_queue_types and not remote_rows.empty is True and not local_rows.empty:
            target_queue_types = ["Local"]

        # -----------------------------
        # 7. Queue pairs
        # -----------------------------
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

        # -----------------------------
        # 8. QM path
        # -----------------------------
        if producer_home_qm == routing_target_qm:
            qm_path = [producer_home_qm]
        else:
            qm_path = [producer_home_qm, routing_target_qm]

        # -----------------------------
        # 9. Flags and issues
        # -----------------------------
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

flows = extract_flows_refined(df)

for flow_id, flow in flows.items():
    print("=" * 80)
    print(flow_id)
    for k, v in flow.items():
        print(f"{k}: {v}")
