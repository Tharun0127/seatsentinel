"""The isolation-level demo behaves as docs/ISOLATION.md says."""
from scripts.isolation_demo import scenario
from tests.conftest import PLAIN_DSN


async def test_read_committed_blind_update_loses_alices_booking(migrated_db):
    outcome = await scenario(PLAIN_DSN, "read_committed", conditional_update=False)
    assert outcome["both_told_booked"] is True
    assert outcome["final_booked_by"] == "bob"


async def test_serializable_rejects_the_second_writer(migrated_db):
    outcome = await scenario(PLAIN_DSN, "serializable", conditional_update=False)
    assert outcome["bob"] == "serialization error 40001"
    assert outcome["final_booked_by"] == "alice"


async def test_conditional_update_is_safe_under_read_committed(migrated_db):
    outcome = await scenario(PLAIN_DSN, "read_committed", conditional_update=True)
    assert outcome["bob"].startswith("0 rows updated")
    assert outcome["final_booked_by"] == "alice"
