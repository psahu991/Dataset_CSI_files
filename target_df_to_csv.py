import pandas as pd


def generate_target_csv_rows(target_flows: dict):
    rows = []

    for flow_id, tf in target_flows.items():

        producer_app = tf["producer_app"]
        consumer_app = tf["consumer_app"]

        producer_qm = tf["producer_target_qm"]
        consumer_qm = tf["consumer_target_qm"]

        producer_name = tf.get("producer_name", "")
        consumer_name = tf.get("consumer_name", "")

        is_cross_qm = producer_qm != consumer_qm

        # ------------------------------------------------------------
        # 1. PRODUCER LOCAL QUEUE
        # ------------------------------------------------------------
        rows.append({
            "Discrete Queue Name": tf["producer_local_queue"],
            "ProducerName": producer_name,
            "ConsumerName": consumer_name,
            "PrimaryAppRole": "Producer",
            "app_id": producer_app,
            "queue_manager_name": producer_qm,
            "q_type": "Local",
            "remote_q_mgr_name": "",
            "remote_q_name": "",
        })

        # ------------------------------------------------------------
        # 2. CONSUMER LOCAL QUEUE
        # ------------------------------------------------------------
        rows.append({
            "Discrete Queue Name": tf["consumer_local_queue"],
            "ProducerName": producer_name,
            "ConsumerName": consumer_name,
            "PrimaryAppRole": "Consumer",
            "app_id": consumer_app,
            "queue_manager_name": consumer_qm,
            "q_type": "Local",
            "remote_q_mgr_name": "",
            "remote_q_name": "",
        })

        # ------------------------------------------------------------
        # 3. CROSS-QM OBJECTS
        # ------------------------------------------------------------
        if is_cross_qm:

            # Remote Queue (defined on producer QM)
            rows.append({
                "Discrete Queue Name": tf["remote_queue"],
                "ProducerName": producer_name,
                "ConsumerName": consumer_name,
                "PrimaryAppRole": "Producer",
                "app_id": producer_app,
                "queue_manager_name": producer_qm,
                "q_type": "Remote",
                "remote_q_mgr_name": consumer_qm,
                "remote_q_name": tf["consumer_local_queue"],
            })

            # XMIT Queue (on producer QM)
            rows.append({
                "Discrete Queue Name": tf["xmit_queue"],
                "ProducerName": producer_name,
                "ConsumerName": consumer_name,
                "PrimaryAppRole": "Producer",
                "app_id": producer_app,
                "queue_manager_name": producer_qm,
                "q_type": "XMIT",
                "remote_q_mgr_name": consumer_qm,
                "remote_q_name": "",
            })

            # Sender Channel (logical representation)
            rows.append({
                "Discrete Queue Name": tf["sender_channel"],
                "ProducerName": producer_name,
                "ConsumerName": consumer_name,
                "PrimaryAppRole": "Producer",
                "app_id": producer_app,
                "queue_manager_name": producer_qm,
                "q_type": "SenderChannel",
                "remote_q_mgr_name": consumer_qm,
                "remote_q_name": "",
            })

            # Receiver Channel
            rows.append({
                "Discrete Queue Name": tf["receiver_channel"],
                "ProducerName": producer_name,
                "ConsumerName": consumer_name,
                "PrimaryAppRole": "Consumer",
                "app_id": consumer_app,
                "queue_manager_name": consumer_qm,
                "q_type": "ReceiverChannel",
                "remote_q_mgr_name": producer_qm,
                "remote_q_name": "",
            })

    return pd.DataFrame(rows)
	

target_df = generate_target_csv_rows(target_flows)

print(target_df.head())

# save to file
target_df.to_csv("target_output.csv", index=False)
