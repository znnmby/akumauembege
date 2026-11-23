"""WebSocket authentication, expiry, and role-scoped event subscriptions."""

import asyncio
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.security import decode_access_token
from app.databases.models import User
from app.databases.session import SessionLocal
from app.modules.realtime.events import Subscriber, broker

router = APIRouter(tags=["Realtime"])


def _authenticate(token: str) -> tuple[int, str, float]:
    claims = decode_access_token(token)
    with SessionLocal() as session:
        user = session.get(User, int(claims["sub"]))
        if user is None:
            raise jwt.InvalidTokenError("Unknown user")
        return user.id, user.role, float(claims["exp"])


@router.websocket("/ws")
async def websocket_events(socket: WebSocket) -> None:
    token = socket.query_params.get("token")
    if token is None:
        scheme, _, credential = socket.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            token = credential
    if not token:
        await socket.close(code=4401)
        return
    try:
        user_id, role, expires_at = await run_in_threadpool(_authenticate, token)
    except jwt.InvalidTokenError:
        await socket.close(code=4401)
        return

    await socket.accept()
    subscriber = Subscriber(socket, user_id, role, expires_at)
    broker.subscribers.add(subscriber)
    try:
        await subscriber.send({"event": "connected", "data": {"user_id": user_id, "role": role}})
        while True:
            remaining = expires_at - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                await socket.close(code=4401)
                break
            try:
                message = await asyncio.wait_for(socket.receive_text(), timeout=remaining)
            except TimeoutError:
                await socket.close(code=4401)
                break
            if message == "ping":
                await subscriber.send({"event": "pong", "data": {}})
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        broker.subscribers.discard(subscriber)
