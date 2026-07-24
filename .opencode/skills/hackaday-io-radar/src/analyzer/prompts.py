import json


def _parse_tags(tags) -> list:
    if isinstance(tags, list):
        return tags
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
            return parsed if isinstance(parsed, list) else [tags]
        except (json.JSONDecodeError, TypeError):
            return [tags]
    return []


def format_project(i: int, p: dict) -> str:
    created = p.get("created_at", "")
    name = p.get("name", "")
    owner = (p.get("owner_name") or "").strip()
    tags = _parse_tags(p.get("tags"))
    tags_str = ", ".join(tags) if tags else ""
    summary = (p.get("summary") or "").strip()
    parts = f'[{created}] "{name}"'
    if owner:
        parts += f" by {owner}"
    if tags_str:
        parts += f" [{tags_str}]"
    if summary:
        if len(summary) > 200:
            summary = summary[:197] + "..."
        parts += f" — {summary}"
    return f"{i}. {parts}"


def build_prompt(query_text: str, projects: list[dict]) -> str:
    prompt_template = DEFAULT_ANALYZE_CONFIG["prompt_search"]
    project_lines = [format_project(i + 1, p) for i, p in enumerate(projects)]
    projects_text = "\n".join(project_lines)
    return prompt_template.format(user_query=query_text, articles=projects_text)


def format_rerank_project(p: dict, desc_limit: int = 1000) -> str:
    tags = _parse_tags(p.get("tags"))
    tags_str = ", ".join(tags) if tags else "—"
    lines = [f"[ID {p['id']}] {p['name']}"]
    lines.append(f"Owner: {p.get('owner_name', '?')}  Tags: {tags_str}")
    summary = (p.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    desc = (p.get("description") or "").strip()
    if desc:
        if len(desc) > desc_limit:
            desc = desc[:desc_limit-3] + "..."
        lines.append(f"Description:\n{desc}")
    return "\n".join(lines)


def build_rerank_prompt(query_text: str, projects: list[dict]) -> str:
    prompt_template = DEFAULT_ANALYZE_CONFIG["prompt_rerank"]
    project_lines = [format_rerank_project(p) for p in projects]
    projects_text = "\n".join(project_lines)
    return prompt_template.format(user_query=query_text, articles=projects_text)


def build_summary_prompt(projects: list[dict]) -> str:
    prompt_template = DEFAULT_ANALYZE_CONFIG["prompt_summarize"]
    lines = []
    for p in projects:
        tags = _parse_tags(p.get("tags"))
        tags_str = ", ".join(tags) if tags else "—"
        lines.append(f"[ID {p['id']}] {p['name']}")
        lines.append(f"Tags: {tags_str}")
        summary = (p.get("summary") or "").strip()
        if summary:
            lines.append(f"Summary: {summary}")
        desc = (p.get("description") or "").strip()
        if desc:
            if len(desc) > 2000:
                desc = desc[:1997] + "..."
            lines.append(f"Description:\n{desc}")
        lines.append("")
    projects_text = "\n".join(lines)
    return prompt_template.format(articles=projects_text)