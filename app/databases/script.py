"""Create the schema and seed initial data from JSON files.

Run with: ``python -m app.databases.script``.
"""

import json
from pathlib import Path
from typing import TypedDict

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.databases.models import Base, Category, MenuItem, Role, User
from app.databases.session import engine

DATA_DIR = Path(__file__).with_name("data")


class CategorySeed(TypedDict):
    name: str


class MenuSeed(TypedDict):
    name: str
    category_name: str
    is_available: bool


class RoleSeed(TypedDict):
    name: str


class UserSeed(TypedDict):
    user_name: str
    email: str
    role: str


def _load_records(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Seed file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in seed file {path}: {exc.msg}") from exc

    if not isinstance(payload, list):
        raise ValueError(f"Seed file must contain a JSON array: {path}")

    records: list[dict[str, object]] = []
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise ValueError(f"{path}[{index}] must be a JSON object")
        records.append(record)
    return records


def _required_string(
    record: dict[str, object],
    field: str,
    path: Path,
    index: int,
) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}[{index}].{field} must be a non-empty string")
    return value.strip()


def load_seed_data(
    data_dir: Path = DATA_DIR,
) -> tuple[
    list[CategorySeed],
    list[MenuSeed],
    list[RoleSeed],
    list[UserSeed],
]:
    category_path = data_dir / "category.json"
    menu_path = data_dir / "menu.json"
    role_path = data_dir / "role.json"
    user_path = data_dir / "user.json"

    categories = [
        CategorySeed(name=_required_string(record, "name", category_path, index))
        for index, record in enumerate(_load_records(category_path))
    ]
    category_names = [category["name"] for category in categories]
    category_name_set = set(category_names)
    if len(category_names) != len(category_name_set):
        raise ValueError(f"Duplicate category name in seed file: {category_path}")

    menus: list[MenuSeed] = []
    menu_keys: set[tuple[str, str]] = set()
    for index, record in enumerate(_load_records(menu_path)):
        name = _required_string(record, "name", menu_path, index)
        category_name = _required_string(
            record,
            "category_name",
            menu_path,
            index,
        )
        if category_name not in category_name_set:
            raise ValueError(
                f"{menu_path}[{index}].category_name references an unknown category"
            )

        is_available = record.get("is_available")
        if not isinstance(is_available, bool):
            raise ValueError(
                f"{menu_path}[{index}].is_available must be a boolean"
            )

        key = (category_name, name)
        if key in menu_keys:
            raise ValueError(f"Duplicate menu item in seed file: {key}")
        menu_keys.add(key)
        menus.append(
            MenuSeed(
                name=name,
                category_name=category_name,
                is_available=is_available,
            )
        )

    roles: list[RoleSeed] = []
    role_names: set[str] = set()
    for index, record in enumerate(_load_records(role_path)):
        name = _required_string(record, "name", role_path, index)
        if len(name) > 100:
            raise ValueError(f"{role_path}[{index}].name exceeds 100 characters")
        if name in role_names:
            raise ValueError(f"Duplicate role in seed file: {name}")
        role_names.add(name)
        roles.append(RoleSeed(name=name))

    users: list[UserSeed] = []
    user_names: set[str] = set()
    emails: set[str] = set()
    for index, record in enumerate(_load_records(user_path)):
        user_name = _required_string(record, "user_name", user_path, index)
        email = _required_string(record, "email", user_path, index)
        role = _required_string(record, "role", user_path, index)
        if role not in role_names:
            raise ValueError(
                f"{user_path}[{index}].role is not defined in {role_path}"
            )
        if user_name in user_names:
            raise ValueError(f"Duplicate user_name in seed file: {user_name}")
        if email in emails:
            raise ValueError(f"Duplicate email in seed file: {email}")
        user_names.add(user_name)
        emails.add(email)
        users.append(UserSeed(user_name=user_name, email=email, role=role))

    return categories, menus, roles, users


def seed_initial_data(
    session: Session,
    admin_password: str | None,
    data_dir: Path = DATA_DIR,
) -> tuple[int, int, int, int]:
    """Insert missing rows; never overwrite existing users or their passwords."""
    category_rows, menu_rows, role_rows, user_rows = load_seed_data(data_dir)
    role_names = [row["name"] for row in role_rows]
    existing_role_names = set(
        session.scalars(select(Role.name).where(Role.name.in_(role_names)))
    )
    new_roles = [
        Role(name=row["name"])
        for row in role_rows
        if row["name"] not in existing_role_names
    ]
    session.add_all(new_roles)
    session.flush()

    category_names = [row["name"] for row in category_rows]

    categories_by_name = {
        category.name: category
        for category in session.scalars(
            select(Category).where(Category.name.in_(category_names))
        )
    }

    added_categories = 0
    for row in category_rows:
        if row["name"] not in categories_by_name:
            category = Category(name=row["name"])
            session.add(category)
            categories_by_name[row["name"]] = category
            added_categories += 1
    session.flush()

    existing_menu_keys = {
        (category_name, menu_name)
        for category_name, menu_name in session.execute(
            select(Category.name, MenuItem.name)
            .join(MenuItem, MenuItem.category_id == Category.id)
            .where(Category.name.in_(category_names))
        )
    }
    new_items = [
        MenuItem(
            category=categories_by_name[row["category_name"]],
            name=row["name"],
            is_available=row["is_available"],
        )
        for row in menu_rows
        if (row["category_name"], row["name"]) not in existing_menu_keys
    ]
    session.add_all(new_items)

    user_names = [row["user_name"] for row in user_rows]
    user_emails = [row["email"] for row in user_rows]
    existing_users = list(
        session.scalars(
            select(User).where(
                or_(User.user_name.in_(user_names), User.email.in_(user_emails))
            )
        )
    )
    users_by_name = {user.user_name: user for user in existing_users}
    users_by_email = {user.email: user for user in existing_users}

    new_users: list[User] = []
    for row in user_rows:
        existing_by_name = users_by_name.get(row["user_name"])
        existing_by_email = users_by_email.get(row["email"])
        if existing_by_name is not None or existing_by_email is not None:
            if existing_by_name is not existing_by_email:
                raise ValueError(
                    "Seed user conflicts with an existing user_name or email: "
                    f"{row['user_name']}"
                )
            if existing_by_name.role != row["role"]:
                raise ValueError(
                    f"Seed role conflicts with existing user: {row['user_name']}"
                )
            continue

        if admin_password is None:
            raise ValueError(
                "INITIAL_ADMIN_PASSWORD must be set before seeding users"
            )
        user = User(
            user_name=row["user_name"],
            email=row["email"],
            password_hash=hash_password(admin_password),
            role=row["role"],
        )
        session.add(user)
        users_by_name[user.user_name] = user
        users_by_email[user.email] = user
        new_users.append(user)

    return len(new_roles), added_categories, len(new_items), len(new_users)


def _upgrade_legacy_roles(connection: Connection) -> None:
    """Replace the old PostgreSQL role CHECK without deleting existing users."""
    inspector = inspect(connection)
    for foreign_key in inspector.get_foreign_keys("users"):
        if (
            foreign_key["constrained_columns"] == ["role"]
            and foreign_key["referred_table"] == "roles"
            and foreign_key["referred_columns"] == ["name"]
        ):
            return

    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "Automatic upgrade of the legacy users.role schema requires PostgreSQL"
        )

    # Preserve roles already referenced by users, including ones absent from JSON.
    connection.execute(text(
        "INSERT INTO roles (name) SELECT DISTINCT role FROM users "
        "ON CONFLICT (name) DO NOTHING"
    ))
    connection.execute(text(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS user_role"
    ))
    connection.execute(text(
        "ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(100)"
    ))
    connection.execute(text(
        "ALTER TABLE users ADD CONSTRAINT fk_users_role_roles "
        "FOREIGN KEY (role) REFERENCES roles (name)"
    ))


def initialize_database() -> tuple[int, int, int, int]:
    """Create or upgrade the schema and seed data in one database transaction."""
    settings = get_settings()
    admin_password = (
        settings.initial_admin_password.get_secret_value()
        if settings.initial_admin_password is not None
        else None
    )
    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        _upgrade_legacy_roles(connection)
        with Session(bind=connection) as session:
            counts = seed_initial_data(session, admin_password)
            session.flush()
        return counts


def main() -> None:
    added_roles, added_categories, added_menu_items, added_users = initialize_database()
    user_label = "user" if added_users == 1 else "users"
    print(
        "Database initialized: "
        f"{added_roles} roles, "
        f"{added_categories} categories, "
        f"{added_menu_items} menu items, and "
        f"{added_users} {user_label} added."
    )


if __name__ == "__main__":
    main()
