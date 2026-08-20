from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

from billing_service import utc_now
from db import NoteBoard, User, db
from tests.conftest import csrf_token


def _login(client, user_id, username="testuser"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["username"] = username


def _json_post(client, path, payload=None):
    return client.post(
        path,
        json=payload or {},
        headers={"X-CSRF-Token": csrf_token(client)},
    )


def _json_patch(client, path, payload):
    return client.patch(
        path,
        json=payload,
        headers={"X-CSRF-Token": csrf_token(client)},
    )


def _json_delete(client, path):
    return client.delete(path, headers={"X-CSRF-Token": csrf_token(client)})


def _create_board(client, name="Campaign map"):
    response = _json_post(client, "/notes/boards", {"name": name})
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["board"]


def _object(object_id="shape-1", object_type="rectangle"):
    item = {
        "id": object_id,
        "type": object_type,
        "x": 120,
        "y": -40,
        "width": 180,
        "height": 110,
        "rotation": 12.5,
        "fill": "#ddd8fb",
        "stroke": "#7463df",
        "stroke_width": 2,
        "opacity": 1,
    }
    if object_type in {"line", "arrow"}:
        item.update(
            {
                "width": -80,
                "height": 45,
                "fill": "transparent",
                "rotation": 0,
            }
        )
    if object_type in {"text", "sticky"}:
        item.update(
            {
                "text": "A literal <script>alert(1)</script> note",
                "font_size": 24,
                "font_weight": 600,
                "text_align": "left",
                "text_color": "#292338",
            }
        )
    return item


def _document(objects=None):
    return {
        "schema_version": 1,
        "viewport": {"x": 42, "y": -18, "zoom": 1.25},
        "objects": objects or [],
    }


def _create_other_user(app):
    with app.app_context():
        user = User(
            username="notes-other",
            email="notes-other@example.com",
            password_hash=generate_password_hash("Password1!"),
            plan_tier="free",
            credits=30,
            extra_credits=0,
            next_credit_reset_at=utc_now() + timedelta(days=30),
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_notes_pages_require_authentication(client):
    assert client.get("/notes").status_code == 302
    assert client.get("/notes/boards/1").status_code == 302


def test_create_list_and_open_empty_board(app, client, test_user):
    _login(client, test_user)

    created = _create_board(client, "  Holiday ideas  ")

    assert created["name"] == "Holiday ideas"
    assert created["revision"] == 1
    assert created["object_count"] == 0
    assert created["document"] == _document([]) | {"viewport": {"x": 0, "y": 0, "zoom": 1}}

    gallery = client.get("/notes")
    editor = client.get(f"/notes/boards/{created['id']}")
    assert gallery.status_code == 200
    assert b"Holiday ideas" in gallery.data
    assert b"0 objects" in gallery.data
    assert editor.status_code == 200
    assert b'id="notes-editor"' in editor.data
    assert b'id="notes-bootstrap"' in editor.data
    with app.app_context():
        board = db.session.get(NoteBoard, created["id"])
        assert board.user_id == test_user
        assert board.object_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "   "},
        {"name": "Board", "unexpected": True},
    ],
)
def test_create_board_validates_payload(client, test_user, payload):
    _login(client, test_user)
    response = _json_post(client, "/notes/boards", payload)
    assert response.status_code == 400


def test_save_document_renames_counts_objects_and_renders_text_safely(
    app, client, test_user
):
    _login(client, test_user)
    board = _create_board(client)
    objects = [
        _object("rect", "rectangle"),
        _object("circle", "ellipse"),
        _object("line", "line"),
        _object("arrow", "arrow"),
        _object("text", "text"),
        _object("sticky", "sticky"),
    ]

    response = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"name": "Updated map", "document": _document(objects), "revision": 1},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    saved = response.get_json()["board"]
    assert saved["revision"] == 2
    assert saved["name"] == "Updated map"
    assert saved["object_count"] == 6
    assert saved["document"]["objects"][4]["text"].startswith("A literal <script>")

    editor = client.get(f"/notes/boards/{board['id']}")
    assert b"<script>alert(1)</script>" not in editor.data
    assert b"\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in editor.data
    with app.app_context():
        persisted = db.session.get(NoteBoard, board["id"])
        assert persisted.object_count == 6
        assert persisted.revision == 2


def test_stale_revision_returns_latest_board_without_overwrite(client, test_user):
    _login(client, test_user)
    board = _create_board(client)
    first = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"name": "First save", "document": _document([_object()]), "revision": 1},
    )
    stale = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"name": "Stale save", "document": _document([]), "revision": 1},
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    payload = stale.get_json()
    assert payload["conflict"] is True
    assert payload["board"]["name"] == "First save"
    assert payload["board"]["revision"] == 2
    assert payload["board"]["object_count"] == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda document: document.update({"schema_version": 2}),
        lambda document: document["viewport"].update({"zoom": 8}),
        lambda document: document["objects"].append(_object("shape-1")),
        lambda document: document["objects"][0].update({"fill": "red"}),
        lambda document: document["objects"][0].update({"unknown": True}),
    ],
)
def test_document_schema_validation_rejects_bad_data(client, test_user, mutator):
    _login(client, test_user)
    board = _create_board(client)
    document = _document([_object()])
    mutator(document)

    response = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"document": document, "revision": 1},
    )

    assert response.status_code == 400


def test_document_rejects_duplicate_ids_and_object_limit(client, test_user):
    _login(client, test_user)
    board = _create_board(client)
    duplicate_ids = _document([_object("same"), _object("same", "ellipse")])
    too_many = _document([_object(f"shape-{index}") for index in range(1001)])

    duplicate = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"document": duplicate_ids, "revision": 1},
    )
    excessive = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"document": too_many, "revision": 1},
    )

    assert duplicate.status_code == 400
    assert excessive.status_code == 400


def test_document_rejects_payload_over_two_megabytes(client, test_user):
    _login(client, test_user)
    board = _create_board(client)
    objects = []
    for index in range(110):
        item = _object(f"text-{index}", "text")
        item["text"] = "x" * 20000
        objects.append(item)

    response = _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"document": _document(objects), "revision": 1},
    )

    assert response.status_code == 400
    assert "2 MB" in response.get_json()["error"]


def test_duplicate_and_delete_board(app, client, test_user):
    _login(client, test_user)
    board = _create_board(client, "Research")
    assert _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"document": _document([_object()]), "revision": 1},
    ).status_code == 200

    duplicated = _json_post(client, f"/notes/boards/{board['id']}/duplicate")

    assert duplicated.status_code == 201
    copy = duplicated.get_json()["board"]
    assert copy["name"] == "Research copy"
    assert copy["revision"] == 1
    assert copy["object_count"] == 1
    assert copy["document"]["objects"][0]["id"] == "shape-1"

    deleted = _json_delete(client, f"/notes/boards/{board['id']}")
    assert deleted.status_code == 200
    with app.app_context():
        assert db.session.get(NoteBoard, board["id"]) is None
        assert db.session.get(NoteBoard, copy["id"]) is not None


def test_board_access_is_scoped_to_owner(app, client, test_user):
    _login(client, test_user)
    board = _create_board(client)
    other_id = _create_other_user(app)
    _login(client, other_id, "notes-other")

    assert client.get(f"/notes/boards/{board['id']}").status_code == 404
    assert _json_patch(
        client,
        f"/notes/boards/{board['id']}",
        {"name": "Stolen", "revision": 1},
    ).status_code == 404
    assert _json_post(client, f"/notes/boards/{board['id']}/duplicate").status_code == 404
    assert _json_delete(client, f"/notes/boards/{board['id']}").status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/notes/boards"),
        ("patch", "/notes/boards/1"),
        ("post", "/notes/boards/1/duplicate"),
        ("delete", "/notes/boards/1"),
    ],
)
def test_mutating_routes_require_csrf(client, test_user, method, path):
    _login(client, test_user)
    response = getattr(client, method)(path, json={})
    assert response.status_code == 400
    assert response.is_json
    assert "csrf" in response.get_json()["error"].lower()
