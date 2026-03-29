def build_human_readable_explanation(flow, decision, tf, validation):
    parts = []

    # AS-IS description
    parts.append(
        f"Flow {flow['producer_app']} → {flow['consumer_app']} "
        f"originates from QM {flow['producer_home_qm']} "
        f"and routes via {flow['routing_target_qm']}."
    )

    # Issues
    if flow["issues"]:
        parts.append(
            f"Identified issues: {', '.join(flow['issues'])}."
        )

    # Decision
    if decision.get("strategy"):
        parts.append(
            f"Applied strategy: {decision['strategy']}."
        )

    if decision.get("remove_intermediate_qms"):
        parts.append(
            f"Removed intermediate QMs: {', '.join(decision['remove_intermediate_qms'])}."
        )

    if decision.get("remove_alias"):
        parts.append("Alias indirection removed.")

    # Target
    parts.append(
        f"Target design enforces routing from {tf.get('producer_target_qm')} "
        f"to {tf.get('consumer_target_qm')}."
    )

    # Constraint summary
    if validation.get("valid"):
        parts.append("All constraints satisfied.")
    else:
        parts.append(
            f"Constraint issues: {', '.join(validation.get('errors', []))}."
        )

    return " ".join(parts)
	
import pandas as pd

def explanation_report_to_df(report):
    return pd.DataFrame(report)
	
validation_results = validate_target_flows(target_flows)

report = generate_explanation_report(
    flows=flows,
    target_flows=target_flows,
    decisions=optimization_decisions,
    validation_results=validation_results
)

report_df = explanation_report_to_df(report)

print(report_df.head())

# save
report_df.to_csv("explanation_report.csv", index=False)
