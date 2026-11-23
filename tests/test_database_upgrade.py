"""Preserve existing cafe data while enabling the endpoint lifecycle schema."""

import json

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from conftest import TEST_PASSWORD


def test_legacy_database_upgrade_preserves_users_and_order_history(database, monkeypatch):
    from app.databases import script
    from app.databases.models import Base, Order, OrderStatus, Role, User
    Base.metadata.create_all(database)
    with database.begin() as connection:
        connection.execute(text("INSERT INTO roles(name) VALUES ('CUSTOMER')"))
        connection.execute(text("INSERT INTO users(user_name,email,password_hash,role) VALUES ('legacy','legacy@example.com','preserved-hash','CUSTOMER')"))
        connection.execute(text("INSERT INTO categories(name) VALUES ('Legacy category')"))
        connection.execute(text("INSERT INTO menu_items(category_id,name) VALUES (1,'Legacy menu')"))
        connection.execute(text("INSERT INTO orders(customer_id,status,queue_number,queue_date) VALUES (1,'QUEUED',99,CURRENT_DATE)"))
        connection.execute(text("INSERT INTO order_items(order_id,menu_item_id,quantity) VALUES (1,1,3)"))
        fk = next(item['name'] for item in inspect(connection).get_foreign_keys('users')
                  if item['constrained_columns'] == ['role'])
        connection.execute(text(f'ALTER TABLE users DROP CONSTRAINT "{fk}"'))
        connection.execute(text("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(8)"))
        connection.execute(text("ALTER TABLE users ADD CONSTRAINT user_role CHECK (role IN ('CUSTOMER','CASHIER','ADMIN'))"))
        connection.execute(text('DROP TABLE roles'))
        connection.execute(text('ALTER TABLE menu_items DROP COLUMN deleted_at'))
        for name in ('cancelled_at', 'paid_at', 'payment_method'):
            connection.execute(text(f'ALTER TABLE orders DROP COLUMN {name}'))
        connection.execute(text('ALTER TABLE orders ALTER COLUMN queue_number SET NOT NULL'))
        connection.execute(text('ALTER TABLE orders ALTER COLUMN queue_date SET NOT NULL'))
        connection.execute(text('ALTER TABLE orders DROP CONSTRAINT order_status'))
        connection.execute(text("ALTER TABLE orders ADD CONSTRAINT order_status CHECK (status IN ('QUEUED','COMPLETED','CANCELLED'))"))
        connection.execute(text("ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'QUEUED'"))
    monkeypatch.setattr(script, 'engine', database)
    script.initialize_database()
    assert script.initialize_database() == (0, 0, 0, 0)
    with Session(database) as session:
        user = session.scalar(select(User).where(User.user_name == 'legacy'))
        assert user.password_hash == 'preserved-hash' and user.role_definition.name == 'CUSTOMER'
        order = session.get(Order, 1)
        assert order.queue_number == 99 and order.items[0].quantity == 3
        pending = Order(customer_id=user.id)
        session.add(pending)
        session.flush()
        assert pending.status == OrderStatus.PENDING and pending.queue_number is None
        role = Role(name='SHIFT_SUPERVISOR')
        session.add(role)
        session.flush()
        session.add(User(user_name='supervisor',email='supervisor@example.com',password_hash='test-only',role_definition=role))
        session.commit()


def test_new_json_roles_and_unavailable_menus_need_no_python_enum(database, tmp_path):
    from app.core.security import verify_password
    from app.databases.models import Base, MenuItem, Role, User
    from app.databases.script import seed_initial_data
    Base.metadata.create_all(database)
    payloads = {
        'role': [{'name': 'SHIFT_SUPERVISOR'}],
        'category': [{'name': 'Specials'}],
        'menu': [{'name': 'Seasonal drink', 'category_name': 'Specials', 'is_available': False}],
        'user': [{'user_name': 'shift', 'email': 'shift@example.com', 'role': 'SHIFT_SUPERVISOR'}],
    }
    for name, payload in payloads.items():
        (tmp_path / f'{name}.json').write_text(json.dumps(payload))
    with Session(database) as session:
        assert seed_initial_data(session, TEST_PASSWORD, tmp_path) == (1, 1, 1, 1)
        session.commit()
        user = session.scalar(select(User))
        assert user.role_definition.name == 'SHIFT_SUPERVISOR'
        assert session.scalar(select(MenuItem)).is_available is False
        assert verify_password(TEST_PASSWORD, user.password_hash)
        original_hash = user.password_hash
        assert seed_initial_data(session, None, tmp_path) == (0, 0, 0, 0)
        session.commit()
        assert session.get(User, user.id).password_hash == original_hash
        assert session.get(Role, 'SHIFT_SUPERVISOR') is not None
