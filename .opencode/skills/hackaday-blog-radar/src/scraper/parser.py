import re
from typing import Any

from bs4 import BeautifulSoup

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

REQUIRED_FIELDS = ["title", "url", "date", "excerpt", "tags"]


def _parse_date(text: str) -> str:
    m = re.match(r"(\w+)\s+(\d+),\s+(\d{4})", text.strip())
    if not m:
        return ""
    month_name, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
    month = MONTH_NAMES.get(month_name)
    if not month:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def get_first_page_info(soup: BeautifulSoup) -> tuple[int, int]:
    counter = soup.find("h2", class_="counter_cat")
    total = 0
    if counter:
        match = re.search(r"(\d+)", counter.get_text())
        if match:
            total = int(match.group(1))

    real_articles = 0
    for article in soup.find_all("article"):
        title_el = article.find("h1", class_="entry-title")
        if title_el and title_el.find("a"):
            real_articles += 1
    per_page = max(real_articles, 1)
    return total, per_page


def parse_archive_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for article in soup.find_all("article"):
        title_el = article.find("h1", class_="entry-title")
        if not title_el:
            continue
        link = title_el.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        url = link.get("href", "")

        date_el = article.find("span", class_="entry-date")
        date = ""
        if date_el:
            date_link = date_el.find("a")
            if date_link:
                date = _parse_date(date_link.get_text(strip=True))

        excerpt_div = article.find("div", class_="entry-content")
        excerpt = excerpt_div.get_text(" ", strip=True) if excerpt_div else ""

        tags = []
        tags_el = article.find("span", class_="tags-links")
        if tags_el:
            tags = [a.get_text(strip=True) for a in tags_el.find_all("a", rel="tag")]

        articles.append({
            "title": title,
            "url": url,
            "date": date,
            "excerpt": excerpt,
            "tags": tags,
        })
    return articles


def parse_article_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, Any] = {
        "author": None,
        "content_html": None,
        "comments": [],
        "has_comments_section": False,
    }

    author_el = soup.select_one("ul.meta-authors span.fn a")
    if author_el:
        result["author"] = author_el.get_text(strip=True)

    content_el = soup.find("div", class_="entry-content")
    if content_el:
        for tag in content_el.select(
            ".sharedaddy, .jp-relatedposts, .wpcnt, .adsbygoogle, "
            ".yarpp-related, nav, .wp-block-group--related-posts"
        ):
            tag.decompose()

        result["content_html"] = str(content_el)

    comment_list = soup.find("ol", class_="comment-list")
    if comment_list:
        result["has_comments_section"] = True
        for li in comment_list.find_all("li", class_=re.compile(r"^comment"), recursive=False):
            comment = _parse_comment(li)
            if comment:
                result["comments"].append(comment)
    else:
        disqus = soup.find(id="disqus_thread")
        result["has_comments_section"] = disqus is not None

    return result


def _parse_comment(li) -> dict | None:
    try:
        auth = li.find("cite", class_="fn")
        author = auth.get_text(strip=True) if auth else "Anonymous"

        date_el = li.find("a", class_="comment-permalink")
        date = ""
        if date_el:
            date = date_el.get_text(strip=True)

        content_div = li.find("div", class_="comment-content")
        content = content_div.get_text(" ", strip=True) if content_div else ""

        if not content:
            return None
        return {"author": author, "date": date, "content": content}
    except Exception:
        return None


def validate_articles(articles: list[dict]) -> list[str]:
    errors = []
    for i, article in enumerate(articles):
        for field in REQUIRED_FIELDS:
            if field == "tags":
                continue
            if not article.get(field):
                errors.append(f"Article {i}: missing or empty field '{field}'")
        url = article.get("url", "")
        if not url.startswith("https://hackaday.com/"):
            errors.append(f"Article {i}: URL does not look like a Hackaday link: {url}")
    return errors