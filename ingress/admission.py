"""Bounded FIFO transport admission; never parses or stores request contents."""
import asyncio
from collections import deque
import anyio


class AdmissionUnavailable(RuntimeError):
    pass


class AdmissionDisconnected(RuntimeError):
    pass


class TextAddAdmission:
    def __init__(self, *, concurrency=2, max_waiting=64, wait_timeout=600, poll_interval=0.1):
        if concurrency < 1 or max_waiting < 0 or wait_timeout <= 0 or poll_interval <= 0:
            raise ValueError('Invalid transport admission bounds')
        self.concurrency = concurrency
        self.max_waiting = max_waiting
        self.wait_timeout = wait_timeout
        self.poll_interval = poll_interval
        self._active = 0
        self._waiting = deque()

    def status(self):
        return {'text_add_active': self._active, 'text_add_waiting': len(self._waiting),
                'text_add_concurrency': self.concurrency,
                'text_add_max_waiting': self.max_waiting,
                'text_add_wait_timeout_seconds': self.wait_timeout}

    def _release(self):
        self._active -= 1
        while self._waiting:
            future = self._waiting.popleft()
            if not future.done():
                self._active += 1
                future.set_result(None)
                break

    async def _acquire(self, disconnected):
        # No await in admission/release mutations: one ASGI event loop owns them.
        if self._active < self.concurrency and not self._waiting:
            self._active += 1
            return
        if len(self._waiting) >= self.max_waiting:
            raise AdmissionUnavailable('text_add_queue_full')
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._waiting.append(future)
        deadline = loop.time() + self.wait_timeout
        try:
            while True:
                if disconnected is not None and await disconnected():
                    raise AdmissionDisconnected()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AdmissionUnavailable('text_add_queue_timeout')
                try:
                    await asyncio.wait_for(asyncio.shield(future), min(remaining, self.poll_interval))
                    return
                except asyncio.TimeoutError:
                    pass
        except BaseException:
            if future.done() and not future.cancelled():
                # Cancellation/expiry can race with a granted slot.
                self._release()
            else:
                future.cancel()
                self._waiting.remove(future)
            raise

    async def run(self, operation, *, disconnected=None):
        await self._acquire(disconnected)

        async def held_operation():
            try:
                return await operation()
            finally:
                self._release()

        task = asyncio.create_task(held_operation())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # An HTTP disconnect cannot cancel a synchronous model thread in the
            # backend. Keep the slot AND the caller's mode lease until its HTTP
            # call finishes, so a retry cannot oversubscribe the model semaphore.
            # Starlette uses AnyIO level cancellation. Shield the drain from its
            # cancelled parent scope to avoid repeatedly cancelling every await.
            with anyio.CancelScope(shield=True):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            if task.done() and not task.cancelled():
                task.exception()  # Retrieve errors without exposing their body.
            raise
