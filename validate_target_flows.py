def validate_target_flows(target_flows: dict):
    """
    Validate all target flows against constraints.
    Returns:
        validation_results (dict)
    """
    results = {}

    for flow_id, tf in target_flows.items():

        errors = []

        producer_qm = tf["producer_target_qm"]
        consumer_qm = tf["consumer_target_qm"]
        routing_qm = tf["routing_target_qm"]

        # 1. QM assignment
        if not producer_qm:
            errors.append("missing_producer_qm")

        if not consumer_qm:
            errors.append("missing_consumer_qm")

        # 2. Local queues must exist
        if not tf["producer_local_queue"]:
            errors.append("missing_producer_local_queue")

        if not tf["consumer_local_queue"]:
            errors.append("missing_consumer_local_queue")

        # 3. Cross-QM checks
        is_cross_qm = producer_qm != consumer_qm

        if is_cross_qm:

            # routing must go to consumer QM
            if routing_qm != consumer_qm:
                errors.append("routing_not_equal_consumer_qm")

            # MQ objects must exist
            if not tf["remote_queue"]:
                errors.append("missing_remote_queue")

            if not tf["xmit_queue"]:
                errors.append("missing_xmit_queue")

            if not tf["sender_channel"]:
                errors.append("missing_sender_channel")

            if not tf["receiver_channel"]:
                errors.append("missing_receiver_channel")

        results[flow_id] = {
            "valid": len(errors) == 0,
            "errors": errors
        }

    return results
validation_results = validate_target_flows(target_flows)

for flow_id, result in validation_results.items():
    print(flow_id, result)
