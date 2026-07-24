def normalize_scores(results: list[dict], key: str = "score") -> list[dict]:
    if not results:
        return results
    scores = [r.get(key, 0) or 0 for r in results]
    mn = min(scores)
    mx = max(scores)
    if mx == mn:
        for r in results:
            r[f"{key}_normalized"] = 100
        return results
    for r in results:
        raw = r.get(key, 0) or 0
        r[f"{key}_normalized"] = round((raw - mn) / (mx - mn) * 100, 1)
    return results