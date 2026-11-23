"""Non-destructive upgrades from the initial cafe schema on PostgreSQL."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def upgrade_endpoint_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    menu_columns = {column["name"]: column for column in inspector.get_columns("menu_items")}
    order_columns = {column["name"]: column for column in inspector.get_columns("orders")}
    additions = {
        "menu_items": {"deleted_at": "TIMESTAMP WITH TIME ZONE"},
        "orders": {
            "cancelled_at": "TIMESTAMP WITH TIME ZONE",
            "paid_at": "TIMESTAMP WITH TIME ZONE",
            "payment_method": "VARCHAR(20)",
        },
    }
    pending_checks = [
        constraint for constraint in inspector.get_check_constraints("orders")
        if constraint["name"] == "order_status" and "PENDING" not in constraint["sqltext"]
    ]
    needs_upgrade = (
        "deleted_at" not in menu_columns
        or any(name not in order_columns for name in additions["orders"])
        or not order_columns["queue_number"]["nullable"]
        or not order_columns["queue_date"]["nullable"]
        or bool(pending_checks)
        or "PENDING" not in str(order_columns["status"]["default"])
    )
    if not needs_upgrade:
        return
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Upgrading an existing endpoint schema requires PostgreSQL")

    for table, columns in additions.items():
        existing = menu_columns if table == "menu_items" else order_columns
        for name, sql_type in columns.items():
            if name not in existing:
                # All identifiers and SQL types are fixed application constants.
                connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}'))
    for name in ("queue_number", "queue_date"):
        if not order_columns[name]["nullable"]:
            connection.execute(text(f'ALTER TABLE orders ALTER COLUMN "{name}" DROP NOT NULL'))
    if pending_checks:
        connection.execute(text("ALTER TABLE orders DROP CONSTRAINT order_status"))
        connection.execute(text(
            "ALTER TABLE orders ADD CONSTRAINT order_status "
            "CHECK (status IN ('PENDING', 'QUEUED', 'COMPLETED', 'CANCELLED'))"
        ))
    if "PENDING" not in str(order_columns["status"]["default"]):
        connection.execute(text("ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'PENDING'"))
