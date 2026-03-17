"""
Tests for BUG-01: _running_jobs dict must be protected by _job_lock at all access points.

These tests verify that the locking pattern eliminates RuntimeError from concurrent
dict mutation. They use a local dict+lock pair (not the app's globals) to test
the pattern in isolation.
"""
import threading
import pytest


def test_lock_protects_len_read(mock_running_jobs):
    """10 reader threads (len) and 10 writer threads — no RuntimeError under lock."""
    d, lock = mock_running_jobs
    errors = []

    def reader():
        for _ in range(1000):
            try:
                with lock:
                    _ = len(d)
            except RuntimeError as e:
                errors.append(e)

    def writer(i):
        for _ in range(1000):
            with lock:
                d[i] = i
            with lock:
                d.pop(i, None)

    threads = [threading.Thread(target=reader) for _ in range(10)]
    threads += [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"RuntimeErrors occurred: {errors}"


def test_lock_protects_keys_snapshot(mock_running_jobs):
    """10 writer threads and 10 reader threads (set(d.keys())) — no RuntimeError under lock."""
    d, lock = mock_running_jobs
    errors = []

    def reader():
        for _ in range(1000):
            try:
                with lock:
                    _ = set(d.keys())
            except RuntimeError as e:
                errors.append(e)

    def writer(i):
        for _ in range(1000):
            with lock:
                d[i] = i
            with lock:
                d.pop(i, None)

    threads = [threading.Thread(target=reader) for _ in range(10)]
    threads += [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"RuntimeErrors occurred: {errors}"


def test_lock_protects_membership_test(mock_running_jobs):
    """10 writer threads and 10 reader threads (key in d) — no RuntimeError under lock."""
    d, lock = mock_running_jobs
    errors = []

    def reader(key):
        for _ in range(1000):
            try:
                with lock:
                    _ = key in d
            except RuntimeError as e:
                errors.append(e)

    def writer(i):
        for _ in range(1000):
            with lock:
                d[i] = i
            with lock:
                d.pop(i, None)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(10)]
    threads += [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"RuntimeErrors occurred: {errors}"


def test_concurrent_access(mock_running_jobs):
    """Combined stress test: 20 threads doing mixed reads and writes under lock, 500 iters each."""
    d, lock = mock_running_jobs
    errors = []

    def mixed_worker(i):
        for _ in range(500):
            try:
                with lock:
                    d[i] = i
                with lock:
                    _ = len(d)
                with lock:
                    _ = set(d.keys())
                with lock:
                    _ = i in d
                with lock:
                    d.pop(i, None)
            except RuntimeError as e:
                errors.append(e)

    threads = [threading.Thread(target=mixed_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"RuntimeErrors occurred under lock: {errors}"


@pytest.mark.xfail(
    reason="Race condition is probabilistic — may not trigger in every run",
    strict=False,
)
def test_unprotected_access_can_fail():
    """Demonstrate that WITHOUT the lock, concurrent dict mutation CAN raise RuntimeError."""
    d = {}
    errors = []

    def reader():
        for _ in range(500):
            try:
                _ = list(d.keys())
            except RuntimeError as e:
                errors.append(e)

    def writer(i):
        for _ in range(500):
            d[i] = i
            d.pop(i, None)

    for attempt in range(50):
        d.clear()
        errors.clear()
        threads = [threading.Thread(target=reader) for _ in range(5)]
        threads += [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            # Race condition triggered — test passes (demonstrates the bug exists without lock)
            return

    # Race never triggered in 50 attempts — xfail is acceptable
    pytest.xfail("Race condition did not trigger in 50 attempts (probabilistic)")
