1. Ownership conflicts (VERY IMPORTANT)

Example:

One app appears on multiple QMs
Flow shows multiple possible home QMs

Auto logic does:

normalize_producer_ownership = True

But:

👉 Which QM should be the final owner?

That decision needs:

domain reasoning
consistency across flows
sometimes business logic

2. Ambiguous but usable flows

Example:

QM_ONLY_MATCH
QUEUE_ONLY_MATCH
MULTI_CANDIDATE

Auto logic says:
👉 exclude / manual review

But expert may say:
👉 “This is 90% likely QM_A → QM_B, accept it”

3. Structural decisions (architecture judgment)

Example:

Remove routing QM?
Keep it as regional hub?
Split high fan-out QM?

Auto logic uses thresholds
Expert decides contextuall

condition1
normalize_producer_ownership == True
OR
normalize_consumer_ownership == True

condition2
resolution_status in:
    QM_ONLY_MATCH
    QUEUE_ONLY_MATCH
    MULTI_CANDIDATE

condition3	
routing_only_qm removal affects many flows

condition4
high fan-in / fan-out hotspots

+++++++++
1. Ownership conflict

This is when one app appears tied to multiple QMs.

Example

Flow says:

producer_app = APP_A
producer_home_qm = ["WQ10", "WQ12"]
consumer_app = APP_B
consumer_home_qm = "WQ20"
resolution_status = INDIRECT_COMPLETE

This means the flow is understandable, but APP_A violates:

exactly one QM per application
{
    "strategy": "direct_route_to_consumer_qm",
    "normalize_producer_ownership": True,
    "target_consumer_qm": "WQ20",
    "manual_review": True,
    "reasons": ["ambiguous_producer_home_qm", "canonical_target_redesign_required"]
}
Expert decision

You decide which QM should own APP_A.
expert_decisions = {
    "APP_A->APP_B": {
        "target_producer_qm": "WQ10",
        "manual_review": False,
        "reason": "APP_A most consistently appears on WQ10 across flows"
    }
}

Why expert is needed

Because automation can detect the conflict, but cannot always safely choose the final owner.

2. Ambiguous but possibly usable flow

This is when the flow has partial evidence, but not enough for automatic targeting.

Example

Flow says:

producer_app = APP_C
consumer_app = APP_D
routing_target_qm = "WQ30"
consumer local queue matches queue name
but consumer_home_qm = "WQ31"
resolution_status = QUEUE_ONLY_MATCH

So:

queue matches
QM does not match

{
    "strategy": "manual_review_before_target",
    "exclude_from_target": True,
    "manual_review": True,
    "reasons": ["ambiguous_flow", "queue_only_match"]
}

Option A: exclude it
{
    "strategy": "exclude_from_target",
    "exclude_from_target": True,
    "reason": "Queue match alone is insufficient"
}

Option B: accept it manually
{
    "strategy": "direct_route_to_consumer_qm",
    "target_consumer_qm": "WQ31",
    "exclude_from_target": False,
    "manual_review": False,
    "reason": "Confirmed by repeated naming pattern and neighboring flows"
}
Why expert is needed

Because this is judgment under uncertainty.

3. Routing-only QM removal

This is when a QM appears to be a transit-only QM.

Example

Suppose:

WQ26 hosts no apps
routing_only = True
it sits between WQ10 -> WQ26 -> WQ20
many flows use it as pass-through only

{
    "strategy": "direct_route_to_consumer_qm",
    "remove_intermediate_qms": ["WQ26"],
    "reasons": ["routing_only_qm", "deterministic_target_routing"]
}

Expert decision
Option A: remove it
{
    "remove_intermediate_qms": ["WQ26"],
    "reason": "Transit-only QM with no ownership role"
}

Option B: keep it
{
    "remove_intermediate_qms": [],
    "reason": "Retained as boundary/regional QM"
}

Why expert is needed

Because sometimes a routing-only QM is architecturally meaningful even if it looks removable mathematically.

4. High fan-in / fan-out hotspot

This is a structural hotspot.

Example

Suppose node metrics show:

WQ50 fan_out = 18
WQ50 fan_in = 1
hosted apps = 1

That means one QM is sending to too many other QMs.

Auto decision

Could be something like:
{
    "strategy": "direct_route_to_consumer_qm",
    "reasons": ["deterministic_target_routing"]
}

But at node level, a hotspot report flags WQ50.

Expert decision

You may decide:

Option A: keep one QM but standardize channels

{
    "action": "retain_qm_standardize_channels",
    "reason": "Fan-out is business-driven, but channel model should be standardized"
}

Option B: split ownership
{
    "action": "split_application_domain",
    "reason": "Excessive fan-out suggests over-coupled ownership"
}

Why expert is needed

Because high fan-out alone does not always mean the QM should be split. It depends on enterprise realism.

Flow review list

Only flows where:

manual_review = True
exclude_from_target = True
normalize_producer_ownership = True
normalize_consumer_ownership = True
Node review list

Only QMs where:

routing_only = True
or fan_in > threshold
or fan_out > threshold

That keeps the work manageable.

expert_decisions = {
    # ------------------------------------------------------------
    # CASE 1: Ownership conflict
    # App appears tied to multiple QMs; expert picks one owner QM
    # ------------------------------------------------------------
    "APP_A->APP_B": {
        "strategy": "direct_route_to_consumer_qm",
        "target_producer_qm": "WQ10",
        "target_consumer_qm": "WQ20",
        "normalize_producer_ownership": False,
        "normalize_consumer_ownership": False,
        "manual_review": False,
        "exclude_from_target": False,
        "reasons": [
            "expert_selected_single_owner_qm_for_producer",
            "APP_A_consistently_owned_by_WQ10"
        ],
        "confidence": "HIGH"
    },

    # ------------------------------------------------------------
    # CASE 2: Ambiguous but accepted manually
    # Queue-only/QM-only/multi-candidate flow that expert resolves
    # ------------------------------------------------------------
    "APP_C->APP_D": {
        "strategy": "direct_route_to_consumer_qm",
        "target_producer_qm": "WQ14",
        "target_consumer_qm": "WQ31",
        "manual_review": False,
        "exclude_from_target": False,
        "target_route_type": "cross_qm_canonical",
        "reasons": [
            "expert_resolved_ambiguous_flow",
            "repeated_pattern_confirms_WQ31_as_consumer_qm"
        ],
        "confidence": "MEDIUM"
    },

    # ------------------------------------------------------------
    # CASE 3: Routing-only QM retained instead of removed
    # Auto wanted to remove WQ26, expert keeps it
    # ------------------------------------------------------------
    "APP_E->APP_F": {
        "strategy": "direct_route_to_consumer_qm",
        "remove_intermediate_qms": [],
        "target_producer_qm": "WQ40",
        "target_consumer_qm": "WQ50",
        "manual_review": False,
        "exclude_from_target": False,
        "reasons": [
            "expert_retained_transit_qm_exception",
            "WQ26_preserved_as_boundary_qm"
        ],
        "confidence": "MEDIUM"
    },

    # ------------------------------------------------------------
    # CASE 4: High fan-out hotspot handled conservatively
    # Keep the QM, but still canonicalize target routing
    # ------------------------------------------------------------
    "APP_G->APP_H": {
        "strategy": "direct_route_to_consumer_qm",
        "target_producer_qm": "WQ50",
        "target_consumer_qm": "WQ72",
        "manual_review": False,
        "exclude_from_target": False,
        "target_route_type": "cross_qm_canonical",
        "reasons": [
            "high_fan_out_qm_retained",
            "business_fan_out_preserved_but_standardized"
        ],
        "confidence": "HIGH"
    }
}

2. How this works with auto decisions

You already have auto decisions from:

optimization_decisions = build_optimization_decisions(
    flows=flows,
    flow_metrics=flow_metrics,
    node_metrics=node_metrics,
    routing_analysis=routing_analysis,
    nodes=nodes,
)

Now merge them with expert overrides:

merged_decisions = apply_expert_overrides(optimization_decisions, expert_decisions)
final_decisions = extract_final_decisions(merged_decisions)
3. Then comes target_flows

Yes — after expert overrides, the next step is exactly:

target_flows = build_optimized_target_flows(flows, final_decisions)

That gives you one target flow object per flow.

4. Full sequence

Use this order:

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

merged_decisions = apply_expert_overrides(optimization_decisions, expert_decisions)
final_decisions = extract_final_decisions(merged_decisions)

target_flows = build_optimized_target_flows(flows, final_decisions)
+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
import copy
import pandas as pd


def as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return []
    if str(value).strip():
        return [str(value).strip()]
    return []


def dedupe_sorted(values):
    return sorted(set(v for v in values if str(v).strip()))


def merge_scalar(auto_value, expert_value):
    """
    Expert scalar overrides auto scalar if expert provided a non-None value.
    """
    if expert_value is None:
        return auto_value
    return expert_value


def merge_list(auto_value, expert_value):
    """
    Merge list-like fields from auto + expert.
    If expert passes an empty list explicitly, keep it as empty list override.
    """
    if expert_value is None:
        return auto_value
    auto_list = as_list(auto_value)
    expert_list = as_list(expert_value)
    return dedupe_sorted(auto_list + expert_list)


def finalize_decision_record(decision):
    """
    Normalize and clean merged decision output.
    """
    list_fields = [
        "remove_intermediate_qms",
        "remove_remote_queue_objects",
        "preserve_validated_target_queues",
        "drop_unvalidated_target_queues",
        "reasons",
        "warnings",
        "decision_notes",
        "target_design_actions",
        "expert_reasons",
        "override_fields",
    ]

    for field in list_fields:
        decision[field] = dedupe_sorted(as_list(decision.get(field, [])))

    return decision


def apply_expert_overrides(auto_decisions, expert_decisions):
    """
    Merge expert decisions on top of auto decisions.

    Rules:
    - scalar fields: expert overrides auto
    - list fields: union merge, unless expert provides explicit empty list []
      which is treated as intentional replacement with empty list
    - tracks:
        - was_overridden
        - override_fields
        - expert_reasons
    """
    merged = {}

    list_merge_fields = {
        "remove_intermediate_qms",
        "remove_remote_queue_objects",
        "preserve_validated_target_queues",
        "drop_unvalidated_target_queues",
        "reasons",
        "warnings",
        "decision_notes",
        "target_design_actions",
    }

    for flow_id, auto_decision in auto_decisions.items():
        merged_decision = copy.deepcopy(auto_decision)
        expert = expert_decisions.get(flow_id, {})

        merged_decision.setdefault("expert_override_applied", False)
        merged_decision.setdefault("override_fields", [])
        merged_decision.setdefault("expert_reasons", [])

        if not expert:
            merged[flow_id] = finalize_decision_record(merged_decision)
            continue

        merged_decision["expert_override_applied"] = True

        for key, expert_value in expert.items():
            merged_decision["override_fields"].append(key)

            # keep expert explanation separately if field is singular reason
            if key == "reason":
                if expert_value is not None and str(expert_value).strip():
                    merged_decision["expert_reasons"].append(str(expert_value).strip())
                    merged_decision["decision_notes"].append(
                        f"Expert override reason: {str(expert_value).strip()}"
                    )
                continue

            if key in list_merge_fields:
                # Explicit empty list means intentional full replacement
                if isinstance(expert_value, list) and len(expert_value) == 0:
                    merged_decision[key] = []
                else:
                    merged_decision[key] = merge_list(merged_decision.get(key, []), expert_value)
            else:
                merged_decision[key] = merge_scalar(merged_decision.get(key), expert_value)

        # Mark expert resolution effects
        if merged_decision.get("manual_review") is False:
            merged_decision["decision_notes"].append(
                "Manual review requirement cleared by expert override."
            )

        if merged_decision.get("exclude_from_target") is False:
            merged_decision["decision_notes"].append(
                "Flow re-included in target scope after expert override."
            )

        merged[flow_id] = finalize_decision_record(merged_decision)

    return merged


def extract_final_decisions(merged_decisions):
    """
    Final decision map used by build_optimized_target_flows(...).

    Keeps all fields, but adds a lightweight status summary
    so downstream functions can use a consistent flag.
    """
    final_decisions = {}

    for flow_id, decision in merged_decisions.items():
        record = copy.deepcopy(decision)

        if record.get("exclude_from_target", False):
            record["final_status"] = "excluded"
        elif record.get("manual_review", False):
            record["final_status"] = "needs_review"
        else:
            record["final_status"] = "approved"

        final_decisions[flow_id] = finalize_decision_record(record)

    return final_decisions


def build_flow_review_df(flows, decisions):
    """
    Create a review dataframe for only the flows that need human attention.

    Review conditions:
    - manual_review = True
    - exclude_from_target = True
    - normalize_producer_ownership = True
    - normalize_consumer_ownership = True
    """
    rows = []

    for flow_id, decision in decisions.items():
        flow = flows.get(flow_id, {})

        needs_review = (
            bool(decision.get("manual_review", False))
            or bool(decision.get("exclude_from_target", False))
            or bool(decision.get("normalize_producer_ownership", False))
            or bool(decision.get("normalize_consumer_ownership", False))
        )

        if not needs_review:
            continue

        rows.append({
            "flow_id": flow_id,
            "producer_app": flow.get("producer_app"),
            "consumer_app": flow.get("consumer_app"),
            "producer_name": flow.get("producer_name"),
            "consumer_name": flow.get("consumer_name"),
            "producer_home_qm": "|".join(as_list(flow.get("producer_home_qm"))),
            "consumer_home_qm": "|".join(as_list(flow.get("consumer_home_qm"))),
            "routing_target_qm": "|".join(as_list(flow.get("routing_target_qm"))),
            "producer_neighborhoods": "|".join(as_list(flow.get("producer_neighborhoods", []))),
            "consumer_neighborhoods": "|".join(as_list(flow.get("consumer_neighborhoods", []))),
            "resolution_status": flow.get("resolution_status"),
            "ambiguity_type": flow.get("ambiguity_type"),
            "architecture_interpretation": flow.get("architecture_interpretation"),
            "target_requires_remote_pattern": flow.get("target_requires_remote_pattern"),
            "auto_strategy": decision.get("strategy"),
            "target_producer_qm": decision.get("target_producer_qm"),
            "target_consumer_qm": decision.get("target_consumer_qm"),
            "normalize_producer_ownership": decision.get("normalize_producer_ownership"),
            "normalize_consumer_ownership": decision.get("normalize_consumer_ownership"),
            "manual_review": decision.get("manual_review"),
            "exclude_from_target": decision.get("exclude_from_target"),
            "final_status": decision.get("final_status"),
            "confidence": decision.get("confidence"),
            "issues": "|".join(as_list(flow.get("issues", []))),
            "reasons": "|".join(as_list(decision.get("reasons", []))),
            "warnings": "|".join(as_list(decision.get("warnings", []))),
            "decision_notes": " || ".join(as_list(decision.get("decision_notes", []))),
        })

    review_df = pd.DataFrame(rows)

    if not review_df.empty:
        sort_cols = [c for c in ["manual_review", "exclude_from_target", "flow_id"] if c in review_df.columns]
        review_df = review_df.sort_values(sort_cols, ascending=[False, False, True]).reset_index(drop=True)

    return review_df


def build_node_review_df(nodes, node_metrics, decisions, fan_in_threshold=8, fan_out_threshold=8):
    """
    Create a QM/node-level review dataframe.

    Review conditions:
    - routing_only = True
    - fan_in > threshold
    - fan_out > threshold
    - QM appears in remove_intermediate_qms for one or more flows
    """
    qm_to_flows_marked_for_removal = {}

    for flow_id, decision in decisions.items():
        for qm in as_list(decision.get("remove_intermediate_qms", [])):
            qm_to_flows_marked_for_removal.setdefault(qm, []).append(flow_id)

    rows = []

    all_qms = sorted(set(list(nodes.keys()) + list(node_metrics.keys()) + list(qm_to_flows_marked_for_removal.keys())))

    for qm in all_qms:
        node_info = nodes.get(qm, {})
        metrics = node_metrics.get(qm, {})

        routing_only = bool(node_info.get("routing_only", False))
        hosted_apps = as_list(node_info.get("hosted_apps", []))
        fan_in = metrics.get("fan_in", 0)
        fan_out = metrics.get("fan_out", 0)

        flagged_for_removal_by_flows = qm_to_flows_marked_for_removal.get(qm, [])

        needs_review = (
            routing_only
            or fan_in > fan_in_threshold
            or fan_out > fan_out_threshold
            or len(flagged_for_removal_by_flows) > 0
        )

        if not needs_review:
            continue

        auto_action = []
        if routing_only:
            auto_action.append("routing_only_qm_detected")
        if fan_in > fan_in_threshold:
            auto_action.append("high_fan_in")
        if fan_out > fan_out_threshold:
            auto_action.append("high_fan_out")
        if flagged_for_removal_by_flows:
            auto_action.append("candidate_for_intermediate_qm_removal")

        rows.append({
            "qm": qm,
            "routing_only": routing_only,
            "hosted_apps": "|".join(hosted_apps),
            "hosted_app_count": len(hosted_apps),
            "fan_in": fan_in,
            "fan_out": fan_out,
            "flagged_for_removal_flow_count": len(flagged_for_removal_by_flows),
            "flagged_for_removal_flows": "|".join(sorted(flagged_for_removal_by_flows)),
            "auto_action": "|".join(auto_action),
        })

    review_df = pd.DataFrame(rows)

    if not review_df.empty:
        review_df = review_df.sort_values(
            ["routing_only", "fan_out", "fan_in", "qm"],
            ascending=[False, False, False, True]
        ).reset_index(drop=True)

    return review_df

optimization_decisions = build_optimization_decisions(
    flows=flows,
    flow_metrics=flow_metrics,
    node_metrics=node_metrics,
    routing_analysis=routing_analysis,
    nodes=nodes,
)

flow_review_df = build_flow_review_df(flows, optimization_decisions)
node_review_df = build_node_review_df(
    nodes=nodes,
    node_metrics=node_metrics,
    decisions=optimization_decisions,
    fan_in_threshold=8,
    fan_out_threshold=8,
)

merged_decisions = apply_expert_overrides(optimization_decisions, expert_decisions)
final_decisions = extract_final_decisions(merged_decisions)

target_flows = build_optimized_target_flows(flows, final_decisions)
This fits the override patterns you described for ownership conflict, ambiguous usable flow, retained routing QM, and hotspot handling.
expert_decisions = {
    "APP_A->APP_B": {
        "target_producer_qm": "WQ10",
        "manual_review": False,
        "exclude_from_target": False,
        "normalize_producer_ownership": False,
        "reasons": [
            "expert_selected_single_owner_qm_for_producer",
            "APP_A_consistently_owned_by_WQ10"
        ],
        "reason": "APP_A most consistently appears on WQ10 across flows"
    },
    "APP_C->APP_D": {
        "strategy": "direct_route_to_consumer_qm",
        "target_consumer_qm": "WQ31",
        "manual_review": False,
        "exclude_from_target": False,
        "reasons": [
            "expert_resolved_ambiguous_flow",
            "repeated_pattern_confirms_WQ31_as_consumer_qm"
        ],
        "reason": "Queue-only match accepted based on repeated naming pattern"
    },
    "APP_E->APP_F": {
        "remove_intermediate_qms": [],
        "manual_review": False,
        "exclude_from_target": False,
        "reasons": [
            "expert_retained_transit_qm_exception",
            "WQ26_preserved_as_boundary_qm"
        ],
        "reason": "Retained as boundary/regional transit QM"
    }
}
