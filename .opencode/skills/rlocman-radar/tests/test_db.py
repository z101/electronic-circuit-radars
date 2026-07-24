import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from db import Database
from config import RZ_CATEGORIES, BASE_URL, SEARCH_URL, ARTICLE_URL


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    return db, path


def _clean_db(db, path):
    conn = db._get_conn()
    conn.close()
    try:
        os.unlink(path)
    except PermissionError:
        import time
        time.sleep(0.1)
        try:
            os.unlink(path)
        except PermissionError:
            pass


def test_config():
    assert len(RZ_CATEGORIES) > 50
    assert "0205" in RZ_CATEGORIES
    assert RZ_CATEGORIES["0205"] == "Светотехника"
    assert BASE_URL.startswith("https://")
    assert SEARCH_URL.endswith("/shem/search.html")
    assert ARTICLE_URL.endswith("/shem/schematics.html")


def test_db_init():
    db, path = _make_db()
    try:
        summary = db.get_db_summary()
        assert summary["total_articles"] == 0
        assert summary["last_session"] is None
        assert summary["by_category"] == {}
    finally:
        _clean_db(db, path)


def test_db_insert_and_query():
    db, path = _make_db()
    try:
        article = {
            "di": 123456,
            "title_ru": "Тестовая статья",
            "title_en": "Test article",
            "author": "Test Author",
            "author_url": "/authors/test.html",
            "date_published": "2025-01-01T12:00:00+03:00",
            "date_modified": "2025-01-02T12:00:00+03:00",
            "categories": "Схемы > Светотехника",
            "category_rz": "0205",
            "manufacturer": "Texas Instruments",
            "components": "LM324",
            "summary_ru": "Краткое описание",
            "body_ru": "<p>Полный текст статьи</p>",
            "english_url": "https://www.radiolocman.com/shem/schematics.html?di=123456",
            "article_url": "https://www.rlocman.ru/shem/schematics.html?di=123456",
            "tags": "LM324, LED",
            "component_count": 2,
            "content_hash": "abc123",
        }
        db.insert_article(article)

        assert db.article_exists(123456) is True
        assert db.article_exists(999999) is False

        fetched = db.get_article(123456)
        assert fetched["title_ru"] == "Тестовая статья"
        assert fetched["author"] == "Test Author"

        summary = db.get_db_summary()
        assert summary["total_articles"] == 1

        latest = db.get_latest_articles(10)
        assert len(latest) == 1

        by_rz = db.get_articles_by_rz("0205")
        assert len(by_rz) == 1

        assert len(db.get_unfetched_dis(100)) == 0
    finally:
        _clean_db(db, path)


def test_db_insert_duplicate():
    db, path = _make_db()
    try:
        a1 = {"di": 1, "title_ru": "First", "loaded_at": "2025-01-01"}
        a2 = {"di": 1, "title_ru": "Second", "loaded_at": "2025-01-02"}
        db.insert_article(a1)
        db.insert_article(a2)
        fetched = db.get_article(1)
        assert fetched["title_ru"] == "First"
    finally:
        _clean_db(db, path)


def test_db_flags():
    db, path = _make_db()
    try:
        a = {"di": 1, "title_ru": "Test", "loaded_at": "2025-01-01"}
        db.insert_article(a)

        db.mark_interesting([1])
        assert len(db.get_interesting()) == 1
        assert len(db.get_unread()) == 1

        db.mark_read([1])
        assert len(db.get_unread()) == 0

        db.unmark_interesting([1])
        assert len(db.get_interesting()) == 0

        db.unmark_read([1])
        assert len(db.get_unread()) == 1
    finally:
        _clean_db(db, path)


def test_db_sessions():
    db, path = _make_db()
    try:
        sid = db.create_session(rz="0205")
        assert sid is not None
        db.finish_session(sid, "completed", pages=5, articles=42)
        summary = db.get_db_summary()
        assert summary["last_session"]["status"] == "completed"
        assert summary["last_session"]["pages_fetched"] == 5
        assert summary["last_session"]["articles_fetched"] == 42
    finally:
        _clean_db(db, path)


def test_db_batch_insert():
    db, path = _make_db()
    try:
        articles = [
            {"di": i, "title_ru": f"Article {i}", "loaded_at": "2025-01-01"}
            for i in range(1, 6)
        ]
        db.insert_articles_batch(articles)
        assert db.get_db_summary()["total_articles"] == 5
    finally:
        _clean_db(db, path)