"""Bounded JSON invalidations, published only after database commits."""
import asyncio
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["subscribe"]
    account_ids: list[str] = Field(default_factory=list, max_length=100)


class Events:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.clients = set()

    def publish(self, event):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event):
        for queue in tuple(self.clients):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(dict(type="resync"))
            else:
                queue.put_nowait(event)

    async def serve(self, socket, store):
        await socket.accept()
        queue = asyncio.Queue(maxsize=64)
        self.clients.add(queue)
        try:
            # Bound the first frame and lifetime of an unsubscribed connection.
            raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
            if len(raw) > 16384:
                await socket.close(code=1008)
                return
            subscription = Subscription.model_validate_json(raw)
            accounts = await asyncio.to_thread(store.accounts)
            known = {a["account_id"] for a in accounts}
            if not set(subscription.account_ids) <= known:
                await socket.close(code=1008)
                return
            await socket.send_json(dict(type="ready", revisions={a["account_id"]: a["revision"] for a in accounts}))
            receive = asyncio.create_task(socket.receive())
            try:
                while True:
                    pending = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait([receive, pending], return_when=asyncio.FIRST_COMPLETED)
                    if receive in done:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                        break  # A new subscription uses a new connection; no unbounded input stream.
                    event = pending.result()
                    if not subscription.account_ids or not event.get("account_id") or event["account_id"] in subscription.account_ids:
                        await socket.send_json(event)
            finally:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)
        except (ValueError, asyncio.TimeoutError):
            await socket.close(code=1008)
        finally:
            self.clients.discard(queue)
