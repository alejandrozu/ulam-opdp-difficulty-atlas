OPDP_CERT_ROUTER_VERSION = "0.1.0"
OPDP_CERT_POLICY = "baseline"

def _opdp_depth(text):
    depth = peak = 0
    for char in text:
        if char == "(":
            depth += 1
            peak = max(peak, depth)
        elif char == ")":
            depth = max(0, depth - 1)
    return peak

def _opdp_marathon_features(problem):
    eq1 = normalise(problem["equation1"])
    eq2 = normalise(problem["equation2"])
    eq1_id = norm_id(problem.get("eq1_id") or problem.get("equation1_id", ""))
    eq2_id = norm_id(problem.get("eq2_id") or problem.get("equation2_id", ""))
    eq1_id, eq2_id = bind_ids_to_text(eq1_id, eq2_id, eq1, eq2)
    known = oracle(eq1_id, eq2_id)
    hyp = analyse(eq1)
    goal = analyse(eq2)
    free = hyp["lhs_only"] | hyp["rhs_only"]
    lhs = hyp["lhs"]
    if known == "false":
        bucket = 0
    elif len(lhs) == 1 and lhs not in hyp["rhs_vars"]:
        bucket = 1
    elif hyp["lhs_only"]:
        bucket = 2
    elif free and len(free) <= 2:
        bucket = 3
    elif known == "true":
        bucket = 4
    else:
        bucket = 5
    return {
        "bucket": bucket,
        "known": known or "unknown",
        "total_ops": hyp["op_count"] + goal["op_count"],
        "total_vars": len(set(hyp["variables"]) | set(goal["variables"])),
        "variable_occurrences": len(re.findall(r"\b([a-z])\b", eq1 + " " + eq2)),
        "max_depth": max(_opdp_depth(eq1), _opdp_depth(eq2)),
    }

def opdp_marathon_priority(problem, policy=OPDP_CERT_POLICY):
    features = _opdp_marathon_features(problem)
    bucket = features["bucket"]
    if policy == "structural_v0":
        if features["known"] == "false":
            return (bucket, features["total_vars"], features["total_ops"],
                    features["max_depth"], features["variable_occurrences"])
        return (bucket, features["total_ops"] + features["max_depth"],
                features["total_vars"], features["variable_occurrences"])
    return (bucket,)
