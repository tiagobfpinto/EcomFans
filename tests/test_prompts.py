import io

import pytest
from PIL import Image

from db import (
    Product,
    ProductImage,
    PromptLibraryItem,
    PromptLibraryTarget,
    PromptLibraryTargetImage,
    PromptLibraryThumbnail,
    db,
)
from tests.conftest import csrf_token


@pytest.fixture()
def media_root(app, tmp_path):
    old_root = app.config.get("MEDIA_ROOT")
    app.config["MEDIA_ROOT"] = str(tmp_path)
    yield tmp_path
    app.config["MEDIA_ROOT"] = old_root


def _login(client, user_id, username="testuser"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = username


def _png_bytes(size=(1400, 980), color=(80, 150, 220)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _thumbnail_upload(name="thumb.png", size=(1400, 980)):
    return io.BytesIO(_png_bytes(size=size)), name, "image/png"


def test_save_prompt_groups_character_and_resizes_thumbnails(
    app,
    client,
    test_user,
    media_root,
):
    token = csrf_token(client)
    _login(client, test_user)

    response = client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "target_type": "character",
            "target_name": "Jane",
            "prompt_name": "Jane studio chrome",
            "prompt_text": "Jane holding a chrome product under soft studio light.",
            "thumbnails": [
                _thumbnail_upload("one.png"),
                _thumbnail_upload("two.png", size=(1200, 1200)),
                _thumbnail_upload("three.png", size=(900, 1400)),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Jane" in response.data

    with app.app_context():
        target = PromptLibraryTarget.query.filter_by(
            user_id=test_user,
            target_type="character",
            name="Jane",
        ).one()
        prompt = PromptLibraryItem.query.filter_by(target_id=target.id).one()
        assert prompt.name == "Jane studio chrome"
        assert "chrome product" in prompt.prompt_text
        assert len(prompt.thumbnails) == 3

        first_thumbnail = prompt.thumbnails[0]
        stored_path = media_root / first_thumbnail.storage_path
        assert stored_path.exists()
        with Image.open(stored_path) as stored_image:
            assert max(stored_image.width, stored_image.height) <= 480
        assert first_thumbnail.mime_type in {"image/webp", "image/jpeg"}

    media_response = client.get(f"/media/prompt-thumbnails/{first_thumbnail.id}")
    assert media_response.status_code == 200
    assert media_response.content_type.startswith("image/")


def test_save_prompt_links_existing_product(app, client, test_user, media_root):
    with app.app_context():
        product = Product(
            user_id=test_user,
            name="Magic stick",
            context="Portable product for creative tests.",
        )
        db.session.add(product)
        db.session.commit()
        product_id = product.id

    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "target_type": "product",
            "product_id": str(product_id),
            "target_name": "",
            "prompt_name": "Magic pedestal",
            "prompt_text": "Magic stick on a clean reflective pedestal.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Magic stick" in response.data

    with app.app_context():
        target = PromptLibraryTarget.query.filter_by(
            user_id=test_user,
            target_type="product",
            name="Magic stick",
        ).one()
        assert target.product_id == product_id
        assert PromptLibraryItem.query.filter_by(target_id=target.id).count() == 1


def test_delete_prompt_removes_thumbnail_file_and_keeps_target_row(
    app,
    client,
    test_user,
    media_root,
):
    token = csrf_token(client)
    _login(client, test_user)
    client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "target_type": "background",
            "target_name": "Blue studio",
            "prompt_name": "Blue reflection",
            "prompt_text": "Blue studio wall with a soft floor reflection.",
            "thumbnails": [_thumbnail_upload("blue.png")],
        },
        content_type="multipart/form-data",
    )

    with app.app_context():
        prompt = PromptLibraryItem.query.one()
        prompt_id = prompt.id
        thumbnail = PromptLibraryThumbnail.query.one()
        stored_path = media_root / thumbnail.storage_path
        assert stored_path.exists()

    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        f"/prompts/{prompt_id}/delete",
        data={"_csrf_token": token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert not stored_path.exists()
    with app.app_context():
        assert PromptLibraryItem.query.count() == 0
        assert PromptLibraryTarget.query.filter_by(name="Blue studio").count() == 1
        assert PromptLibraryThumbnail.query.count() == 0


def test_rejects_more_than_three_thumbnails(app, client, test_user, media_root):
    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "link_kind": "none",
            "prompt_name": "High contrast detail",
            "prompt_text": "High contrast ecommerce detail image.",
            "thumbnails": [
                _thumbnail_upload("one.png"),
                _thumbnail_upload("two.png"),
                _thumbnail_upload("three.png"),
                _thumbnail_upload("four.png"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Upload up to 3 images" in response.data
    with app.app_context():
        assert PromptLibraryItem.query.count() == 0


def test_create_character_target_with_reference_photo(app, client, test_user, media_root):
    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        "/prompts/targets",
        data={
            "_csrf_token": token,
            "target_type": "character",
            "name": "Jane",
            "description": "Main spokesperson.",
            "images": [_thumbnail_upload("jane.png", size=(1200, 900))],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Jane" in response.data
    with app.app_context():
        target = PromptLibraryTarget.query.filter_by(
            user_id=test_user,
            target_type="character",
            name="Jane",
        ).one()
        assert target.description == "Main spokesperson."
        image = PromptLibraryTargetImage.query.filter_by(target_id=target.id).one()
        stored_path = media_root / image.storage_path
        assert stored_path.exists()
        with Image.open(stored_path) as stored_image:
            assert max(stored_image.width, stored_image.height) <= 480


def test_create_product_target_uses_existing_product_model(app, client, test_user, media_root):
    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        "/prompts/targets",
        data={
            "_csrf_token": token,
            "target_type": "product",
            "name": "Magic stick",
            "context": "Portable product for creative tests.",
            "images": [_thumbnail_upload("product.png", size=(500, 500))],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Magic stick" in response.data
    with app.app_context():
        product = Product.query.filter_by(user_id=test_user, name="Magic stick").one()
        assert product.context == "Portable product for creative tests."
        assert ProductImage.query.filter_by(product_id=product.id).count() == 1
        target = PromptLibraryTarget.query.filter_by(
            user_id=test_user,
            target_type="product",
            product_id=product.id,
        ).one()
        assert target.name == "Magic stick"


def test_save_prompt_can_be_unlinked(app, client, test_user, media_root):
    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "link_kind": "none",
            "prompt_name": "Standalone",
            "prompt_text": "Standalone prompt without a linked asset.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"unlinked" in response.data
    with app.app_context():
        prompt = PromptLibraryItem.query.one()
        assert prompt.name == "Standalone"
        assert prompt.target_id is None


def test_unlinked_prompt_can_be_linked_after_creation(app, client, test_user, media_root):
    with app.app_context():
        target = PromptLibraryTarget(
            user_id=test_user,
            target_type="character",
            name="Jane",
        )
        db.session.add(target)
        db.session.commit()
        target_id = target.id

    token = csrf_token(client)
    _login(client, test_user)
    client.post(
        "/prompts",
        data={
            "_csrf_token": token,
            "link_kind": "none",
            "prompt_name": "Unlinked Jane idea",
            "prompt_text": "A portrait prompt that should be linked later.",
        },
        follow_redirects=True,
    )

    with app.app_context():
        prompt = PromptLibraryItem.query.one()
        prompt_id = prompt.id
        assert prompt.target_id is None

    token = csrf_token(client)
    _login(client, test_user)
    response = client.post(
        f"/prompts/{prompt_id}/link",
        data={
            "_csrf_token": token,
            "link_kind": "character",
            "character_target_id": str(target_id),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Unlinked Jane idea" in response.data
    with app.app_context():
        prompt = db.session.get(PromptLibraryItem, prompt_id)
        assert prompt.target_id == target_id
