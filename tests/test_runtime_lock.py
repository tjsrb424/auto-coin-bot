from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import main


class RuntimeLockRecoveryTests(unittest.TestCase):
    def test_start_releases_stale_lock_when_runtime_is_paused(self) -> None:
        stale_lock = {"lock_id": "auto-trading", "instance_id": "old-instance", "status": "RUNNING"}
        new_lock = {"lock_id": "auto-trading", "instance_id": "new-instance", "status": "RUNNING"}
        request = SimpleNamespace()

        with (
            patch.object(main, "_try_acquire_runtime_lock", side_effect=[(False, stale_lock), (True, new_lock)]) as acquire,
            patch.object(main, "_runtime_status_payload", return_value={"runtime_status": "PAUSED"}) as status,
            patch.object(main, "release_runtime_lock") as release,
            patch.object(main, "insert_live_mode_event") as event,
        ):
            acquired, lock, payload = main._try_acquire_runtime_lock_for_start("admin-ui", request)

        self.assertTrue(acquired)
        self.assertEqual(lock, new_lock)
        self.assertIsNone(payload)
        self.assertEqual(acquire.call_count, 2)
        status.assert_called_once_with(request)
        release.assert_called_once_with(lock_id=main.RUNTIME_LOCK_ID, instance_id="old-instance", status="STALE")
        event.assert_called_once()

    def test_start_does_not_release_lock_when_runtime_is_running(self) -> None:
        active_lock = {"lock_id": "auto-trading", "instance_id": "other-instance", "status": "RUNNING"}
        request = SimpleNamespace()

        with (
            patch.object(main, "_try_acquire_runtime_lock", return_value=(False, active_lock)) as acquire,
            patch.object(main, "_runtime_status_payload", return_value={"runtime_status": "RUNNING"}) as status,
            patch.object(main, "release_runtime_lock") as release,
            patch.object(main, "insert_live_mode_event") as event,
        ):
            acquired, lock, payload = main._try_acquire_runtime_lock_for_start("admin-ui", request)

        self.assertFalse(acquired)
        self.assertEqual(lock, active_lock)
        self.assertEqual(payload, {"runtime_status": "RUNNING"})
        acquire.assert_called_once_with("admin-ui")
        status.assert_called_once_with(request)
        release.assert_not_called()
        event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
