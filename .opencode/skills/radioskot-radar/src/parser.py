import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import BASE_URL, CATEGORY_MAP


def parse_sitemap(xml_text: str) -> list[dict]:
    soup = BeautifulSoup(xml_text, "xml")
    results = []
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if not loc:
            continue
        url = loc.get_text(strip=True)

        lastmod = url_tag.find("lastmod")
        lastmod_text = lastmod.get_text(strip=True) if lastmod else ""

        image_url = ""
        image = url_tag.find("image:image") or url_tag.find("image")
        if image:
            iloc = image.find("image:loc") or image.find("loc")
            if iloc:
                image_url = iloc.get_text(strip=True)

        results.append({
            "url": url,
            "lastmod": lastmod_text,
            "image_url": image_url,
        })
    return results


def _infer_category_from_url(url: str) -> str | None:
    m = re.search(r"/publ/(\w+)/", url)
    if m:
        slug = m.group(1)
        return CATEGORY_MAP.get(slug, slug)
    m = re.search(r"/calc/", url)
    if m:
        return "Калькуляторы"
    m = re.search(r"/blog/", url)
    if m:
        return "Блог"
    return None


def parse_article(html: str, article_url: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, Any] = {
        "title": None,
        "author": None,
        "published": None,
        "category": None,
        "tags": [],
        "meta_description": None,
        "meta_keywords": None,
        "content_html": None,
        "main_image": None,
    }

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        result["meta_description"] = meta_desc["content"]

    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        result["meta_keywords"] = meta_kw["content"]

    title_el = soup.find("h1", class_="entry-title")
    if title_el:
        result["title"] = title_el.get_text(strip=True)

    time_el = (
        soup.find("time", class_="entry-date")
        or soup.find("time", class_="gp-post-date")
        or soup.find("time", itemprop="datePublished")
    )
    if time_el:
        dt = time_el.get("datetime")
        if dt:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", dt)
            if m:
                result["published"] = m.group(1)

    author_el = soup.find("span", class_="author")
    if not author_el:
        author_el = soup.find("a", rel="author")
    if author_el:
        result["author"] = author_el.get_text(strip=True)

    cat_links = soup.find_all("a", rel="category tag")
    if cat_links:
        result["category"] = ", ".join(
            c.get_text(strip=True) for c in cat_links
        )
    elif article_url and not result["category"]:
        result["category"] = _infer_category_from_url(article_url)

    tag_links = soup.find_all("a", rel="tag")
    for t in tag_links:
        text = t.get_text(strip=True)
        if text:
            result["tags"].append(text)

    img = soup.find("img", class_="wp-post-image")
    if img:
        src = img.get("src", "")
        if src and not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        result["main_image"] = src

    content_div = soup.find("div", class_="entry-content")
    if content_div:
        for tag in content_div.select(
            ".gp-related-posts, .rmp-widgets-container, "
            "script, style, nav, ins, iframe, aside, "
            ".sharedaddy, .jp-relatedposts"
        ):
            tag.decompose()
        result["content_html"] = str(content_div)

    return result