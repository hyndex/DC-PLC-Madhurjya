from src.evse_hal.lock import CableLockSim


def test_cable_lock_sim_lock_unlock_cycle():
    lock = CableLockSim()
    assert lock.is_locked() is False
    lock.lock()
    assert lock.is_locked() is True
    ts1 = lock.last_command_ts()
    lock.unlock()
    assert lock.is_locked() is False
    ts2 = lock.last_command_ts()
    assert ts2 >= ts1

