"""A released lease's cohort reservation turns away more of the same herd,
but a contender with its own mutation (the PostCompact refresh) may take it."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"))
from hook_runtime import LeaseLock  # noqa: E402


def _released_lease(path, *, released=1):
    now = time.time()
    path.write_text(json.dumps({
        "pid": 999999, "nonce": "a" * 32, "released": released,
        "created_wall": now, "reuse_wall": now + 8, "expires_wall": now + 8,
    }), encoding="utf-8")


def test_released_reservation_turns_away_the_herd(tmp_path):
    lease = tmp_path / "q.qlease"
    _released_lease(lease)
    assert LeaseLock(lease, acquire_timeout=0.05).acquire() is False


def test_released_lease_can_be_taken_by_a_distinct_mutation(tmp_path):
    lease = tmp_path / "q.qlease"
    _released_lease(lease)
    lock = LeaseLock(lease, acquire_timeout=0.05, reclaim_released=True)
    assert lock.acquire() is True
    lock.release()


def test_a_held_lease_is_never_taken_early(tmp_path):
    lease = tmp_path / "q.qlease"
    _released_lease(lease, released=0)
    assert LeaseLock(lease, acquire_timeout=0.05, reclaim_released=True).acquire() is False
