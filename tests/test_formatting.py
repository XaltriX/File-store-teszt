from app.utils.formatting import chunk_text, esc, human_size, is_valid_url, share_url


def test_html_escaping_of_dynamic_text():
    assert esc("<b>x</b> & <script>") == "&lt;b&gt;x&lt;/b&gt; &amp; &lt;script&gt;"
    assert esc(None) == "" and esc(5) == "5"


def test_url_validation():
    assert is_valid_url("https://example.com/a?b=1") and is_valid_url("http://t.me/x.y")
    for bad in ("javascript:alert(1)", "ftp://x.com", "example.com", "https://", "https://nodot", "", "data:text/html,x"):
        assert not is_valid_url(bad)


def test_share_url_encodes_deep_link():
    u = share_url("https://t.me/b?start=ABC_-1")
    assert u == "https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fb%3Fstart%3DABC_-1"


def test_chunking_respects_limit():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(500))
    chunks = chunk_text(text, 1000)
    assert all(len(c) <= 1100 for c in chunks) and "".join(chunks).count("line") == 500


def test_human_size():
    assert human_size(None) == "-" and human_size(512) == "512 B" and human_size(2048) == "2.0 KB"
