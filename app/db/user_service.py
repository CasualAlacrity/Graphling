from sqlalchemy import select

from db.models import User
from db.session import SessionLocal


async def get_or_create_user(discord_id: str) -> User:
    """Resolves the local users.id a Discord pilot maps to, creating the row on their
    first-ever login. Called once at startup (voice/__init__.py) — see db/current_user.py
    for why this is a per-process lookup rather than something re-resolved per call."""
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.discord_id == discord_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        user = User(discord_id=discord_id)
        session.add(user)
        await session.commit()
        return user
