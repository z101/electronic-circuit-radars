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


@pytest.fixture
def db(db_path):
    from database import Database
    db = Database(db_path)
    yield db
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


@pytest.fixture
def sample_project():
    return {
        "id": 206188,
        "name": "DIY 3D Printed LED Roulette Wheel",
        "slug": "206188-diy-3d-printed-led-roulette-wheel-esp32-c3-proje",
        "summary": "A digital roulette wheel with ESP32-C3 and CD4017",
        "description": "Full description text...",
        "owner_id": 317883,
        "owner_name": "Hulk",
        "created_at": "1783528923",
        "updated_at": "1783993696",
        "view_count": 100,
        "followers_count": 5,
        "tags": ["led", "esp32", "arduino", "roulette"],
        "content_hash": "abc123",
    }