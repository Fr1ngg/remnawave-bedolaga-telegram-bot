from __future__ import annotations

import argparse
import asyncio
import os
from typing import Optional

from sqlalchemy import select, delete

from app.database.database import AsyncSessionLocal
from app.database.models import AdminUser
from app.webapi.routes.auth import _hash_password


async def reset_admin(username: str, password: str, mode: str = "update", force: bool = False) -> None:
    """
    Безопасный сброс/создание администратора.

    Режимы:
      - update: если админ существует — обновляем логин/пароль первого администратора, иначе создаём нового
      - recreate: удаляем всех админов и создаём одного нового (требует --force)
    """
    async with AsyncSessionLocal() as session:
        if mode == "recreate":
            if not force:
                raise RuntimeError("Recreate requires --force to avoid accidental data loss")
            await session.execute(delete(AdminUser))
            await session.commit()

        result = await session.execute(select(AdminUser).order_by(AdminUser.id.asc()))
        admin: Optional[AdminUser] = result.scalars().first()

        if admin:
            admin.username = username.strip().lower()
            admin.password_hash = _hash_password(password)
        else:
            admin = AdminUser(username=username.strip().lower(), password_hash=_hash_password(password))
            session.add(admin)

        await session.commit()


def main():
    parser = argparse.ArgumentParser(description="Reset or create the single admin user")
    parser.add_argument("--username", required=False, default=os.getenv("ADMIN_RESET_USERNAME", "admin"))
    parser.add_argument("--password", required=False, default=os.getenv("ADMIN_RESET_PASSWORD", "admin123"))
    parser.add_argument("--mode", choices=["update", "recreate"], default="update")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--token", required=False, default=os.getenv("ADMIN_RESET_TOKEN"))
    args = parser.parse_args()

    expected = os.getenv("ADMIN_RESET_TOKEN")
    if not expected:
        print("ERROR: ADMIN_RESET_TOKEN env is not set on the server.")
        raise SystemExit(2)
    if args.token != expected:
        print("ERROR: Provided token does not match ADMIN_RESET_TOKEN.")
        raise SystemExit(3)

    asyncio.run(reset_admin(args.username, args.password, mode=args.mode, force=args.force))
    print("Admin reset completed.")


if __name__ == "__main__":
    main()


