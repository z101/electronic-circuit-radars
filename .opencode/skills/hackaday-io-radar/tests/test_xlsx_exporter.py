import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestXlsxExport:
    @pytest.fixture
    def sample_projects(self):
        return [
            {
                "id": 206188,
                "name": "LED Roulette",
                "slug": "206188-led-roulette",
                "owner_name": "Hulk",
                "created_at": "1783528923",
                "tags": ["led", "esp32"],
                "view_count": 100,
                "followers_count": 5,
                "is_interesting": True,
                "is_read": False,
                "summary_ru": None,
            }
        ]

    def test_export_xlsx_creates_file(self, sample_projects):
        from xlsx_exporter import export_to_xlsx
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        try:
            result = export_to_xlsx(sample_projects, path)
            assert os.path.exists(path)
            assert result == path
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_export_with_columns(self, sample_projects):
        from xlsx_exporter import export_to_xlsx, BASE_COLUMNS, BASE_HEADER_NAMES
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        try:
            result = export_to_xlsx(
                sample_projects, path,
                columns=BASE_COLUMNS,
                header_names=BASE_HEADER_NAMES
            )
            assert os.path.exists(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_flatten_tags(self):
        from xlsx_exporter import _flatten_tags
        assert _flatten_tags(["a", "b"]) == "a, b"
        assert _flatten_tags('["x","y"]') == "x, y"
        assert _flatten_tags("") == ""
        assert _flatten_tags(None) == ""


class TestXlsxImport:
    def test_import_nonexistent_file(self):
        from xlsx_exporter import import_from_xlsx
        class FakeDB:
            def get_project(self, pid): return None
        with pytest.raises(FileNotFoundError):
            import_from_xlsx("nonexistent.xlsx", FakeDB())