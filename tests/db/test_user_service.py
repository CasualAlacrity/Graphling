import uuid

from db import user_service
from db.models import User


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, existing_user=None):
        self._existing_user = existing_user
        self.added = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._existing_user)

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed = True


async def test_returns_the_existing_user_without_creating_one(monkeypatch):
    existing = User(id=uuid.uuid4(), discord_id="123456789012345678")
    session = _FakeSession(existing_user=existing)
    monkeypatch.setattr(user_service, "SessionLocal", lambda: session)

    result = await user_service.get_or_create_user("123456789012345678")

    assert result is existing
    assert session.added is None
    assert session.committed is False


async def test_creates_a_new_user_on_first_login(monkeypatch):
    session = _FakeSession(existing_user=None)
    monkeypatch.setattr(user_service, "SessionLocal", lambda: session)

    result = await user_service.get_or_create_user("987654321098765432")

    assert result.discord_id == "987654321098765432"
    assert session.added is result
    assert session.committed is True
