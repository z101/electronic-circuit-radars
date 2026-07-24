import hashlib
import re


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def compute_query_hash(query_text: str) -> str:
    return _sha256(normalize_query(query_text))


def compute_content_hash(title: str = "", summary: str = "") -> str:
    return _sha256((title or "") + "\n" + (summary or ""))


def compute_params_hash(params: dict) -> str:
    import json
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return _sha256(canonical)