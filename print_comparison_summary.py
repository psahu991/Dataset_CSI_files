def print_comparison_summary(comparison):
    print("=" * 100)
    print("AS-IS vs TARGET COMPARISON")
    print("=" * 100)

    print("\nGLOBAL METRICS")
    for metric, values in comparison["global"].items():
        print(
            f"- {metric}: "
            f"before={values['before']}, "
            f"after={values['after']}, "
            f"delta={values['delta']}, "
            f"pct_change={values['pct_change']}"
        )

    print("\nNODE SUMMARY")
    for k, v in comparison["nodes"].items():
        print(f"- {k}: {v}")

    print("\nFLOW SUMMARY")
    for k, v in comparison["flows"].items():
        print(f"- {k}: {v}")
		
=======================================================================================
# attach validation back into target flows
validation_results = validate_target_flows(target_flows)

for flow_id, result in validation_results.items():
    target_flows[flow_id]["constraint_valid"] = result["valid"]
    target_flows[flow_id]["constraint_errors"] = result["errors"]

# build target graph data
target_nodes, target_edges = build_target_graph_data(target_flows)

# target matrix
target_matrix = build_target_complexity_matrix(target_flows, target_nodes, target_edges)

# comparison
comparison = compare_as_is_vs_target(matrix, target_matrix)

# print results
print("TARGET GLOBAL METRICS")
print(target_matrix["global"])

print_comparison_summary(comparison)
