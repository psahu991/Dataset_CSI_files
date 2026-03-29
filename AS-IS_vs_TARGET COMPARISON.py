# ------------------------------------------------------------
# 5. AS-IS vs TARGET COMPARISON
# ------------------------------------------------------------

def compare_as_is_vs_target(as_is_matrix, target_matrix):
    comparison = {
        "global": {},
        "nodes": {},
        "flows": {}
    }

    # -----------------------------
    # Global comparison
    # -----------------------------
    global_keys = sorted(set(as_is_matrix["global"].keys()) | set(target_matrix["global"].keys()))

    for key in global_keys:
        before = as_is_matrix["global"].get(key, 0)
        after = target_matrix["global"].get(key, 0)

        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = after - before
            pct_change = None
            if before != 0:
                pct_change = round((delta / before) * 100, 2)
        else:
            delta = None
            pct_change = None

        comparison["global"][key] = {
            "before": before,
            "after": after,
            "delta": delta,
            "pct_change": pct_change
        }

    # -----------------------------
    # Node count summary only
    # -----------------------------
    comparison["nodes"]["before_count"] = len(as_is_matrix["nodes"])
    comparison["nodes"]["after_count"] = len(target_matrix["nodes"])
    comparison["nodes"]["delta"] = comparison["nodes"]["after_count"] - comparison["nodes"]["before_count"]

    # -----------------------------
    # Flow summary counts
    # -----------------------------
    comparison["flows"]["before_count"] = len(as_is_matrix["flows"])
    comparison["flows"]["after_count"] = len(target_matrix["flows"])
    comparison["flows"]["delta"] = comparison["flows"]["after_count"] - comparison["flows"]["before_count"]

    return comparison
