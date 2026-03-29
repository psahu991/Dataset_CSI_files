def generate_explanation_report(flows, target_flows, decisions, validation_results):
    report = []

    for flow_id, flow in flows.items():
        tf = target_flows.get(flow_id, {})
        decision = decisions.get(flow_id, {})
        validation = validation_results.get(flow_id, {})

        entry = {
            # --------------------------------------------------
            # 1. IDENTIFICATION
            # --------------------------------------------------
            "flow_id": flow_id,
            "producer_app": flow["producer_app"],
            "consumer_app": flow["consumer_app"],

            # --------------------------------------------------
            # 2. AS-IS STATE
            # --------------------------------------------------
            "as_is_producer_qm": flow["producer_home_qm"],
            "as_is_consumer_qm": flow["consumer_home_qm"],
            "as_is_routing_qm": flow["routing_target_qm"],
            "as_is_has_alias": flow["has_alias"],
            "as_is_local_only": flow["has_local_only"],
            "as_is_issues": ", ".join(flow["issues"]),

            # --------------------------------------------------
            # 3. OPTIMIZATION DECISION
            # --------------------------------------------------
            "strategy": decision.get("strategy"),
            "remove_alias": decision.get("remove_alias"),
            "removed_intermediate_qms": ", ".join(decision.get("remove_intermediate_qms", [])),
            "manual_review": decision.get("manual_review"),
            "decision_reasons": ", ".join(decision.get("reasons", [])),

            # --------------------------------------------------
            # 4. TARGET STATE
            # --------------------------------------------------
            "target_producer_qm": tf.get("producer_target_qm"),
            "target_consumer_qm": tf.get("consumer_target_qm"),
            "target_routing_qm": tf.get("routing_target_qm"),

            "producer_local_queue": tf.get("producer_local_queue"),
            "consumer_local_queue": tf.get("consumer_local_queue"),
            "remote_queue": tf.get("remote_queue"),
            "xmit_queue": tf.get("xmit_queue"),
            "sender_channel": tf.get("sender_channel"),
            "receiver_channel": tf.get("receiver_channel"),

            # --------------------------------------------------
            # 5. VALIDATION
            # --------------------------------------------------
            "constraint_valid": validation.get("valid"),
            "constraint_errors": ", ".join(validation.get("errors", [])),

            # --------------------------------------------------
            # 6. HUMAN-READABLE SUMMARY
            # --------------------------------------------------
            "explanation": build_human_readable_explanation(flow, decision, tf, validation)
        }

        report.append(entry)

    return report
