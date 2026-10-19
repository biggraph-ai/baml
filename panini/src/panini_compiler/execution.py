"""Bounded scheduler waits. Local process isolation does not undo remote work."""

from __future__ import annotations

import multiprocessing
import queue
import threading
import time


class InvocationTimeout(TimeoutError):
    pass


def _process_entry(send, callback, args):
    try:
        send.send(("ok", callback(*args)))
    except BaseException as error:
        send.send(("error", type(error).__name__))
    finally:
        send.close()


def isolated_call(callback, args: tuple, timeout: float):
    if "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("Process-isolated local tasks currently require POSIX fork")
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_process_entry, args=(send, callback, args), daemon=True
    )
    deadline = time.monotonic() + timeout
    process.start()
    send.close()
    try:
        while not receive.poll(min(3600, max(0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                raise InvocationTimeout("Local task deadline expired")
        try:
            kind, value = receive.recv()
        except EOFError as error:
            raise RuntimeError("Isolated task exited without a result") from error
        if kind == "error":
            raise RuntimeError(f"Isolated callback failed ({value})")
        return value
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
        process.join(0.1)
        if process.is_alive():
            process.kill()
            process.join(0.1)


def capability_call(callback, args: tuple, timeout: float, cancel_event=None):
    # One private queue per invocation. A stale result can never be consumed by
    # a subsequent invocation, even if the remote adapter ignores cancellation.
    results = queue.Queue(maxsize=1)

    def invoke():
        try:
            results.put(("ok", callback(*args)))
        except BaseException as error:
            results.put(("error", error))

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    try:
        kind, value = results.get(timeout=timeout)
    except queue.Empty as error:
        if cancel_event is not None:
            cancel_event.set()
        raise InvocationTimeout(
            "Capability deadline expired; remote outcome may be unknown"
        ) from error
    if kind == "error":
        raise value
    return value
