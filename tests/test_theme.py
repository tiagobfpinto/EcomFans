from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["username"] = "testuser"


def test_auth_pages_render_theme_bootstrap_before_styles(client):
    response = client.get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "ecomfans-theme" in html
    assert 'id="theme-color-meta"' in html
    assert html.index("ecomfans-theme") < html.index("css/style.css")
    assert 'static/js/theme.js' in html
    assert html.count("data-theme-toggle") == 1


def test_authenticated_pages_render_sidebar_and_mobile_theme_controls(client, test_user):
    _login(client, test_user)

    response = client.get("/dashboard")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "theme-toggle-sidebar" in html
    assert "theme-toggle-mobile" in html
    assert html.count("data-theme-toggle") == 2


def test_dark_theme_defines_semantic_tokens_and_tool_overrides():
    style = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    editorial = (PROJECT_ROOT / "static" / "css" / "editorial-theme.css").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")

    assert 'html[data-theme="dark"]' in style
    for token in ("--bg-primary", "--surface", "--text-primary", "--border", "--accent", "--focus-ring"):
        assert token in style
    for tool in (".notes-stage", ".fn-editor-header", ".sb-canvas-toolbar", ".social-item", ".vt-page"):
        assert tool in editorial
    assert "localStorage.setItem(STORAGE_KEY" in script
    assert "ecomfans:themechange" in script
