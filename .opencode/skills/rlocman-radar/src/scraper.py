import hashlib
import logging
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from lxml import html as lxml_html

from config import (
    BASE_URL, SEARCH_URL, ARTICLE_URL, ENGLISH_URL,
    ITEMS_PER_PAGE, RZ_CATEGORIES,
)

logger = logging.getLogger("rlocman-radar")

HTML_PATTERN = re.compile(r'<[^>]+>')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RUSSIAN_MONTHS = {
    'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04',
    'мая': '05', 'июня': '06', 'июля': '07', 'августа': '08',
    'сентября': '09', 'октября': '10', 'ноября': '11', 'декабря': '12',
}
DATE_RU_RE = re.compile(
    r'(\d{1,2})\s+(' + '|'.join(RUSSIAN_MONTHS.keys()) + r')\s+(\d{4})'
)


def _compute_content_hash(*parts) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return HTML_PATTERN.sub("", text).strip()


def _fetch_html(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None


def _parse_russian_date(text: str) -> str | None:
    m = DATE_RU_RE.search(text)
    if m:
        day = m.group(1).zfill(2)
        month = RUSSIAN_MONTHS[m.group(2)]
        year = m.group(3)
        return f"{year}-{month}-{day}"
    return None


def _parse_search_page(html_text: str, rz: str = None) -> dict:
    tree = lxml_html.fromstring(html_text)
    result = {"total": 0, "articles": []}

    total_el = tree.xpath("//*[contains(text(), 'Найдено:')]")
    if total_el:
        m = re.search(r'Найдено:\s*([\d\s]+)', total_el[0].text_content())
        if m:
            result["total"] = int(m.group(1).replace(" ", ""))

    for ol in tree.cssselect("ol.ui.list"):
        for li in ol.cssselect("li"):
            item1 = li.cssselect("div.item1 a[href*='schematics.html?di=']")
            if not item1:
                continue
            a = item1[0]
            href = a.get("href")
            di_str = href.split("di=")[-1].split("&")[0]
            if not di_str.isdigit():
                continue
            di = int(di_str)

            title_ru = (a.text_content() or "").strip()
            article_url = urljoin(BASE_URL, href)

            item2s = li.cssselect("div.item2")
            item2_texts = [el.text_content().strip() for el in item2s if el.text_content().strip()]

            author = item2_texts[0] if item2_texts else None
            date_str = None
            for txt in item2_texts:
                d = _parse_russian_date(txt)
                if d:
                    date_str = d
                    break

            summary_ru = None
            desc_el = li.cssselect("div.ui.items div.description")
            if desc_el:
                summary_ru = (desc_el[0].text_content() or "").strip()

            english_url = None
            title_en = None
            dl = li.cssselect("dl dd div.item1 a[href*='radiolocman.com']")
            if dl:
                english_url = dl[0].get("href") or None
                title_en = (dl[0].text_content() or "").strip()

            result["articles"].append({
                "di": di,
                "title_ru": title_ru,
                "title_en": title_en,
                "author": author,
                "date_published": date_str,
                "summary_ru": summary_ru,
                "english_url": english_url,
                "article_url": article_url,
                "category_rz": rz,
            })

    return result


def _parse_article_page(html_text: str, di: int, rz: str = None,
                        summary_ru: str = None, english_url: str = None) -> dict:
    tree = lxml_html.fromstring(html_text)

    body_ru = None
    body_el = tree.cssselect("div[itemprop='articleBody']")
    if body_el:
        body_ru = lxml_html.tostring(body_el[0], encoding="unicode", method="html")
    if not body_ru:
        body_el = tree.xpath("//div[contains(@class, 'my_content')]")
        if body_el:
            body_ru = lxml_html.tostring(body_el[0], encoding="unicode", method="html")

    if not body_ru:
        return None

    title_ru = None
    title_el = tree.cssselect("h1[itemprop='headline']")
    if title_el:
        title_ru = (title_el[0].text_content() or "").strip()
    if not title_ru:
        title_el = tree.cssselect("h1")
        if title_el:
            title_ru = (title_el[0].text_content() or "").strip()
    if not title_ru:
        meta_el = tree.cssselect("meta[property='og:title']")
        if meta_el:
            title_ru = (meta_el[0].get("content") or "").strip()

    date_published = None
    time_el = tree.cssselect("time[itemprop='datePublished']")
    if time_el:
        date_published = time_el[0].get("datetime")
    if not date_published:
        p_el = tree.cssselect("h1 + p")
        if p_el:
            d = _parse_russian_date(p_el[0].text_content() or "")
            if d:
                date_published = d

    date_modified = None
    date_mod_el = tree.cssselect("meta[itemprop='dateModified']")
    if date_mod_el:
        date_modified = date_mod_el[0].get("content")

    author = None
    author_url = None
    author_el = tree.cssselect("span[itemprop='author'] a[itemprop='url']")
    if author_el:
        author_url = author_el[0].get("href")
        name_el = author_el[0].cssselect("span[itemprop='name']")
        if name_el:
            author = (name_el[0].text_content() or "").strip()
        else:
            author = (author_el[0].text_content() or "").strip()

    if not author:
        author_el = tree.cssselect("h1 + p")
        if author_el:
            text = (author_el[0].text_content() or "").strip()
            if text and not _parse_russian_date(text):
                author = text

    manufacturer = None
    components = None
    for h2 in tree.cssselect("h2.my_content_h2"):
        txt = h2.text_content() or ""
        parts = [p.strip() for p in txt.split("›")]
        if len(parts) >= 1:
            manufacturer = parts[0]
        if len(parts) >= 2:
            components = parts[1] if len(parts) == 2 else " ".join(parts[1:])

    eng_url = english_url
    eng_title = None
    eng_link_el = tree.xpath("//*[contains(text(), 'На английском языке')]/a")
    if eng_link_el:
        eng_url = eng_link_el[0].get("href") or eng_url
        eng_title = (eng_link_el[0].text_content() or "").strip()

    categories = None
    breadcrumb_el = tree.cssselect("div[itemprop='breadcrumb']")
    if breadcrumb_el:
        categories = (breadcrumb_el[0].text_content() or "").strip()

    tags = None
    tag_texts = []
    if components:
        tag_texts = [c.strip() for c in components.split() if c.strip()]

    return {
        "di": di,
        "title_ru": title_ru,
        "title_en": eng_title,
        "author": author,
        "author_url": author_url,
        "date_published": date_published,
        "date_modified": date_modified,
        "categories": categories,
        "category_rz": rz,
        "manufacturer": manufacturer,
        "components": components,
        "summary_ru": summary_ru,
        "body_ru": body_ru,
        "english_url": eng_url,
        "article_url": f"{ARTICLE_URL}?di={di}",
        "tags": ", ".join(tag_texts) if tag_texts else None,
        "component_count": len(tag_texts),
        "content_hash": _compute_content_hash(
            title_ru, body_ru, author, date_published
        ),
    }


class RlocmanScraper:
    def __init__(self, max_workers: int = 2):
        self.max_workers = max_workers

    def discover_articles(self, rz: str, max_pages: int = 20) -> list:
        first_url = f"{SEARCH_URL}?rz={rz}"
        html = _fetch_html(first_url)
        if not html:
            return []
        parsed = _parse_search_page(html, rz=rz)
        total = parsed["total"]
        if total == 0:
            return []
        pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        pages = min(pages, max_pages)
        logger.info("Category %s (%s): %d articles across %d pages",
                     rz, RZ_CATEGORIES.get(rz, "?"), total, pages)

        all_articles = list(parsed["articles"])
        if pages > 1:
            urls = {p: f"{SEARCH_URL}?rz={rz}&p={p}" for p in range(1, pages)}
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                fut_map = {pool.submit(_fetch_html, url): p for p, url in urls.items()}
                for fut in as_completed(fut_map):
                    p = fut_map[fut]
                    html = fut.result()
                    if html:
                        parsed = _parse_search_page(html, rz)
                        batch = parsed["articles"]
                        if batch:
                            all_articles.extend(batch)

        seen = set()
        unique = []
        for a in all_articles:
            if a["di"] not in seen:
                seen.add(a["di"])
                unique.append(a)
        logger.info("Category %s: discovered %d unique articles", rz, len(unique))
        return unique

    def _fetch_article(self, meta: dict) -> dict | None:
        di = meta["di"]
        url = f"{ARTICLE_URL}?di={di}"
        for attempt in range(3):
            html = _fetch_html(url)
            if html:
                result = _parse_article_page(
                    html, di,
                    rz=meta.get("category_rz"),
                    summary_ru=meta.get("summary_ru"),
                    english_url=meta.get("english_url"),
                )
                if result and result.get("body_ru"):
                    return result
                return None
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        return None

    def fetch_articles(self, articles_meta: list, db=None) -> list:
        results = [None] * len(articles_meta)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            fut_map = {}
            for i, meta in enumerate(articles_meta):
                if db and db.article_exists(meta["di"]):
                    continue
                fut_map[pool.submit(self._fetch_article, meta)] = i
            for fut in as_completed(fut_map):
                i = fut_map[fut]
                results[i] = fut.result()
                if (i + 1) % 50 == 0:
                    done = len([r for r in results if r is not None])
                    logger.info("Progress: %d/%d articles", done, len(articles_meta))
        return [r for r in results if r is not None]

    def fetch_category(self, rz: str, db=None) -> list:
        discovered = self.discover_articles(rz)
        if not discovered:
            return []
        logger.info("Fetching %d articles for category %s", len(discovered), rz)
        full = self.fetch_articles(discovered, db=db)
        return full