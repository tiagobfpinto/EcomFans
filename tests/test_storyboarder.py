import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from billing_service import utc_now
from db import Product, StoryboardFrame, StoryboardProject, User, db
from storyboarder import (
    StoryboardParseError,
    parse_storyboard_clips,
    parse_storyboard_markdown,
)
from tests.conftest import csrf_token


EXAMPLE_MARKDOWN = Path(__file__).resolve().parents[1] / "example.md"
MEGA_EXAMPLE_MARKDOWN = Path(__file__).resolve().parents[1] / "mega_example.md"


def _login(client, user_id, username="testuser"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = username


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


def _multipart_post(client, path, data):
    return client.post(
        path,
        data=data,
        headers={"X-CSRF-Token": csrf_token(client)},
        content_type="multipart/form-data",
    )


def _value(item, name):
    if isinstance(item, dict):
        return item[name]
    return getattr(item, name)


def _parsed_base(parsed):
    return _value(parsed, "base_prompt")


def _parsed_frames(parsed):
    return _value(parsed, "frames")


def _parsed_blocks(parsed):
    return _value(parsed, "prompt_blocks")


def _frame_value(frame, name):
    aliases = {
        "sort_order": ("sort_order", "number", "clip_number"),
        "clip_type": ("clip_type", "type"),
        "transform_prompt": ("transform_prompt", "transform"),
        "video_prompt": ("video_prompt", "video"),
    }
    for candidate in aliases.get(name, (name,)):
        try:
            return _value(frame, candidate)
        except (AttributeError, KeyError):
            pass
    raise AssertionError(f"Parsed frame has no {name!r} field")


def _valid_markdown(
    *,
    base="Shared base prompt.",
    label="Opening hook",
    clip_type="talking_head",
    timestamp="0:00-0:04",
    photo="Photo A",
    transform="Bathroom, soft light",
    voiceover="Here is the hook.",
    video="Blink naturally.",
):
    return (
        "[BASE_PROMPT]\n"
        f"{base}\n\n"
        "[CLIP_1]\n"
        f"LABEL: {label}\n"
        f"TYPE: {clip_type}\n"
        f"TIMESTAMP: {timestamp}\n"
        f"PHOTO: {photo}\n"
        f"TRANSFORM: {transform}\n"
        f"VOICEOVER: {voiceover}\n"
        f"VIDEO: {video}\n"
    )


def _valid_clip_text(
    *,
    dialogue="Here is the hook.",
    timestamp="0:00–0:04",
    top="The creator speaks to camera.",
    bottom="A hand writes the key message.",
    reference="",
    text_to_image="",
    image_to_video="",
):
    text = (
        f"CLIP 1 — {timestamp}\n\n"
        f'Dialogue, spoken aloud with accurate lip sync: "{dialogue}"\n\n'
        f"Top: {top}\n\n"
        f"Bottom: {bottom}\n"
    )
    if text_to_image:
        text += f"\nText to image prompt: {text_to_image}\n"
    if image_to_video:
        text += f"\nImage to video prompt: {image_to_video}\n"
    if reference:
        text += f"\n{reference}\n"
    return text


def _create_product(app, user_id, name="Root Touch-Up"):
    with app.app_context():
        product = Product(user_id=user_id, name=name, context="Product context.")
        db.session.add(product)
        db.session.commit()
        return product.id


def _create_other_user(app):
    with app.app_context():
        user = User(
            username="otheruser",
            email="other@example.com",
            password_hash=generate_password_hash("Password1!"),
            plan_tier="free",
            credits=30,
            extra_credits=0,
            next_credit_reset_at=utc_now() + timedelta(days=30),
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _create_project(client, product_id, name="Launch storyboard"):
    response = _json_post(
        client,
        "/storyboarder/projects",
        {"name": name, "product_id": product_id},
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["project"]["id"]


def _import_markdown(client, project_id, markdown, *, filename="story.md", replace=False):
    data = {"file": (io.BytesIO(markdown.encode("utf-8")), filename, "text/markdown")}
    if replace:
        data["replace_existing"] = "true"
    return _multipart_post(
        client,
        f"/storyboarder/projects/{project_id}/import",
        data,
    )


def _create_from_text(client, project_id, base_prompt, clips_text, *, replace=False):
    return _json_post(
        client,
        f"/storyboarder/projects/{project_id}/clips",
        {
            "base_prompt": base_prompt,
            "clips_text": clips_text,
            "replace_existing": replace,
        },
    )


def _png_upload(name="frame.png", size=(1400, 900), color=(80, 150, 220)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer, name, "image/png"


@pytest.fixture()
def media_root(app, tmp_path):
    previous = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    yield tmp_path
    app.config["MEDIA_ROOT"] = previous


def test_parser_imports_real_example_with_one_frame_per_clip():
    parsed = parse_storyboard_clips(
        "Static single-take shot with consistent framing.",
        EXAMPLE_MARKDOWN.read_text(encoding="utf-8"),
    )

    frames = _parsed_frames(parsed)
    assert len(frames) == 9
    assert _parsed_base(parsed).startswith("Static single-take shot")
    assert _frame_value(frames[0], "sort_order") == 1
    assert _frame_value(frames[-1], "sort_order") == 9
    assert _frame_value(frames[2], "label") == "Clip 3"
    assert _frame_value(frames[2], "voiceover") == ""
    assert _frame_value(frames[2], "timestamp") == "0:18–0:30"
    assert _frame_value(frames[5], "clip_type") == "custom"
    assert _frame_value(frames[5], "photo") == "@mockup_brown"
    assert _frame_value(frames[0], "transform_prompt").startswith("[base block]")
    assert _frame_value(frames[0], "video_prompt").startswith(
        "Single continuous handheld-style take"
    )


def test_parser_imports_mega_example_and_preserves_named_block_variables():
    parsed = parse_storyboard_clips(
        "Global base prompt.",
        MEGA_EXAMPLE_MARKDOWN.read_text(encoding="utf-8"),
    )

    blocks = _parsed_blocks(parsed)
    frames = _parsed_frames(parsed)
    assert len(frames) == 9
    assert set(blocks) == {
        "SALON BASE BLOCK",
        "AVATAR",
        "CLIENT BLOCK",
        "PRODUCT",
        "NEGATIVE PROMPT",
    }
    assert blocks["SALON BASE BLOCK"].startswith("A modern hair salon")
    assert blocks["AVATAR"].startswith("A hair colorist")
    assert "@Mockup-dark-brown" in blocks["PRODUCT"]
    assert blocks["NEGATIVE PROMPT"].startswith("silent, no speech")
    assert frames[0].video_prompt.startswith("[SALON BASE BLOCK] [AVATAR]")
    assert "[PRODUCT]" in frames[4].video_prompt
    assert "[CLIENT BLOCK]" in frames[6].video_prompt
    assert "NEGATIVE PROMPT" not in frames[-1].video_prompt


def test_parser_saves_optional_text_to_image_and_image_to_video_prompts():
    clips_text = _valid_clip_text(
        text_to_image="Photorealistic bathroom scene.\nKeep the product label readable.",
        image_to_video="Slow handheld push-in.\nThe creator blinks naturally.",
    )

    frame = parse_storyboard_clips("Shared direction", clips_text).frames[0]

    assert frame.transform_prompt == (
        "Photorealistic bathroom scene.\nKeep the product label readable."
    )
    assert frame.video_prompt == (
        "Slow handheld push-in.\nThe creator blinks naturally."
    )


def test_parser_accepts_hyphenated_optional_prompt_names():
    clips_text = _valid_clip_text().replace(
        "Bottom: A hand writes the key message.",
        "Bottom: A hand writes the key message.\n\n"
        "TEXT-TO-IMAGE PROMPT: Still prompt\n\n"
        "IMAGE-TO-VIDEO PROMPT: Motion prompt",
    )

    frame = parse_storyboard_clips("Shared direction", clips_text).frames[0]

    assert frame.transform_prompt == "Still prompt"
    assert frame.video_prompt == "Motion prompt"


def test_parser_accepts_prompt_only_clip_without_dialogue_top_or_bottom():
    clips_text = (
        "CLIP_1 — 0:00–0:05\n\n"
        "**TEXT TO IMAGE PROMPT:**\n"
        "A product on a marble counter, studio lighting.\n\n"
        "## IMAGE TO VIDEO PROMPT\n"
        "The camera slowly pushes toward the product.\n"
    )

    frame = parse_storyboard_clips("Shared direction", clips_text).frames[0]

    assert frame.label == "Clip 1"
    assert frame.clip_type == "custom"
    assert frame.voiceover == ""
    assert frame.transform_prompt == (
        "A product on a marble counter, studio lighting."
    )
    assert frame.video_prompt == "The camera slowly pushes toward the product."


def test_parser_accepts_partial_visual_fields_when_other_fields_are_absent():
    clips_text = (
        "CLIP 1 — 0:00–0:05\n\n"
        "Bottom: A hand writes the offer.\n\n"
        "Image to video prompt: The marker moves naturally.\n"
    )

    frame = parse_storyboard_clips("Shared direction", clips_text).frames[0]

    assert frame.voiceover == ""
    assert frame.transform_prompt == "Bottom: A hand writes the offer."
    assert frame.video_prompt == "The marker moves naturally."


def test_parser_accepts_bom_crlf_mixed_case_any_field_order_and_multiline():
    markdown = (
        "\ufeff[base_prompt]\r\n"
        "Line one of base.\r\n"
        "Line two of base.\r\n\r\n"
        "[clip_1]\r\n"
        "video: First video line.\r\n"
        "Second video line: with a colon.\r\n"
        "VOICEOVER: First spoken line.\r\n\r\n"
        "Second spoken paragraph.\r\n"
        "photo: Photo A\r\n"
        "timestamp: 00:01:02\r\n"
        "TrAnSfOrM: A room: with warm light\r\n"
        "type: custom:type\r\n"
        "label: Olá — ação…\r\n"
    )

    parsed = parse_storyboard_markdown(markdown.encode("utf-8"))
    frame = _parsed_frames(parsed)[0]

    assert _parsed_base(parsed) == "Line one of base.\nLine two of base."
    assert _frame_value(frame, "label") == "Olá — ação…"
    assert _frame_value(frame, "clip_type") == "custom:type"
    assert _frame_value(frame, "timestamp") == "00:01:02"
    assert _frame_value(frame, "transform_prompt") == "A room: with warm light"
    assert _frame_value(frame, "voiceover") == (
        "First spoken line.\n\nSecond spoken paragraph."
    )
    assert _frame_value(frame, "video_prompt") == (
        "First video line.\nSecond video line: with a colon."
    )


@pytest.mark.parametrize(
    ("markdown", "clip_expected"),
    [
        ("[CLIP_1]\nLABEL: x\n", True),
        (_valid_markdown() + "\n[BASE_PROMPT]\nDuplicate", False),
        (_valid_markdown().replace("[BASE_PROMPT]", "[UNKNOWN]"), False),
        (
            _valid_markdown().replace(
                "[BASE_PROMPT]\nShared base prompt.\n\n[CLIP_1]",
                "[CLIP_1]",
            )
            + "\n[BASE_PROMPT]\nToo late",
            True,
        ),
        (_valid_markdown(base="   "), False),
        (_valid_markdown().replace("[CLIP_1]", "[CLIP_0]"), True),
        (_valid_markdown().replace("[CLIP_1]", "[CLIP_2]"), True),
        (_valid_markdown() + _valid_markdown().split("[CLIP_1]", 1)[1].join(["\n[CLIP_1]", ""]), True),
        (_valid_markdown().replace("LABEL: Opening hook\n", ""), True),
        (_valid_markdown().replace("LABEL: Opening hook", "LABEL:   "), True),
        (_valid_markdown().replace("TYPE: talking_head", "TYPE: a\nTYPE: b"), True),
        (_valid_markdown().replace("PHOTO: Photo A", "UNKNOWN_KEY: value\nPHOTO: Photo A"), True),
        (_valid_markdown().replace("LABEL: Opening hook", "LABEL Opening hook"), True),
    ],
    ids=[
        "missing-base",
        "duplicate-base",
        "unknown-section",
        "base-after-clip",
        "empty-base",
        "non-positive-clip",
        "non-sequential-clip",
        "duplicate-clip",
        "missing-field",
        "empty-field",
        "duplicate-field",
        "unknown-field",
        "malformed-field",
    ],
)
def test_parser_rejects_invalid_structure_with_line_and_clip_context(
    markdown, clip_expected
):
    with pytest.raises(StoryboardParseError) as error:
        parse_storyboard_markdown(markdown)

    message = str(error.value).lower()
    assert "line" in message
    if clip_expected:
        assert "clip" in message


def test_storyboard_page_requires_authentication(client):
    response = client.get("/storyboarder")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_project_creation_requires_name_and_owned_product(app, client, test_user):
    _login(client, test_user)
    own_product_id = _create_product(app, test_user)
    other_id = _create_other_user(app)
    foreign_product_id = _create_product(app, other_id, "Foreign product")

    missing_name = _json_post(
        client, "/storyboarder/projects", {"name": "   ", "product_id": own_product_id}
    )
    missing_product = _json_post(
        client, "/storyboarder/projects", {"name": "Campaign"}
    )
    foreign_product = _json_post(
        client,
        "/storyboarder/projects",
        {"name": "Campaign", "product_id": foreign_product_id},
    )
    created = _json_post(
        client,
        "/storyboarder/projects",
        {"name": " Campaign ", "product_id": own_product_id},
    )

    assert missing_name.status_code == 400
    assert missing_product.status_code == 400
    assert foreign_product.status_code == 400
    assert created.status_code == 201
    assert created.get_json()["project"]["name"] == "Campaign"
    with app.app_context():
        project = StoryboardProject.query.one()
        assert project.user_id == test_user
        assert project.product_id == own_product_id


def test_grid_exposes_product_filter_and_tags_each_project(app, client, test_user):
    _login(client, test_user)
    first_product = _create_product(app, test_user, "Serum")
    second_product = _create_product(app, test_user, "Comb")
    _create_project(client, first_product, "Serum launch")
    _create_project(client, second_product, "Comb launch")

    page = client.get("/storyboarder")

    assert page.status_code == 200
    assert b"Serum launch" in page.data
    assert b"Comb launch" in page.data
    assert b"All products" in page.data
    assert f'data-product-id="{first_product}"'.encode() in page.data
    assert f'data-product-id="{second_product}"'.encode() in page.data


def test_empty_project_exposes_auto_import_modal_hook(app, client, test_user):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))

    empty_page = client.get(f"/storyboarder/projects/{project_id}")

    assert empty_page.status_code == 200
    assert b'data-frame-count="0"' in empty_page.data
    assert b'id="sb-import-modal"' in empty_page.data
    assert b'id="sb-import-base-prompt"' in empty_page.data
    assert b'id="sb-import-clips-text"' in empty_page.data
    assert b'id="sb-import-file"' not in empty_page.data
    assert b"Not now" in empty_page.data

    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200
    populated_page = client.get(f"/storyboarder/projects/{project_id}")
    assert b'data-frame-count="1"' in populated_page.data


def test_storyboard_uses_canvas_inspector_and_omits_empty_fields(
    app, client, test_user
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    prompt_only = (
        "CLIP 1 — 0:00–0:05\n\n"
        "Text to image prompt: Product on a marble counter.\n\n"
        "Image to video prompt: Slow camera push-in.\n"
    )
    assert _create_from_text(
        client, project_id, "Shared direction", prompt_only
    ).status_code == 200

    page = client.get(f"/storyboarder/projects/{project_id}")

    assert page.status_code == 200
    assert b'id="sb-storyboard-canvas"' in page.data
    assert b'id="sb-canvas-world"' in page.data
    assert b'data-frame-node' in page.data
    assert b'id="sb-node-inspector"' in page.data
    assert b'data-copy-field="transform_prompt"' in page.data
    assert b'data-copy-field="video_prompt"' in page.data
    assert b'data-include-base-prompt' in page.data
    assert b"Copy + base" in page.data
    assert b'data-frame-field="photo"' not in page.data
    assert b'data-frame-field="voiceover"' not in page.data
    assert b'id="sb-frame-grid"' not in page.data


def test_named_blocks_persist_and_are_exposed_for_copy_time_expansion(
    app, client, test_user
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    mega_example = MEGA_EXAMPLE_MARKDOWN.read_text(encoding="utf-8")

    response = _create_from_text(
        client, project_id, "Global base prompt.", mega_example
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()["project"]
    assert payload["prompt_blocks"]["SALON BASE BLOCK"].startswith(
        "A modern hair salon"
    )
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        stored = json.loads(project.prompt_blocks_json)
        assert stored["AVATAR"].startswith("A hair colorist")
        assert len(project.frames) == 9

    page = client.get(f"/storyboarder/projects/{project_id}")
    assert page.status_code == 200
    assert b'id="sb-prompt-blocks-data"' in page.data
    assert b"[SALON BASE BLOCK]" in page.data
    assert b"[AVATAR]" in page.data
    assert b"Reusable prompt blocks" in page.data

    javascript = Path("static/js/storyboarder.js").read_text(encoding="utf-8")
    assert "resolvePromptVariables(exactValue)" in javascript
    assert "copyText(expandedPrompt.value)" in javascript


def test_pasted_text_persists_example_and_requires_confirmation_before_replace(
    app, client, test_user
):
    _login(client, test_user)
    product_id = _create_product(app, test_user)
    project_id = _create_project(client, product_id)
    example = EXAMPLE_MARKDOWN.read_text(encoding="utf-8")

    imported = _create_from_text(client, project_id, "Shared direction", example)
    conflict = _create_from_text(
        client, project_id, "Replacement", _valid_clip_text()
    )

    assert imported.status_code == 200, imported.get_data(as_text=True)
    assert conflict.status_code == 409
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        assert project.base_prompt == "Shared direction"
        assert len(project.frames) == 9
        assert project.frames[2].voiceover == ""

    replaced = _create_from_text(
        client,
        project_id,
        "Replacement",
        _valid_clip_text(
            text_to_image="A precise still-image prompt.",
            image_to_video="A precise motion prompt.",
        ),
        replace=True,
    )

    assert replaced.status_code == 200
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        assert project.base_prompt == "Replacement"
        assert len(project.frames) == 1
        assert project.frames[0].sort_order == 1
        assert project.frames[0].transform_prompt == "A precise still-image prompt."
        assert project.frames[0].video_prompt == "A precise motion prompt."


def test_import_rejects_extension_encoding_empty_and_size_limit(
    app, client, test_user
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))

    wrong_extension = _import_markdown(
        client, project_id, _valid_markdown(), filename="story.txt"
    )
    invalid_utf8 = _multipart_post(
        client,
        f"/storyboarder/projects/{project_id}/import",
        {"file": (io.BytesIO(b"\xff\xfe\xfa"), "story.md", "text/markdown")},
    )
    empty = _multipart_post(
        client,
        f"/storyboarder/projects/{project_id}/import",
        {"file": (io.BytesIO(b""), "story.md", "text/markdown")},
    )
    oversized = _multipart_post(
        client,
        f"/storyboarder/projects/{project_id}/import",
        {"file": (io.BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "story.md", "text/markdown")},
    )

    assert wrong_extension.status_code == 400
    assert invalid_utf8.status_code == 400
    assert empty.status_code == 400
    assert oversized.status_code in {400, 413}
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        assert project.base_prompt == ""
        assert len(project.frames) == 0


def test_failed_reimport_rolls_back_entire_existing_storyboard(
    app, client, test_user
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    initial = _valid_markdown(base="Keep this", label="Keep frame")
    assert _import_markdown(client, project_id, initial).status_code == 200

    malformed = _valid_markdown(base="Do not keep").replace("VIDEO: Blink naturally.\n", "")
    response = _import_markdown(client, project_id, malformed, replace=True)

    assert response.status_code == 400
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        assert project.base_prompt == "Keep this"
        assert [frame.label for frame in project.frames] == ["Keep frame"]


def test_project_and_frame_patch_autosave_fields(app, client, test_user):
    _login(client, test_user)
    product_id = _create_product(app, test_user, "First")
    replacement_product_id = _create_product(app, test_user, "Second")
    project_id = _create_project(client, product_id)
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200

    with app.app_context():
        frame_id = StoryboardFrame.query.filter_by(project_id=project_id).one().id

    project_response = _json_patch(
        client,
        f"/storyboarder/projects/{project_id}",
        {
            "name": "Updated project",
            "product_id": replacement_product_id,
            "base_prompt": " Updated base prompt. ",
        },
    )
    frame_payload = {
        "label": "Updated label",
        "clip_type": "custom",
        "timestamp": "1:02:03",
        "photo": "Photo B: close-up",
        "transform_prompt": "Transform line one\nline two",
        "voiceover": "Voice: keep this colon",
        "video_prompt": "Move naturally.",
    }
    frame_response = _json_patch(
        client, f"/storyboarder/frames/{frame_id}", frame_payload
    )

    assert project_response.status_code == 200
    assert frame_response.status_code == 200
    with app.app_context():
        project = db.session.get(StoryboardProject, project_id)
        frame = db.session.get(StoryboardFrame, frame_id)
        assert project.name == "Updated project"
        assert project.product_id == replacement_product_id
        assert project.base_prompt == " Updated base prompt. "
        for field, expected in frame_payload.items():
            assert getattr(frame, field) == expected


def test_projects_frames_imports_and_media_are_scoped_to_owner(
    app, client, test_user, media_root
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200
    with app.app_context():
        frame_id = StoryboardFrame.query.one().id

    uploaded = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload()},
    )
    assert uploaded.status_code == 200

    other_id = _create_other_user(app)
    _login(client, other_id, "otheruser")

    assert client.get(f"/storyboarder/projects/{project_id}").status_code == 404
    assert _json_patch(
        client, f"/storyboarder/projects/{project_id}", {"name": "Stolen"}
    ).status_code == 404
    assert _json_delete(client, f"/storyboarder/projects/{project_id}").status_code == 404
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 404
    assert _json_patch(
        client, f"/storyboarder/frames/{frame_id}", {"label": "Stolen"}
    ).status_code == 404
    assert _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload("stolen.png")},
    ).status_code == 404
    assert client.get(f"/media/storyboard-thumbnails/{frame_id}").status_code == 404


def test_thumbnail_upload_validates_resizes_replaces_and_serves(
    app, client, test_user, media_root
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200
    with app.app_context():
        frame_id = StoryboardFrame.query.one().id

    first = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload(size=(1600, 1000))},
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    with app.app_context():
        frame = db.session.get(StoryboardFrame, frame_id)
        first_path = media_root / frame.thumbnail_storage_path
        assert first_path.is_file()
        first_bytes = first_path.read_bytes()
        with Image.open(first_path) as image:
            assert max(image.size) <= 720
        assert frame.thumbnail_mime_type in {"image/webp", "image/jpeg"}

    served = client.get(f"/media/storyboard-thumbnails/{frame_id}")
    assert served.status_code == 200
    assert served.content_type.startswith("image/")

    second = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload("replacement.png", color=(220, 80, 100))},
    )
    assert second.status_code == 200
    with app.app_context():
        frame = db.session.get(StoryboardFrame, frame_id)
        second_path = media_root / frame.thumbnail_storage_path
        assert second_path.is_file()
        assert second_path.read_bytes() != first_bytes
    if second_path != first_path:
        assert not first_path.exists()

    fake_image = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": (io.BytesIO(b"not really an image"), "fake.png", "image/png")},
    )
    oversized = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {
            "thumbnail": (
                io.BytesIO(b"x" * (8 * 1024 * 1024 + 1)),
                "huge.png",
                "image/png",
            )
        },
    )
    assert fake_image.status_code == 400
    assert oversized.status_code in {400, 413}
    assert second_path.exists()


def test_failed_thumbnail_replacement_preserves_previous_file_and_metadata(
    app, client, test_user, media_root, monkeypatch
):
    _login(client, test_user)
    project_id = _create_project(client, _create_product(app, test_user))
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200
    with app.app_context():
        frame_id = StoryboardFrame.query.one().id

    assert _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload("original.png", color=(30, 80, 140))},
    ).status_code == 200
    with app.app_context():
        frame = db.session.get(StoryboardFrame, frame_id)
        original_path = frame.thumbnail_storage_path
        original_filename = frame.thumbnail_filename
        original_bytes = (media_root / original_path).read_bytes()

    def _fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db.session, "commit", _fail_commit)
    response = _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload("replacement.png", color=(220, 40, 50))},
    )

    assert response.status_code == 500
    with app.app_context():
        frame = db.session.get(StoryboardFrame, frame_id)
        assert frame.thumbnail_storage_path == original_path
        assert frame.thumbnail_filename == original_filename
    assert (media_root / original_path).read_bytes() == original_bytes


def test_reimport_and_project_delete_remove_thumbnail_files(
    app, client, test_user, media_root
):
    _login(client, test_user)
    product_id = _create_product(app, test_user)
    project_id = _create_project(client, product_id)
    assert _import_markdown(client, project_id, _valid_markdown()).status_code == 200
    with app.app_context():
        frame_id = StoryboardFrame.query.one().id
    assert _multipart_post(
        client,
        f"/storyboarder/frames/{frame_id}/thumbnail",
        {"thumbnail": _png_upload("before-reimport.png")},
    ).status_code == 200
    with app.app_context():
        old_path = media_root / db.session.get(
            StoryboardFrame, frame_id
        ).thumbnail_storage_path
    assert old_path.exists()

    assert _import_markdown(
        client, project_id, _valid_markdown(label="Replacement"), replace=True
    ).status_code == 200
    assert not old_path.exists()

    with app.app_context():
        replacement_frame_id = StoryboardFrame.query.one().id
    assert _multipart_post(
        client,
        f"/storyboarder/frames/{replacement_frame_id}/thumbnail",
        {"thumbnail": _png_upload("before-delete.png")},
    ).status_code == 200
    with app.app_context():
        delete_path = media_root / db.session.get(
            StoryboardFrame, replacement_frame_id
        ).thumbnail_storage_path

    deleted = _json_delete(client, f"/storyboarder/projects/{project_id}")

    assert deleted.status_code == 200
    assert not delete_path.exists()
    with app.app_context():
        assert db.session.get(StoryboardProject, project_id) is None
        assert StoryboardFrame.query.count() == 0


def test_product_linked_to_storyboard_cannot_be_deleted(app, client, test_user):
    _login(client, test_user)
    product_id = _create_product(app, test_user)
    project_id = _create_project(client, product_id)

    response = client.post(
        f"/products/{product_id}/delete",
        data={"_csrf_token": csrf_token(client)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"storyboard" in response.data.lower()
    with app.app_context():
        assert db.session.get(Product, product_id) is not None
        assert db.session.get(StoryboardProject, project_id) is not None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/storyboarder/projects"),
        ("patch", "/storyboarder/projects/1"),
        ("delete", "/storyboarder/projects/1"),
        ("post", "/storyboarder/projects/1/clips"),
        ("post", "/storyboarder/projects/1/import"),
        ("patch", "/storyboarder/frames/1"),
        ("post", "/storyboarder/frames/1/thumbnail"),
    ],
)
def test_mutating_endpoints_require_csrf(client, test_user, method, path):
    _login(client, test_user)

    response = getattr(client, method)(path, json={})

    assert response.status_code == 400
