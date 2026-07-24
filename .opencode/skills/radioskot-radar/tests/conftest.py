import sys
from pathlib import Path

import pytest

_skill_root = Path(__file__).parent.parent
_src = _skill_root / "src"
sys.path.insert(0, str(_skill_root))
sys.path.insert(0, str(_src))


@pytest.fixture
def sample_sitemap_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>https://radioskot.com/publ/1/am-peredatchik-na-7-mgc</loc>
    <lastmod>2026-07-13T07:27:08+00:00</lastmod>
    <image:image>
      <image:loc>https://radioskot.com/wp-content/uploads/2026/07/am-transmitter.jpg</image:loc>
    </image:image>
  </url>
  <url>
    <loc>https://radioskot.com/publ/svetodiody/3/rgb-led-stroboskop</loc>
    <lastmod>2026-05-15T05:45:48+00:00</lastmod>
  </url>
  <url>
    <loc>https://radioskot.com/calc/3d-konstruktor-molekul-onlajn</loc>
    <lastmod>2026-07-21T08:31:43+00:00</lastmod>
  </url>
</urlset>'''


@pytest.fixture
def sample_article_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<title>AM передатчик на 7 МГц</title>
<meta name="description" content="Самодельный AM-передатчик на 7 МГц с MOSFET" />
<meta name="keywords" content="передатчик, AM, 7 МГц, MOSFET" />
</head>
<body>
<h1 class="entry-title">Самодельный AM-передатчик на 7 МГц</h1>
<div class="entry-meta">
  <span class="posted-on">
    <time class="entry-date" datetime="2026-07-13T11:00:00+03:00">13.07.2026</time>
  </span>
  <a rel="category tag" href="/publ/1">Схемы</a>
  <a rel="category tag" href="/publ/peredatchiki/11">Передатчики</a>
  <a rel="tag" href="/tags/am">AM</a>
  <a rel="tag" href="/tags/transmitter">transmitter</a>
</div>
<img class="wp-post-image" src="https://radioskot.com/wp-content/uploads/2026/07/am-transmitter.jpg" alt="" />
<div class="entry-content">
  <p>В основе радиопередатчика кварцевый генератор.</p>
  <p>Схема способна обеспечить мощность до 5 Вт.</p>
</div>
<footer class="entry-meta">
  <span class="cat-links">Рубрики: Схемы, Передатчики</span>
</footer>
</body>
</html>"""


@pytest.fixture
def sample_article_html_gp_style() -> str:
    """Article with gp-post-date class (article page style)."""
    return """<!DOCTYPE html>
<html>
<head>
<title>Резонансный волномер</title>
</head>
<body>
<h1 class="entry-title">Резонансный волномер</h1>
<div class="entry-meta">
  <time class="gp-post-date" datetime="2026-06-18T03:52:32+00:00">18.06.2026</time>
  <a rel="category tag" href="/publ/1">Большой сборник электросхем</a>
  <a rel="category tag" href="/publ/izmeriteli/15">Схемы измерительных приборов и тестеров</a>
  <a rel="tag" href="/tags/volnomer">волномер</a>
</div>
<img class="wp-post-image" src="https://radioskot.com/wp-content/uploads/2026/06/volnomer.jpg" alt="" />
<div class="entry-content">
  <p>Схема полезна как генератор сигналов.</p>
</div>
</body>
</html>"""