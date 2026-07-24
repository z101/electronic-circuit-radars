import json

from src.database import Database
from src.parser import parse_article, parse_sitemap


def test_parse_sitemap_filters_circuits(sample_sitemap_xml):
    entries = parse_sitemap(sample_sitemap_xml)
    assert len(entries) == 2
    assert entries[0]["url"] == "https://circuitdigest.com/electronic-circuits/1-watt-led-dimmer"
    assert entries[0]["lastmod"] == "2025-06-17T16:30:02+05:30"
    assert "LED-Dimmer-Circuit.gif" in entries[0]["image_url"]
    assert entries[0]["image_title"] == "1 Watt LED Dimmer Circuit Diagram"


def test_parse_sitemap_ignores_news(sample_sitemap_xml):
    entries = parse_sitemap(sample_sitemap_xml)
    urls = [e["url"] for e in entries]
    assert "https://circuitdigest.com/news/some-news" not in urls


def test_parse_article_author(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["author"] == "Dilip Raja"


def test_parse_article_date(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["published"] == "2015-05-13"


def test_parse_article_category(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["category"] == "Electronic Circuits"


def test_parse_article_tags(sample_article_html):
    result = parse_article(sample_article_html)
    assert "555 timer circuits" in result["tags"]
    assert "LED" in result["tags"]


def test_parse_article_meta(sample_article_html):
    result = parse_article(sample_article_html)
    assert "555 IC based PWM circuit" in result["meta_description"]
    assert "led dimmer" in result["meta_keywords"]


def test_parse_article_images(sample_article_html):
    result = parse_article(sample_article_html)
    assert "projectimage" in result["main_image"]
    assert "circuitdiagram" in result["circuit_diagram"]


def test_parse_article_body(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["content_html"] is not None
    assert "PWM" in result["content_html"]
    # Tags section should be removed from body
    assert "field-name-field-tags" not in result["content_html"]


def test_parse_article_comments(sample_article_html):
    result = parse_article(sample_article_html)
    assert len(result["comments"]) == 1
    assert result["comments"][0]["author"] == "John"
    assert "Great article" in result["comments"][0]["content"]


def test_database_upsert_and_count(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(
        url="https://circuitdigest.com/electronic-circuits/test",
        title="Test Article",
        author="Test Author",
        date="2025-01-01",
        category="Electronic Circuits",
        tags=["led", "test"],
        meta_description="desc",
        meta_keywords="kw",
        image_url="https://example.com/img.jpg",
        circuit_diagram="https://example.com/diag.gif",
    )
    assert db.get_total_count() == 1
    assert db.get_full_text_count() == 0


def test_database_export(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://example.com/a1", title="A1")
    db.upsert_article(url="https://example.com/a2", title="A2")
    articles = db.export_articles()
    assert len(articles) == 2


def test_database_search(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://example.com/led", title="LED Dimmer", tags=["led"])
    db.update_full_text(1, "This is about LED dimming")
    results = db.search_articles("LED")
    assert len(results) == 1
    assert "LED Dimmer" in results[0]["title"]