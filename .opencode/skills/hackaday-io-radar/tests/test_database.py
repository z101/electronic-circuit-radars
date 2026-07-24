import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from database import Database


class TestDatabaseInit:
    def test_creates_tables(self, db_path):
        db = Database(db_path)
        schema = db.get_schema()
        tables = {t["table"] for t in schema}
        assert "projects" in tables
        assert "scrape_sessions" in tables
        assert "search_scores" in tables

    def test_reinit_is_safe(self, db_path):
        db1 = Database(db_path)
        db2 = Database(db_path)
        assert db2.get_db_summary()["total_projects"] == 0


class TestProjectCrud:
    def test_insert_and_get(self, db, sample_project):
        db.insert_project(sample_project)
        p = db.get_project(206188)
        assert p is not None
        assert p["name"] == "DIY 3D Printed LED Roulette Wheel"
        assert p["owner_name"] == "Hulk"

    def test_insert_ignores_duplicate(self, db, sample_project):
        db.insert_project(sample_project)
        db.insert_project(sample_project)
        p = db.get_project(206188)
        assert p is not None

    def test_insert_json_fields(self, db, sample_project):
        db.insert_project(sample_project)
        p = db.get_project(206188)
        tags = json.loads(p["tags"]) if isinstance(p["tags"], str) else p["tags"]
        assert "led" in tags

    def test_exists(self, db, sample_project):
        assert not db.project_exists(206188)
        db.insert_project(sample_project)
        assert db.project_exists(206188)

    def test_get_nonexistent(self, db):
        assert db.get_project(999999) is None

    def test_get_all_projects(self, db, sample_project):
        db.insert_project(sample_project)
        all_p = db.get_all_projects()
        assert len(all_p) == 1
        assert all_p[0]["id"] == 206188


class TestSessions:
    def test_create_and_finish(self, db):
        sid = db.create_session()
        assert sid >= 1
        db.finish_session(sid, "completed", 100, 500)
        summary = db.get_db_summary()
        assert summary["last_session"]["status"] == "completed"
        assert summary["last_session"]["total_fetched"] == 100
        assert summary["last_session"]["last_offset"] == 500

    def test_interrupted_session(self, db):
        sid = db.create_session()
        db.finish_session(sid, "interrupted", 50, 200)
        summary = db.get_db_summary()
        assert summary["last_session"]["status"] == "interrupted"
        assert summary["last_session"]["last_offset"] == 200


class TestLatest:
    def test_latest_projects(self, db, sample_project):
        for i in range(5):
            p = dict(sample_project)
            p["id"] = 100 + i
            p["name"] = f"Project {i}"
            db.insert_project(p)
        latest = db.get_latest_projects(3)
        assert len(latest) == 3

    def test_latest_empty(self, db):
        assert db.get_latest_projects(10) == []


class TestGetByIds:
    def test_get_by_ids(self, db, sample_project):
        db.insert_project(sample_project)
        p2 = dict(sample_project)
        p2["id"] = 206189
        p2["name"] = "Second project"
        db.insert_project(p2)
        results = db.get_projects_by_ids([206188, 206189])
        assert len(results) == 2

    def test_get_by_ids_empty(self, db):
        assert db.get_projects_by_ids([]) == []


class TestSearchPipeline:
    def test_save_and_get_report(self, db, sample_project):
        db.insert_project(sample_project)
        db.save_search_result(
            206188, "abc123", "test-query", "LED projects",
            "content_hash_1", 85, comment="Very relevant"
        )
        status = db.get_search_status("abc123")
        assert status["scored"] == 1
        report = db.get_search_report("abc123")
        assert len(report) == 1
        assert report[0]["score"] == 85

    def test_search_candidates(self, db, sample_project):
        db.insert_project(sample_project)
        candidates = db.get_search_candidates_batch("nonexistent_hash", 0, 10)
        assert len(candidates) >= 1


class TestFlags:
    def test_mark_interesting(self, db, sample_project):
        db.insert_project(sample_project)
        db.mark_interesting([206188])
        interesting = db.get_interesting()
        assert len(interesting) == 1

    def test_unmark_interesting(self, db, sample_project):
        db.insert_project(sample_project)
        db.mark_interesting([206188])
        db.unmark_interesting([206188])
        assert len(db.get_interesting()) == 0

    def test_mark_read(self, db, sample_project):
        db.insert_project(sample_project)
        db.mark_read([206188])
        assert db.get_project(206188)["is_read"] == 1

    def test_list_unread(self, db, sample_project):
        db.insert_project(sample_project)
        unread = db.get_unread()
        assert len(unread) == 1


class TestSummary:
    def test_save_summary(self, db, sample_project):
        db.insert_project(sample_project)
        db.save_summary(206188, "Тестовая сводка")
        p = db.get_project(206188)
        assert p["summary_ru"] == "Тестовая сводка"

    def test_summary_status(self, db, sample_project):
        db.insert_project(sample_project)
        status = db.get_summary_status()
        assert status["total"] == 1
        assert status["pending"] == 1
        db.save_summary(206188, "Сводка")
        status = db.get_summary_status()
        assert status["summarized"] == 1

    def test_get_candidates(self, db, sample_project):
        db.insert_project(sample_project)
        candidates = db.get_candidates_for_summary(10)
        assert len(candidates) == 1


class TestSchema:
    def test_schema_output(self, db):
        schema = db.get_schema()
        assert len(schema) >= 3
        proj = [t for t in schema if t["table"] == "projects"][0]
        col_names = {c["name"] for c in proj["columns"]}
        assert "id" in col_names
        assert "summary_ru" in col_names