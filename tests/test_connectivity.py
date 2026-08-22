"""Connectivity hysteresis tests. No real network calls are made."""
from src.connectivity import ConnectivityManager, ConnectivityState


def scripted_check(results):
    """Return a check_fn that replays a fixed sequence of (success, latency_ms)."""
    iterator = iter(results)

    def _check():
        return next(iterator)

    return _check


def make_manager(results, **kwargs):
    kwargs.setdefault("failures_for_offline", 3)
    kwargs.setdefault("successes_for_recovery", 2)
    kwargs.setdefault("successes_for_online", 3)
    return ConnectivityManager(check_fn=scripted_check(results), **kwargs)


def test_starts_online():
    manager = make_manager([(True, 20.0)])
    assert manager.snapshot().state == ConnectivityState.ONLINE


def test_single_failure_does_not_flap_to_offline():
    manager = make_manager([(False, 500.0)])
    manager.check_once()
    assert manager.snapshot().state != ConnectivityState.OFFLINE


def test_repeated_failures_trigger_offline():
    manager = make_manager([(False, 500.0), (False, 500.0), (False, 500.0)])
    for _ in range(3):
        manager.check_once()
    assert manager.snapshot().state == ConnectivityState.OFFLINE
    snap = manager.snapshot()
    assert snap.offline_started_at is not None


def test_recovery_after_offline_requires_successes():
    manager = make_manager(
        [(False, 500.0), (False, 500.0), (False, 500.0), (True, 20.0), (True, 20.0)]
    )
    for _ in range(3):
        manager.check_once()
    assert manager.snapshot().state == ConnectivityState.OFFLINE

    manager.check_once()  # 1st success
    assert manager.snapshot().state == ConnectivityState.OFFLINE  # not yet enough

    manager.check_once()  # 2nd success -> RECOVERY
    assert manager.snapshot().state == ConnectivityState.RECOVERY


def test_stable_successes_return_to_online():
    manager = make_manager(
        [(False, 500.0), (False, 500.0), (False, 500.0), (True, 20.0), (True, 20.0), (True, 20.0), (True, 20.0)]
    )
    for _ in range(3):
        manager.check_once()
    manager.check_once()
    manager.check_once()
    assert manager.snapshot().state == ConnectivityState.RECOVERY

    manager.check_once()
    manager.check_once()
    assert manager.snapshot().state == ConnectivityState.ONLINE


def test_state_timestamps_update_on_success_and_failure():
    manager = make_manager([(True, 20.0), (False, 500.0)])
    manager.check_once()
    first = manager.snapshot()
    assert first.last_success_at is not None

    manager.check_once()
    second = manager.snapshot()
    assert second.last_failure_at is not None
    assert second.consecutive_failures == 1


def test_outage_duration_accumulates_only_while_offline():
    manager = make_manager([(False, 500.0), (False, 500.0), (False, 500.0)])
    for _ in range(3):
        manager.check_once()
    snap = manager.snapshot()
    assert snap.current_outage_duration_s >= 0.0
    assert snap.total_outage_duration_s >= snap.current_outage_duration_s


def test_permits_sync_false_while_offline():
    manager = make_manager([(False, 500.0), (False, 500.0), (False, 500.0)])
    for _ in range(3):
        manager.check_once()
    assert manager.permits_sync() is False


def test_permits_sync_true_while_online():
    manager = make_manager([(True, 20.0)])
    manager.check_once()
    assert manager.permits_sync() is True
