from datetime import timedelta

from werkzeug.security import generate_password_hash

from billing_service import utc_now
from db import Funnel, FunnelPage, User, db
from funnels import DEFAULT_PAGE_TEMPLATE_ID, page_template_html
from tests.conftest import csrf_token


def _login(client, user_id, username="testuser"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = username


def _json(client, method, path, payload=None):
    return client.open(
        path,
        method=method,
        json=payload or {},
        headers={"X-CSRF-Token": csrf_token(client)},
    )


def _create_funnel(client, name="Campaign funnel"):
    response = _json(
        client,
        "POST",
        "/funnels",
        {"name": name, "description": "Conversion flow"},
    )
    assert response.status_code == 201
    return response.get_json()["funnel"]


def _create_page(client, funnel_id, slug="five-reasons", template_id="listicle"):
    response = _json(
        client,
        "POST",
        f"/funnels/{funnel_id}/pages",
        {"title": "Five reasons", "template_id": template_id, "slug": slug},
    )
    assert response.status_code == 201
    return response.get_json()["page"]


def test_funnels_require_login(client):
    response = client.get("/funnels")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_create_funnel_and_page_uses_selected_template(client, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(client, funnel["id"], slug="/Cinco Razões/")

    assert page["slug"] == "cinco-razoes"
    assert page["status"] == "draft"
    assert page["template_id"] == DEFAULT_PAGE_TEMPLATE_ID
    assert page["html_content"] == page_template_html("listicle")
    assert 'data-page-template="listicle"' in page["html_content"]
    assert 'data-template-bundle="styles.css"' in page["html_content"]
    assert 'data-template-bundle="app.js"' in page["html_content"]
    assert "/funnels/templates/listicle/assets/hair-roots-hero.webp" in page["html_content"]
    assert "ecomfans:funnel-template" in page["html_content"]
    assert ".template-editor-enabled .edit-dock" in page["html_content"]
    assert 'href="styles.css"' not in page["html_content"]
    assert 'src="app.js"' not in page["html_content"]

    detail = client.get(f"/funnels/{funnel['id']}")
    editor = client.get(f"/funnels/{funnel['id']}/pages/{page['id']}")
    assert detail.status_code == 200
    assert b"Five reasons" in detail.data
    assert b"Choose a template" in detail.data
    assert editor.status_code == 200
    assert b"Page template" in editor.data
    assert b"Listicle" in editor.data
    assert b"ex.html" not in editor.data


def test_korean_advertorial_template_is_selectable_and_editable(client, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(
        client, funnel["id"], slug="korean-roots", template_id="listicle-korean"
    )

    assert page["template_id"] == "listicle-korean"
    assert page["template_name"] == "Korean Root Cover Advertorial"
    assert page["html_content"] == page_template_html("listicle-korean")
    assert 'data-page-template="listicle-korean"' in page["html_content"]
    assert 'data-template-bundle="styles.css"' in page["html_content"]
    assert 'data-template-bundle="app.js"' in page["html_content"]
    assert 'href="styles.css"' not in page["html_content"]
    assert 'src="app.js"' not in page["html_content"]

    # the editor chrome the visual builder drives
    assert "ecomfans:funnel-template" in page["html_content"]
    assert ".template-editor-enabled .edit-dock" in page["html_content"]
    assert page["html_content"].count('data-image-key="') == 8

    # copy and assets specific to the advertorial
    assert "Hydrogen Peroxide" in page["html_content"]
    for asset in ("gray-roots-hero.webp", "peroxide-diagram.svg", "cuticle-diagram.svg"):
        assert f"/funnels/templates/listicle-korean/assets/{asset}" in page["html_content"]
        assert client.get(f"/funnels/templates/listicle-korean/assets/{asset}").status_code == 200

    detail = client.get(f"/funnels/{funnel['id']}")
    assert b"Korean Root Cover Advertorial" in detail.data


def test_unknown_page_template_is_rejected(client, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    response = _json(
        client,
        "POST",
        f"/funnels/{funnel['id']}/pages",
        {"title": "Unknown", "template_id": "advertorial", "slug": "unknown"},
    )
    assert response.status_code == 400
    assert "template" in response.get_json()["error"].lower()


def test_template_asset_is_publicly_served(client):
    path = "/funnels/templates/listicle/assets/hair-roots-hero.webp"
    response = client.get(path)
    assert response.status_code == 200
    assert response.content_type == "image/webp"


def test_reserved_and_duplicate_slugs_are_rejected(client, test_user):
    _login(client, test_user)
    first = _create_funnel(client, "First")
    second = _create_funnel(client, "Second")

    reserved = _json(
        client,
        "POST",
        f"/funnels/{first['id']}/pages",
        {"title": "Bad", "template_id": "listicle", "slug": "/products/sale"},
    )
    assert reserved.status_code == 400
    assert "reserved" in reserved.get_json()["error"].lower()

    _create_page(client, first["id"], slug="offer")
    duplicate = _json(
        client,
        "POST",
        f"/funnels/{second['id']}/pages",
        {"title": "Other offer", "template_id": "listicle", "slug": "offer"},
    )
    assert duplicate.status_code == 409


def test_draft_preview_and_published_root_slug(client, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(client, funnel["id"], slug="special-offer")

    assert client.get("/special-offer").status_code == 404
    preview = client.get(
        f"/funnels/{funnel['id']}/pages/{page['id']}/preview"
    )
    assert preview.status_code == 200
    assert preview.get_data(as_text=True) == page["html_content"]
    assert preview.headers["X-Robots-Tag"] == "noindex, nofollow"

    saved = _json(
        client,
        "PATCH",
        f"/funnels/{funnel['id']}/pages/{page['id']}",
        {
            "title": "Live offer",
            "slug": "special-offer",
            "status": "published",
            "html_content": "<!doctype html><html><body>Live funnel page</body></html>",
            "revision": page["revision"],
        },
    )
    assert saved.status_code == 200

    published = client.get("/special-offer")
    assert published.status_code == 200
    assert published.get_data(as_text=True).endswith("</html>")
    assert "sandbox" in published.headers["Content-Security-Policy"]


def test_page_revision_conflict_does_not_overwrite(client, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(client, funnel["id"])

    first = _json(
        client,
        "PATCH",
        f"/funnels/{funnel['id']}/pages/{page['id']}",
        {"title": "Changed once", "revision": 1},
    )
    assert first.status_code == 200
    assert first.get_json()["page"]["revision"] == 2

    stale = _json(
        client,
        "PATCH",
        f"/funnels/{funnel['id']}/pages/{page['id']}",
        {"title": "Stale overwrite", "revision": 1},
    )
    assert stale.status_code == 409
    assert stale.get_json()["conflict"] is True


def test_other_users_cannot_access_funnel_or_page(client, app, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(client, funnel["id"])

    with app.app_context():
        other = User(
            username="funnel-other",
            email="funnel-other@example.com",
            password_hash=generate_password_hash("Password1!"),
            plan_tier="free",
            credits=30,
            extra_credits=0,
            next_credit_reset_at=utc_now() + timedelta(days=30),
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id

    _login(client, other_id, "funnel-other")
    assert client.get(f"/funnels/{funnel['id']}").status_code == 404
    assert (
        client.get(f"/funnels/{funnel['id']}/pages/{page['id']}").status_code
        == 404
    )
    denied = _json(
        client,
        "PATCH",
        f"/funnels/{funnel['id']}/pages/{page['id']}",
        {"title": "No access", "revision": 1},
    )
    assert denied.status_code == 404


def test_duplicate_page_is_draft_and_delete_funnel_cascades(client, app, test_user):
    _login(client, test_user)
    funnel = _create_funnel(client)
    page = _create_page(client, funnel["id"], slug="campaign")
    duplicated = _json(
        client,
        "POST",
        f"/funnels/{funnel['id']}/pages/{page['id']}/duplicate",
    )
    assert duplicated.status_code == 201
    duplicate = duplicated.get_json()["page"]
    assert duplicate["slug"] == "campaign-copy"
    assert duplicate["status"] == "draft"

    deleted = _json(client, "DELETE", f"/funnels/{funnel['id']}")
    assert deleted.status_code == 200
    with app.app_context():
        assert db.session.get(Funnel, funnel["id"]) is None
        assert FunnelPage.query.filter_by(funnel_id=funnel["id"]).count() == 0


def test_mutating_routes_require_csrf(client, test_user):
    _login(client, test_user)
    response = client.post("/funnels", json={"name": "No token"})
    assert response.status_code == 400
    assert "csrf" in response.get_json()["error"].lower()
