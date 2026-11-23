"""Consumer-visible authorization, lifecycle, and concurrency regression tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from conftest import TEST_PASSWORD, auth_headers


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def create_order(api, headers, menu_id=1):
    return checked(api.post('/orders', headers=headers, json={
        'items': [{'menu_item_id': menu_id, 'quantity': 2}],
    }), 201)


def test_authentication_rotation_logout_and_user_administration(api, accounts, database):
    from app.databases.models import RefreshToken, User
    assert api.get('/auth/me').status_code == 401
    alice, bob, admin = (accounts[name] for name in ('alice', 'bob', 'admin'))
    profile = checked(api.get('/auth/me', headers=alice['headers']))
    assert profile['role'] == 'CUSTOMER' and 'password_hash' not in profile
    assert api.post('/auth/register', json={
        'user_name': 'public_admin', 'email': 'bad@example.com', 'password': TEST_PASSWORD, 'role': 'ADMIN',
    }).status_code == 400
    assert api.post('/auth/register', json={
        'user_name': 'alice', 'email': 'other@example.com', 'password': TEST_PASSWORD,
    }).status_code == 409
    assert api.post('/auth/login', json={'identifier': 'alice', 'password': 'wrong-password'}).status_code == 401
    assert api.post('/auth/login', json={'identifier': 'unknown', 'password': 'wrong-password'}).status_code == 401
    assert api.get('/users', headers=alice['headers']).status_code == 403
    cashier = checked(api.post('/users', headers=admin['headers'], json={
        'user_name': 'new_cashier', 'email': 'cashier2@example.com', 'password': TEST_PASSWORD, 'role': 'CASHIER',
    }), 201)
    assert cashier['role'] == 'CASHIER'
    new_admin = checked(api.post('/users', headers=admin['headers'], json={
        'user_name': 'new_admin', 'email': 'admin2@example.com', 'password': TEST_PASSWORD, 'role': 'ADMIN',
    }), 201)
    assert new_admin['role'] == 'ADMIN'
    assert api.post('/users', headers=admin['headers'], json={
        'user_name': 'not_staff', 'email': 'other@example.com', 'password': TEST_PASSWORD, 'role': 'CUSTOMER',
    }).status_code == 400
    users = checked(api.get('/users?page=2&limit=2', headers=admin['headers']))
    assert users['total'] == 6 and len(users['items']) == 2 and users['pages'] == 3
    assert all('password_hash' not in user for user in users['items'])
    assert api.post('/auth/logout', headers=bob['headers'], json={
        'refresh_token': alice['refresh_token'],
    }).status_code == 401
    rotated = checked(api.post('/auth/refresh', json={'refresh_token': alice['refresh_token']}))
    assert rotated['refresh_token'] != alice['refresh_token']
    assert api.post('/auth/refresh', json={'refresh_token': alice['refresh_token']}).status_code == 401
    assert checked(api.get('/auth/me', headers=auth_headers(rotated['access_token'])))['id'] == profile['id']
    checked(api.post('/auth/logout', headers=auth_headers(rotated['access_token']), json={
        'refresh_token': rotated['refresh_token'],
    }), 204)
    assert api.post('/auth/refresh', json={'refresh_token': rotated['refresh_token']}).status_code == 401
    with Session(database) as session:
        saved_user = session.scalar(select(User).where(User.user_name == 'alice'))
        assert saved_user.password_hash.startswith('$argon2')
        stored = list(session.scalars(select(RefreshToken)))
        assert all(token.token_hash != rotated['refresh_token'] and len(token.token_hash) == 64 for token in stored)
    error = api.get('/users', headers=alice['headers']).json()
    assert error['status_code'] == 403


def test_catalog_permissions_filtering_and_soft_deletion(api, accounts):
    admin, cashier, alice = (accounts[name]['headers'] for name in ('admin', 'cashier', 'alice'))
    assert len(checked(api.get('/categories'))) == 2
    assert api.post('/categories', headers=cashier, json={'name': 'Denied'}).status_code == 403
    category = checked(api.post('/categories', headers=admin, json={'name': 'Snacks'}), 201)
    cid = category['id']
    assert api.post('/categories', headers=admin, json={'name': 'Snacks'}).status_code == 409
    assert checked(api.patch(f'/categories/{cid}', headers=admin, json={'name': 'Desserts'}))['name'] == 'Desserts'
    menu = checked(api.post('/menu-items', headers=admin, json={
        'name': 'Black_100% Coffee', 'category_id': cid, 'is_available': True,
    }), 201)
    mid = menu['id']
    assert checked(api.get(f'/menu-items/{mid}'))['name'] == 'Black_100% Coffee'
    for q in ('_', '%', 'black_100%'):
        items = checked(api.get('/menu-items', params={'q': q, 'category_id': cid, 'limit': 1}))
        assert items['total'] == 1 and items['items'][0]['id'] == mid
    assert api.patch(f'/menu-items/{mid}/availability', headers=alice, json={'is_available': False}).status_code == 403
    assert checked(api.patch(f'/menu-items/{mid}/availability', headers=cashier, json={'is_available': False}))['is_available'] is False
    assert checked(api.patch(f'/menu-items/{mid}', headers=admin, json={'name': 'Updated Coffee'}))['name'] == 'Updated Coffee'
    assert api.patch(f'/menu-items/{mid}', headers=admin, json={'name': None}).status_code == 400
    assert api.patch(f'/menu-items/{mid}', headers=admin, json={}).status_code == 400
    assert api.patch(f'/menu-items/{mid}/availability', headers=cashier, json={'is_available': 'false'}).status_code == 400
    assert api.delete(f'/categories/{cid}', headers=admin).status_code == 409
    checked(api.delete(f'/menu-items/{mid}', headers=admin), 204)
    assert api.get(f'/menu-items/{mid}').status_code == 404
    assert checked(api.get('/menu-items', params={'category_id': cid}))['total'] == 0
    assert api.delete(f'/categories/{cid}', headers=admin).status_code == 409
    unused = checked(api.post('/categories', headers=admin, json={'name': 'Unused'}), 201)
    checked(api.delete(f"/categories/{unused['id']}", headers=admin), 204)
    assert api.patch(f"/categories/{unused['id']}", headers=admin, json={'name': 'Gone'}).status_code == 404
    assert api.get('/menu-items?limit=0').status_code == 400


def test_order_lifecycle_ownership_and_payment(api, accounts):
    alice, bob, cashier, admin = (accounts[name]['headers'] for name in ('alice', 'bob', 'cashier', 'admin'))
    assert checked(api.get('/health')) == {'status': 'ok', 'database': 'ok'}
    assert api.post('/orders', headers=cashier, json={'items': [{'menu_item_id': 1, 'quantity': 1}]}).status_code == 403
    order = create_order(api, alice)
    oid = order['id']
    assert order['status'] == 'PENDING' and order['queue_number'] is None and order['queue_date'] is None
    assert order['items'][0]['quantity'] == 2
    assert api.get(f'/orders/{oid}', headers=bob).status_code == 403
    assert api.post(f'/orders/{oid}/confirm', headers=bob).status_code == 403
    assert api.patch(f'/orders/{oid}/cancel', headers=bob).status_code == 403
    assert api.patch(f'/orders/{oid}/complete', headers=cashier).status_code == 409
    checked(api.patch('/menu-items/1/availability', headers=cashier, json={'is_available': False}))
    assert api.post(f'/orders/{oid}/confirm', headers=alice).status_code == 409
    assert api.post('/orders', headers=alice, json={'items': [{'menu_item_id': 1, 'quantity': 1}]}).status_code == 409
    checked(api.patch('/menu-items/1/availability', headers=cashier, json={'is_available': True}))
    queued = checked(api.post(f'/orders/{oid}/confirm', headers=alice))
    assert queued['status'] == 'QUEUED' and queued['queue_number'] == 1
    assert api.post(f'/orders/{oid}/confirm', headers=alice).status_code == 409
    assert api.patch(f'/orders/{oid}/complete', headers=admin).status_code == 403
    completed = checked(api.patch(f'/orders/{oid}/complete', headers=cashier, json={'payment_method': 'QRIS'}))
    assert completed['status'] == 'COMPLETED' and completed['paid_at'] == completed['completed_at']
    assert completed['payment_method'] == 'QRIS' and completed['cashier_id'] == accounts['cashier']['user']['id']
    assert api.patch(f'/orders/{oid}/complete', headers=cashier).status_code == 409
    assert api.patch(f'/orders/{oid}/cancel', headers=alice).status_code == 409
    pending = create_order(api, bob)
    cancelled = checked(api.patch(f"/orders/{pending['id']}/cancel", headers=bob))
    assert cancelled['status'] == 'CANCELLED' and cancelled['cancelled_at'] is not None
    other = create_order(api, bob)
    checked(api.post(f"/orders/{other['id']}/confirm", headers=bob))
    assert checked(api.patch(f"/orders/{other['id']}/cancel", headers=cashier))['status'] == 'CANCELLED'
    assert checked(api.get('/orders', headers=alice))['total'] == 1
    assert checked(api.get('/orders', headers=cashier))['total'] == 3
    filtered = checked(api.get('/orders', headers=admin, params={
        'status': 'COMPLETED', 'date': datetime.now(timezone.utc).date().isoformat(), 'page': 1, 'limit': 1,
    }))
    assert filtered['total'] == 1 and filtered['items'][0]['id'] == oid
    checked(api.delete('/menu-items/1', headers=admin), 204)
    assert checked(api.get(f'/orders/{oid}', headers=alice))['items'][0]['name'] == 'Nasi Goreng'
    assert api.post('/orders', headers=alice, json={'items': [{'menu_item_id': 1, 'quantity': 1}]}).status_code == 409
    assert api.post('/orders', headers=alice, json={'items': []}).status_code == 400


def test_atomic_daily_queue_with_legacy_numbers(api, accounts, database):
    from app.databases.models import Order, OrderItem, OrderStatus
    alice, admin = (accounts[name]['headers'] for name in ('alice', 'admin'))
    with Session(database) as session:
        legacy = Order(customer_id=accounts['alice']['user']['id'], status=OrderStatus.QUEUED,
                       queue_date=datetime.now(timezone.utc).date(), queue_number=40,
                       items=[OrderItem(menu_item_id=1, quantity=1)])
        session.add(legacy)
        session.commit()
    pending = []
    for number in range(8):
        menu = checked(api.post('/menu-items', headers=admin, json={
            'name': f'Concurrent menu {number}', 'category_id': 1,
        }), 201)
        pending.append(create_order(api, alice, menu['id'])['id'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda oid: api.post(f'/orders/{oid}/confirm', headers=alice), pending))
    queues = [checked(response)['queue_number'] for response in responses]
    assert sorted(queues) == list(range(41, 49))
    with ThreadPoolExecutor(max_workers=2) as pool:
        repeated = list(pool.map(lambda _: api.post(f'/orders/{pending[0]}/confirm', headers=alice), range(2)))
    assert [response.status_code for response in repeated] == [409, 409]


def test_concurrent_refresh_is_single_use(api, accounts):
    token = accounts['alice']['refresh_token']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.post('/auth/refresh', json={'refresh_token': token}), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert api.get('/auth/me', headers=auth_headers(winner['access_token'])).status_code == 200


def test_websocket_scopes_order_events_and_broadcasts_availability(api, accounts):
    alice, bob, cashier = (accounts[name] for name in ('alice', 'bob', 'cashier'))
    with api.websocket_connect(f"/ws?token={alice['access_token']}") as owner, \
         api.websocket_connect(f"/ws?token={bob['access_token']}") as other, \
         api.websocket_connect(f"/ws?token={cashier['access_token']}") as staff:
        for socket in (owner, other, staff):
            assert socket.receive_json()['event'] == 'connected'
        order = create_order(api, alice['headers'])
        for socket in (owner, staff):
            event = socket.receive_json()
            assert event['event'] == 'order.created' and event['data']['id'] == order['id']
        other.send_text('ping')
        assert other.receive_json()['event'] == 'pong'
        checked(api.patch('/menu-items/1/availability', headers=cashier['headers'], json={'is_available': False}))
        for socket in (owner, other, staff):
            event = socket.receive_json()
            assert event['event'] == 'menu.availability_changed' and event['data']['is_available'] is False
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect('/ws?token=invalid'):
            pass
