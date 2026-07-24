import json
import logging
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 10, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    m = re.match(r"(\w+)\s+(\d+),?\s+(\d{4})", text, re.IGNORECASE)
    if m:
        month_name = m.group(1).lower()
        day = int(m.group(2))
        year = int(m.group(3))
        month = MONTH_NAMES.get(month_name)
        if month and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return text[:10]
    return ""


def parse_archive_page(html: str) -> list[dict]:
    """Parse the project listing page. Returns list of project dicts."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    cards = soup.select('[class*="_ibleCard"]')
    if not cards:
        logger.warning("No project cards found on page")
        return articles
    seen_urls = set()
    for card in cards:
        title_link = card.select_one('a[class*="_title"]')
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        url = title_link.get("href", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        if url.startswith("/"):
            url = f"https://www.instructables.com{url}"
        author_link = card.select_one('a[href^="/member/"]')
        author = author_link.get_text(strip=True) if author_link else ""
        cat_link = card.select_one('a[href*="/projects"]')
        category = cat_link.get_text(strip=True) if cat_link else ""
        articles.append({
            "title": title,
            "url": url,
            "date": "",
            "excerpt": title,
            "tags": [category] if category else [],
            "author": author,
        })
    return articles


def parse_project_jsonld(html: str) -> dict[str, Any]:
    """Extract project data from JSON-LD embedded in the page.

    Returns:
        author:  contributor name (e.g. "tpw037")
        date:    datePublished (YYYY-MM-DD)
        title:   headline
        excerpt: description
        content_md: full text assembled from HowTo steps
    """
    result = {
        "author": None,
        "date": None,
        "title": None,
        "excerpt": None,
        "content_md": None,
    }

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        tp = data.get("@type", "")

        if tp == "Article":
            result["title"] = data.get("headline") or data.get("name")
            # date
            pub = data.get("datePublished") or data.get("dateModified")
            if pub:
                result["date"] = pub[:10]
            # excerpt
            desc = data.get("description", "")
            if desc:
                # Strip the title prefix that appears in descriptions
                title_part = (result["title"] or "") + ": "
                if desc.startswith(title_part):
                    desc = desc[len(title_part):]
                result["excerpt"] = desc[:500]
            # author from contributor
            contributor = data.get("contributor")
            if contributor and isinstance(contributor, dict):
                result["author"] = contributor.get("name")

        if tp == "HowTo":
            steps = data.get("step", [])
            if steps:
                parts = []
                for step in steps:
                    step_name = step.get("name", "")
                    step_text = step.get("text", "")
                    if step_name:
                        parts.append(f"## {step_name}")
                    if step_text:
                        parts.append(step_text)
                if parts:
                    result["content_md"] = "\n\n".join(parts)

    return result


KNOWN_CATEGORIES = [
    ("leds", "LEDs Projects",
     "https://www.instructables.com/circuits/leds/projects/"),
]