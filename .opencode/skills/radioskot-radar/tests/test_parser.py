from src.database import Database
from src.parser import parse_article, parse_sitemap


def test_parse_sitemap_extracts_all_urls(sample_sitemap_xml):
    entries = parse_sitemap(sample_sitemap_xml)
    assert len(entries) == 3
    assert entries[0]["url"] == "https://radioskot.com/publ/1/am-peredatchik-na-7-mgc"
    assert entries[0]["lastmod"] == "2026-07-13T07:27:08+00:00"
    assert "am-transmitter.jpg" in entries[0]["image_url"]


def test_parse_sitemap_no_prefix_filter(sample_sitemap_xml):
    entries = parse_sitemap(sample_sitemap_xml)
    urls = [e["url"] for e in entries]
    assert "https://radioskot.com/calc/3d-konstruktor-molekul-onlajn" in urls


def test_parse_article_title(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["title"] == "Самодельный AM-передатчик на 7 МГц"


def test_parse_article_date(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["published"] == "2026-07-13"


def test_parse_article_category(sample_article_html):
    result = parse_article(sample_article_html)
    assert "Схемы" in result["category"]
    assert "Передатчики" in result["category"]


def test_parse_article_tags(sample_article_html):
    result = parse_article(sample_article_html)
    assert "AM" in result["tags"]
    assert "transmitter" in result["tags"]


def test_parse_article_meta(sample_article_html):
    result = parse_article(sample_article_html)
    assert "MOSFET" in result["meta_description"]
    assert "передатчик" in result["meta_keywords"]


def test_parse_article_image(sample_article_html):
    result = parse_article(sample_article_html)
    assert "am-transmitter.jpg" in result["main_image"]


def test_parse_article_body(sample_article_html):
    result = parse_article(sample_article_html)
    assert result["content_html"] is not None
    assert "кварцевый генератор" in result["content_html"]


def test_parse_article_gp_style_date(sample_article_html_gp_style):
    result = parse_article(sample_article_html_gp_style)
    assert result["published"] == "2026-06-18"


def test_parse_article_gp_style_category(sample_article_html_gp_style):
    result = parse_article(sample_article_html_gp_style)
    assert "Схемы" in result["category"]


def test_parse_article_gp_style_image(sample_article_html_gp_style):
    result = parse_article(sample_article_html_gp_style)
    assert "volnomer.jpg" in result["main_image"]


def test_database_upsert_and_count(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(
        url="https://radioskot.com/publ/1/test",
        title="Test Article",
        author="Test Author",
        date="2026-01-01",
        category="Схемы",
        tags=["test", "radio"],
        meta_description="desc",
        meta_keywords="kw",
        image_url="https://radioskot.com/img.jpg",
    )
    assert db.get_total_count() == 1
    assert db.get_full_text_count() == 0


def test_database_dedup(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://radioskot.com/publ/1/a")
    db.upsert_article(url="https://radioskot.com/publ/1/a")
    assert db.get_total_count() == 1


def test_database_export(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://radioskot.com/publ/1/a1", title="A1")
    db.upsert_article(url="https://radioskot.com/publ/1/a2", title="A2")
    articles = db.export_articles()
    assert len(articles) == 2


def test_database_search(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://radioskot.com/publ/1/led", title="LED Dimmer", tags=["led"])
    db.update_full_text(1, "This is about LED dimming")
    results = db.search_articles("LED")
    assert len(results) == 1
    assert "LED Dimmer" in results[0]["title"]


def test_database_full_text_update(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.upsert_article(url="https://radioskot.com/publ/1/test", title="Test")
    assert db.get_full_text_count() == 0
    db.update_full_text(1, "Full content here")
    assert db.get_full_text_count() == 1


def test_database_get_schema(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    schema = db.get_schema()
    tables = {t["table"] for t in schema}
    assert "articles" in tables
    assert "search_scores" in tables