DEFAULT_ANALYZE_CONFIG: dict = {
    "max_score": 100,
    "batch_size": 100,
    "parallel_agents": 10,
    "prompt_search": (
        "You are an expert in electronics, embedded systems, and DIY hardware.\n"
        "Rate how relevant each project is to the user's query.\n\n"
        "User query:\n{user_query}\n\n"
        "For each project you have: name, owner, tags, and a short summary.\n"
        "Score relevance on a 0-100 scale using this rubric:\n\n"
        "  Score  | Meaning\n"
        "  -------|--------\n"
        "  81-100 | Core topic match. Project is directly about the query subject.\n"
        "  61-80  | Clearly relevant. Shares the same domain/technology as the query.\n"
        "  41-60  | Somewhat relevant. Mentions related concepts but isn't focused on the query.\n"
        "  21-40  | Tangential. The topic touches the query only peripherally.\n"
        "   0-20  | Unrelated or off-topic.\n\n"
        "Rules:\n"
        "- Base your score primarily on the summary and name. If the summary is empty, use name and tags only.\n"
        "- Score strictly — use the whole 0-100 range.\n"
        "- When in doubt, prefer the lower end of the range.\n"
        "- Write the reason in the same language as the user query.\n\n"
        "Return a strict JSON array, no markdown, no explanation:\n"
        '[{"id": N, "relevance": 0, "reason": "..."}, ...]\n\n'
        "Projects:\n{articles}"
    ),
    "prompt_rerank": (
        "You are an expert in electronics, embedded systems, and DIY hardware.\n"
        "Re-rank these projects by relevance to the user's query.\n\n"
        "User query:\n{user_query}\n\n"
        "For each project you have: name, owner, tags, summary, and full description.\n"
        "Score relevance on a 0-100 scale:\n"
        "  81-100 | Direct match. Core topic of the project matches the query.\n"
        "  61-80  | Clearly relevant. Shares technology or domain.\n"
        "  41-60  | Somewhat relevant. Related concepts mentioned.\n"
        "  21-40  | Tangential. Only peripherally related.\n"
        "   0-20  | Unrelated.\n"
        "Use the full description to inform your score, not just the summary.\n"
        "Score strictly. Write reason in the same language as the query.\n"
        "Return strict JSON array, no markdown:\n"
        '[{"id": N, "relevance": 0-100, "reason": "..."}, ...]\n\n'
        "Projects:\n{articles}"
    ),
    "prompt_summarize": (
        "You are a technical editor. Write a 3-5 sentence Russian summary\n"
        "of each hardware project below. Cover: what it is, key components,\n"
        "how it works, and what makes it interesting.\n"
        "Return a strict JSON array, no markdown:\n"
        '[{"id": N, "summary_ru": "..."}, ...]\n\n'
        "Projects:\n{articles}"
    ),
}