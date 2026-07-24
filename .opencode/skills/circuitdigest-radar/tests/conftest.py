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
    <loc>https://circuitdigest.com/electronic-circuits/1-watt-led-dimmer</loc>
    <lastmod>2025-06-17T16:30:02+05:30</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
    <image:image>
      <image:loc>https://circuitdigest.com/sites/default/files/circuitdiagram/LED-Dimmer-Circuit.gif</image:loc>
      <image:title>1 Watt LED Dimmer Circuit Diagram</image:title>
    </image:image>
  </url>
  <url>
    <loc>https://circuitdigest.com/electronic-circuits/flashing-led-using-555-timer-ic</loc>
    <lastmod>2025-06-17T18:43:48+05:30</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://circuitdigest.com/news/some-news</loc>
    <lastmod>2025-06-18T10:00:00+05:30</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>'''


@pytest.fixture
def sample_article_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
<title>1 Watt LED Dimmer Circuit</title>
<meta name="description" content="This 1W LED DIMMER is a 555 IC based PWM circuit." />
<meta name="keywords" content="led dimmer, 555 timer, pwm" />
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [{
    "@type": "TechArticle",
    "datePublished": "2015-05-13T18:41:40+0530",
    "author": { "@type": "Person", "name": "Dilip Raja" }
  }]
}
</script>
</head>
<body>
<div class="row">
  <ol class="breadcrumb">
    <li><a href="/">Home</a></li>
    <li><a href="/electronic-circuits">Electronic Circuits</a></li>
    <li class="active">1 Watt LED Dimmer</li>
  </ol>
</div>
<h1>1 Watt LED Dimmer Circuit</h1>
Published May 13, 2015 2 Comments
<a href="/users/dilip-raja">Dilip Raja</a> Author
<img src="/sites/default/files/projectimage/LED-Dimmer-Project.jpg" alt="Project" />
<img src="https://circuitdigest.com/sites/default/files/circuitdiagram/LED-Dimmer-Circuit.gif" alt="Circuit Diagram" />
<div class="node__content">
  <p>The LED DIMMER is a 555 IC based PWM circuit.</p>
  <p>More content here.</p>
  <div class="field-name-field-tags">
    <a href="/tags/555-timer-circuits">555 timer circuits</a>
    <a href="/tags/led">LED</a>
  </div>
</div>
<section id="comments">
  <article class="comment">
    <span class="authorname">John</span>
    <time datetime="2016-03-10">March 10, 2016</time>
    <div class="comment-body"><p>Great article!</p></div>
  </article>
</section>
</body>
</html>"""