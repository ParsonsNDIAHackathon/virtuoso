"""Bound optional network work, including DNS and slow response bodies."""
import threading
from concurrent.futures import Future


class BoundedCalls:
    def __init__(self, capacity=4):
        self.slots = threading.BoundedSemaphore(capacity)

    def call(self, function, timeout, label):
        if not self.slots.acquire(blocking=False):
            raise TimeoutError(f"{label} is busy; using available record fields")
        result = Future()

        def work():
            try:
                result.set_result(function())
            except BaseException as error:
                result.set_exception(error)
            finally:
                self.slots.release()

        threading.Thread(target=work, daemon=True, name="fusion-context").start()
        try:
            return result.result(timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"{label} exceeded {timeout:g} seconds; using available record fields") from None
