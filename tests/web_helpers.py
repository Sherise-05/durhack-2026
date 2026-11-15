"""Existing page tests run through real account authentication and data scoping."""

from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid4

from fastapi.testclient import TestClient

from app.schemas import UserProfile
from app.services import accounts, db

TEST_PASSWORD = "A fictional fixture password 2026!"


@lru_cache(maxsize=1)
def _password_hash():
    return accounts.hash_password(TEST_PASSWORD)


@contextmanager
def authenticated_client(application):
    email = "fixture-" + uuid4().hex + "@example.com"
    account = db.create_account(email, _password_hash(), "You")
    db.complete_onboarding(account["id"], UserProfile(name="You"))
    with db.account_scope(account["id"]):
        with TestClient(application) as client:
            response = client.post("/login", data={"email": email, "password": TEST_PASSWORD}, follow_redirects=False)
            assert response.status_code == 303 and response.headers["location"] == "/dashboard"
            client.account_id = account["id"]
            yield client
