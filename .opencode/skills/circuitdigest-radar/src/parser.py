import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

SITEMAP_BASE = "https://circuitdigest.com/sitemap.xml"
SITEMAP_PAGES = 3
BASE_URL = "https://circuitdigest.com"
CIRCUIT_PREFIX = "/electronic-circuits/"

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_sitemap(xml_text: str) -> list[dict]:
    soup = BeautifulSoup(xml_text, "xml")
    results = []
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if not loc:
            continue
        url = loc.get_text(strip=True)
        if CIRCUIT_PREFIX not in url:
            continue

        lastmod = url_tag.find("lastmod")
        lastmod_text = lastmod.get_text(strip=True) if lastmod else ""

        image_url = ""
        image_title = ""
        image = url_tag.find("image:image") or url_tag.find("image")
        if image:
            iloc = image.find("image:loc") or image.find("loc")
            ititle = image.find("image:title") or image.find("title")
            if iloc:
                image_url = iloc.get_text(strip=True)
            if ititle:
                image_title = ititle.get_text(strip=True)

        results.append({
            "url": url,
            "lastmod": lastmod_text,
            "image_url": image_url,
            "image_title": image_title,
        })
    return results


def _parse_date(text: str) -> str:
    m = re.match(r"(\w+)\s+(\d+),?\s*(\d{4})", text.strip())
    if not m:
        return ""
    month_name, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
    month = MONTH_NAMES.get(month_name)
    if not month:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_iso_date(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso)
    if m:
        return m.group(0)
    return ""


def parse_article(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, Any] = {
        "author": None,
        "published": None,
        "category": None,
        "tags": [],
        "meta_description": None,
        "meta_keywords": None,
        "content_html": None,
        "main_image": None,
        "circuit_diagram": None,
        "comments": [],
    }

    # Meta tags
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        result["meta_description"] = meta_desc["content"]

    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        result["meta_keywords"] = meta_kw["content"]

    # JSON-LD structured data (most reliable for date + author)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            # Handle @graph format
            if "@graph" in data:
                for item in data["@graph"]:
                    if "datePublished" in item:
                        result["published"] = _parse_iso_date(item["datePublished"])
                    if "author" in item and isinstance(item["author"], dict):
                        result["author"] = item["author"].get("name") or result["author"]
                    if result["published"] and result["author"]:
                        break
            else:
                if "datePublished" in data:
                    result["published"] = _parse_iso_date(data["datePublished"])
                if "author" in data and isinstance(data["author"], dict):
                    result["author"] = data["author"].get("name") or result["author"]
            if result["published"]:
                break
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    # Fallback: author from /users/ link
    if not result["author"]:
        author_link = soup.find("a", href=re.compile(r"^/users/"))
        if author_link:
            result["author"] = author_link.get_text(strip=True)

    # Fallback: published date from text
    if not result["published"]:
        pub_match = re.search(r"Published\s*(\w+\s+\d+[^<]*?\d{4})", html)
        if pub_match:
            result["published"] = _parse_date(pub_match.group(1))

    # Category from breadcrumb
    bc_ol = soup.find("ol", class_="breadcrumb")
    if bc_ol:
        links = bc_ol.find_all("a")
        if len(links) >= 2:
            result["category"] = links[1].get_text(strip=True)

    # Tags from /tags/ links
    for tag_a in soup.find_all("a", href=re.compile(r"^/tags/")):
        text = tag_a.get_text(strip=True)
        if text:
            result["tags"].append(text)

    # Main image — first <img> with projectimage in src
    main_img = soup.find("img", src=re.compile(r"projectimage"))
    if main_img:
        src = main_img.get("src", "")
        if src and not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        result["main_image"] = src

    # Circuit diagram image
    cd_img = soup.find("img", src=re.compile(r"circuitdiagram"))
    if cd_img:
        src = cd_img.get("src", "")
        if src and not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        result["circuit_diagram"] = src

    # Body content
    content_div = (
        soup.find("div", class_="region region-content")
        or soup.find("div", class_=re.compile(r"node__content"))
        or soup.find("div", class_=re.compile(r"field-name-body"))
    )
    if content_div:
        # Remove non-content elements
        for tag in content_div.select(
            ".comment, #comments, .field-name-field-tags, "
            ".field-name-field-category, script, style, nav, "
            ".breadcrumb, .region-header, .sharethis-wrapper, "
            ".block-block-content, .block-views, .block-search"
        ):
            tag.decompose()
        result["content_html"] = str(content_div)

    # Comments
    comment_section = soup.find("section", id="comments")
    if not comment_section:
        comment_section = soup.find("div", id="comments")
    if comment_section:
        for comment_article in comment_section.find_all("article", class_=re.compile(r"comment")):
            c = _parse_drupal_comment(comment_article)
            if c:
                result["comments"].append(c)

    return result


def _parse_drupal_comment(article) -> dict | None:
    try:
        author_el = article.find("span", class_=re.compile(r"authorname"))
        if not author_el:
            author_el = article.find("a", href=re.compile(r"/user/"))
        author = author_el.get_text(strip=True) if author_el else "Anonymous"

        content_div = article.find("div", class_=re.compile(r"comment-body"))
        if not content_div:
            content_div = article.find("div", class_=re.compile(r"field-name-comment-body"))
        content = content_div.get_text(" ", strip=True) if content_div else ""

        date_el = article.find("time")
        date = date_el.get("datetime", "") if date_el else ""

        if not content:
            return None
        return {"author": author, "date": date, "content": content}
    except Exception:
        return None