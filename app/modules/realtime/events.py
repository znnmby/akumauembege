"""Best-effort notifications for the single-process development API."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


@dataclass(eq=False)
class Subscriber:
    socket: WebSocket
    user_id: int
    role: str
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict) -> None:
        async with self.lock:
            await self.socket.send_json(payload)


class EventBroker:
    def __init__(self) -> None:
        self.subscribers: set[Subscriber] = set()

    async def publish(
        self, event: str, data: dict, customer_id: int | None = None
    ) -> None:
        payload = {
            "event": event,
            "id": uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        now = datetime.now(timezone.utc).timestamp()
        targets = [
            subscriber
            for subscriber in self.subscribers
            if subscriber.expires_at > now
            and (
                customer_id is None
                or subscriber.user_id == customer_id
                or subscriber.role in {"CASHIER", "ADMIN"}
            )
        ]
        await asyncio.gather(*(self._deliver(subscriber, payload) for subscriber in targets))

    async def _deliver(self, subscriber: Subscriber, payload: dict) -> None:
        try:
            await asyncio.wait_for(subscriber.send(payload), timeout=2)
        except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            self.subscribers.discard(subscriber)
            try:
                await subscriber.socket.close(code=1011)
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass


broker = EventBroker()
