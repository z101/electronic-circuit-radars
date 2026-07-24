import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except PermissionError:
        pass


class TestCliDbSummary:
    def test_db_summary_empty(self, db_path):
        from database import Database
        db = Database(db_path)
        summary = db.get_db_summary()
        assert summary["total_projects"] == 0

    def test_db_summary_with_data(self, db_path):
        from database import Database
        db = Database(db_path)
        db.insert_project({"id": 1, "name": "Test", "summary": ""})
        db.insert_project({"id": 2, "name": "Test 2", "summary": ""})
        summary = db.get_db_summary()
        assert summary["total_projects"] == 2

    def test_db_schema(self, db_path):
        from database import Database
        db = Database(db_path)
        schema = db.get_schema()
        assert len(schema) >= 3


class TestCliLatest:
    def test_latest_empty(self, db_path):
        from database import Database
        db = Database(db_path)
        projects = db.get_latest_projects(10)
        assert projects == []

    def test_latest_ordering(self, db_path):
        from database import Database
        db = Database(db_path)
        for i in range(3):
            db.insert_project({"id": i, "name": f"P{i}", "summary": ""})
        latest = db.get_latest_projects(10)
        assert len(latest) == 3