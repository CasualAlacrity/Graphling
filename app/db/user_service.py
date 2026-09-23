from sqlalchemy import select

from db.models import User
from db.session import SessionLocal


async def get_or_create_user(discord_id: str) -> User:
    """Resolves the local users.id a Discord pilot maps to, creating the row on their
    first-ever login. Called from server/routes/auth.py's Discord callback, once per
    login — everything after that resolves the pilot from their JWT instead
    (server/dependencies.get_current_user), not by calling this again."""
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.discord_id == discord_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        user = User(discord_id=discord_id)
        session.add(user)
        await session.commit()
        return user
