"""
Tests for auth security changes:
  - Password validation
  - Login brute-force lockout
  - DB-stored password reset tokens
  - Password reset cooldown
  - CSRF protection
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from auth import _password_valid, _make_reset_token, _verify_reset_token
from billing_service import utc_now
from db import db, User
from tests.conftest import post_with_csrf, csrf_token


# ── password validation ───────────────────────────────────────────────────────

class TestPasswordValidation:
    def test_too_short_rejected(self):
        valid, msg = _password_valid("Ab1!")
        assert not valid
        assert "8 characters" in msg

    def test_exactly_8_with_digit_accepted(self):
        valid, _ = _password_valid("abcdefg1")
        assert valid

    def test_no_digit_or_special_rejected(self):
        valid, msg = _password_valid("abcdefghij")
        assert not valid
        assert "number or special" in msg

    def test_digit_satisfies_requirement(self):
        valid, _ = _password_valid("abcdefgh1")
        assert valid

    def test_special_char_satisfies_requirement(self):
        valid, _ = _password_valid("abcdefgh!")
        assert valid

    def test_space_does_not_satisfy_special_requirement(self):
        # "hello world" has a space but no digit or real special char — must fail
        valid, msg = _password_valid("hello world")
        assert not valid
        assert "number or special" in msg

    def test_space_plus_digit_is_accepted(self):
        # Space alone doesn't help, but a digit alongside space is fine
        valid, _ = _password_valid("hello wor1d")
        assert valid

    def test_only_digits_accepted(self):
        valid, _ = _password_valid("12345678")
        assert valid

    def test_both_digit_and_special_accepted(self):
        valid, _ = _password_valid("Passw0rd!")
        assert valid


# ── login brute-force lockout ─────────────────────────────────────────────────

class TestLoginBruteForce:
    def test_successful_login(self, client, test_user):
        resp = post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "Password1!",
        })
        assert resp.status_code == 200
        assert b"Welcome back" in resp.data or b"dashboard" in resp.data.lower()

    def test_wrong_password_increments_counter(self, app, client, test_user):
        post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "WrongPassword1!",
        })
        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.failed_login_attempts == 1

    def test_multiple_failures_increment_counter(self, app, client, test_user):
        for _ in range(3):
            post_with_csrf(client, "/login", {
                "username": "testuser",
                "password": "WrongPassword1!",
            })
        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.failed_login_attempts == 3

    def test_account_locked_after_max_failures(self, app, client, test_user):
        # Simulate 9 existing failures so the next one triggers lockout
        with app.app_context():
            user = db.session.get(User, test_user)
            user.failed_login_attempts = 9
            db.session.commit()

        # 10th failure — sets the lock but still shows generic error on this request
        post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "WrongPassword1!",
        })

        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.locked_until is not None
            assert user.failed_login_attempts == 10

        # 11th attempt — locked_until is now set so "locked" message shows
        resp = post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "WrongPassword1!",
        })
        assert b"locked" in resp.data.lower()

    def test_locked_account_rejects_correct_password(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            user.locked_until = utc_now() + timedelta(minutes=15)
            user.failed_login_attempts = 10
            db.session.commit()

        resp = post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "Password1!",
        })
        assert b"locked" in resp.data.lower()

    def test_successful_login_resets_counter(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            user.failed_login_attempts = 5
            db.session.commit()

        post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "Password1!",
        })

        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.failed_login_attempts == 0
            assert user.locked_until is None

    def test_expired_lockout_allows_login(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            # Lock expired 1 second ago
            user.locked_until = utc_now() - timedelta(seconds=1)
            user.failed_login_attempts = 10
            db.session.commit()

        resp = post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "Password1!",
        })
        # Should succeed — lock has expired
        assert b"locked" not in resp.data.lower()

    def test_unknown_username_shows_generic_error(self, client, test_user):
        resp = post_with_csrf(client, "/login", {
            "username": "nobody",
            "password": "Password1!",
        })
        assert b"Invalid username or password" in resp.data


# ── password reset token (DB-stored) ─────────────────────────────────────────

class TestPasswordResetToken:
    def test_make_token_stores_in_db(self, app, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            token = _make_reset_token(user)
            db.session.commit()

            assert user.reset_token == token
            assert user.reset_token_expires_at is not None

    def test_verify_token_returns_user(self, app, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            token = _make_reset_token(user)
            db.session.commit()

            result = _verify_reset_token(token)
            assert result is not None
            assert result.id == test_user

    def test_invalid_token_returns_none(self, app):
        with app.app_context():
            result = _verify_reset_token("this-is-not-a-valid-token")
            assert result is None

    def test_empty_token_returns_none(self, app):
        with app.app_context():
            assert _verify_reset_token("") is None
            assert _verify_reset_token(None) is None

    def test_expired_token_returns_none(self, app, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            token = _make_reset_token(user)
            # Backdate expiry to the past
            user.reset_token_expires_at = utc_now() - timedelta(seconds=1)
            db.session.commit()

            result = _verify_reset_token(token)
            assert result is None

    def test_token_cleared_after_reset(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            token = _make_reset_token(user)
            db.session.commit()

        resp = post_with_csrf(client, f"/reset-password/{token}", {
            "password": "NewPassword2@",
            "confirm_password": "NewPassword2@",
        })
        assert resp.status_code == 200

        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.reset_token is None
            assert user.reset_token_expires_at is None

    def test_token_cannot_be_reused(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            token = _make_reset_token(user)
            db.session.commit()

        # Use the token once
        post_with_csrf(client, f"/reset-password/{token}", {
            "password": "NewPassword2@",
            "confirm_password": "NewPassword2@",
        })

        # Try again with same token — should be rejected
        resp = post_with_csrf(client, f"/reset-password/{token}", {
            "password": "AnotherPass3#",
            "confirm_password": "AnotherPass3#",
        })
        assert b"invalid or has expired" in resp.data.lower()

    def test_reset_also_clears_lockout(self, app, client, test_user):
        with app.app_context():
            user = db.session.get(User, test_user)
            user.locked_until = utc_now() + timedelta(minutes=15)
            user.failed_login_attempts = 10
            token = _make_reset_token(user)
            db.session.commit()

        post_with_csrf(client, f"/reset-password/{token}", {
            "password": "NewPassword2@",
            "confirm_password": "NewPassword2@",
        })

        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.locked_until is None
            assert user.failed_login_attempts == 0

    def test_oversized_token_returns_none(self, app):
        with app.app_context():
            huge_token = "a" * 200
            assert _verify_reset_token(huge_token) is None


# ── password reset cooldown ───────────────────────────────────────────────────

class TestPasswordResetCooldown:
    def test_second_request_within_cooldown_is_silently_skipped(
        self, app, client, test_user
    ):
        """
        If a reset was requested recently, a second attempt must not generate
        a new token (the old token should remain unchanged).
        """
        with patch("auth._send_password_reset_email", return_value=True):
            post_with_csrf(client, "/forgot-password", {"email": "test@example.com"})

        with app.app_context():
            first_token = db.session.get(User, test_user).reset_token

        with patch("auth._send_password_reset_email", return_value=True):
            post_with_csrf(client, "/forgot-password", {"email": "test@example.com"})

        with app.app_context():
            user = db.session.get(User, test_user)
            # Token must not have changed — cooldown was active
            assert user.reset_token == first_token

    def test_request_after_cooldown_generates_new_token(
        self, app, client, test_user
    ):
        with app.app_context():
            user = db.session.get(User, test_user)
            # Simulate a prior request older than the cooldown window
            user.last_password_reset_request_at = utc_now() - timedelta(minutes=6)
            db.session.commit()

        with patch("auth._send_password_reset_email", return_value=True):
            post_with_csrf(client, "/forgot-password", {"email": "test@example.com"})

        with app.app_context():
            user = db.session.get(User, test_user)
            assert user.reset_token is not None

    def test_forgot_password_always_shows_same_message(self, client, test_user):
        """User enumeration prevention: same flash regardless of whether email exists."""
        with patch("auth._send_password_reset_email", return_value=True):
            resp_exists = post_with_csrf(
                client, "/forgot-password", {"email": "test@example.com"}
            )
            resp_missing = post_with_csrf(
                client, "/forgot-password", {"email": "nobody@example.com"}
            )
        # Both should contain the same generic message
        assert b"if an account" in resp_exists.data.lower()
        assert b"if an account" in resp_missing.data.lower()


# ── CSRF protection ───────────────────────────────────────────────────────────

class TestCSRFProtection:
    def test_post_without_csrf_token_is_rejected(self, client, test_user):
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "Password1!"},
        )
        assert resp.status_code == 400
        assert b"CSRF" in resp.data

    def test_post_with_wrong_csrf_token_is_rejected(self, client, test_user):
        # Initialise session so a real token exists, then send the wrong one
        csrf_token(client)
        resp = client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "Password1!",
                "_csrf_token": "totally-wrong-token",
            },
        )
        assert resp.status_code == 400

    def test_post_with_correct_csrf_token_is_accepted(self, client, test_user):
        resp = post_with_csrf(client, "/login", {
            "username": "testuser",
            "password": "Password1!",
        })
        # 200 means the request got through (redirect followed)
        assert resp.status_code == 200
        assert b"Invalid CSRF" not in resp.data


# ── register ──────────────────────────────────────────────────────────────────

class TestRegister:
    def test_register_with_weak_password_fails(self, client):
        resp = post_with_csrf(client, "/register", {
            "username": "newuser",
            "email": "new@example.com",
            "password": "password",        # no digit/special
            "confirm_password": "password",
        })
        assert b"number or special" in resp.data

    def test_register_with_space_only_special_fails(self, client):
        resp = post_with_csrf(client, "/register", {
            "username": "newuser",
            "email": "new@example.com",
            "password": "hello world",    # space is not a valid special char
            "confirm_password": "hello world",
        })
        assert b"number or special" in resp.data

    def test_register_success(self, client):
        resp = post_with_csrf(client, "/register", {
            "username": "newuser",
            "email": "new@example.com",
            "password": "Password1!",
            "confirm_password": "Password1!",
        })
        assert resp.status_code == 200
        assert b"Invalid" not in resp.data

    def test_duplicate_username_rejected(self, client, test_user):
        resp = post_with_csrf(client, "/register", {
            "username": "testuser",       # already exists
            "email": "other@example.com",
            "password": "Password1!",
            "confirm_password": "Password1!",
        })
        assert b"already exists" in resp.data

    def test_passwords_must_match(self, client):
        resp = post_with_csrf(client, "/register", {
            "username": "newuser",
            "email": "new@example.com",
            "password": "Password1!",
            "confirm_password": "DifferentPass2@",
        })
        assert b"do not match" in resp.data
