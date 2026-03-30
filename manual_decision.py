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
