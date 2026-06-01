import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import game_logic
from app.models import Group, StationLevel, Station, StealRecord

async def run_tests():
    print("🧪 Running Calibration Calibration Tests...")
    print("=" * 60)

    # Test 1: CHURCH_TIERS configurations
    print("Test 1: Verifying CHURCH_TIERS configurations...")
    assert game_logic.get_max_occupancy(0) == 50, f"Expected L0 capacity 50, got {game_logic.get_max_occupancy(0)}"
    assert game_logic.get_church_min_pop(1) == 14, f"Expected L1 pop req 14, got {game_logic.get_church_min_pop(1)}"
    assert game_logic.get_church_min_pop(2) == 150, f"Expected L2 pop req 150, got {game_logic.get_church_min_pop(2)}"
    assert game_logic.get_church_min_pop(3) == 1000, f"Expected L3 pop req 1000, got {game_logic.get_church_min_pop(3)}"
    print("✅ Test 1 Passed!")
    print("-" * 60)

    # Test 2: Stealing Calculations and Safety Net
    print("Test 2: Verifying Church Theft Calculations...")
    
    # Mock database session
    mock_db = AsyncMock()
    
    # Setup group models
    stealer = Group(id=1, name="Stealer", population=100.0, church_level=0)
    victim = Group(id=2, name="Victim", population=100.0, church_level=2)  # Steal regardless of level
    
    # We will test apply_level_upgrade on a Church Upgrade level 1 (Family Church)
    target_level = StationLevel(
        id=101,
        station_id=9,
        level_number=1,
        reward_multiplier=1.0,
        station=Station(name="Church Upgrade")
    )
    
    class MockResult:
        def __init__(self, data):
            self._data = data
        def all(self):
            return self._data
        def scalars(self):
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = self._data
            return mock_scalars
        def scalar_one_or_none(self):
            return self._data[0] if self._data else None

    # We patch game_logic helpers
    with patch("app.services.game_logic._completed_level_ids", return_value=set()), \
         patch("app.services.game_logic._all_levels", return_value=[target_level]), \
         patch("app.services.game_logic._resolve_implicit_prereqs", return_value=[]), \
         patch("app.services.game_logic.get_group_church_bonus", return_value=0.10), \
         patch("app.services.game_logic.trigger_eligibility_check") as mock_trigger:
        
        # We need mock_db.execute to return the correct models sequentially
        # query 1: Group (stealer) -> return stealer
        # query 2: Group (victim) -> return victim
        mock_db.execute.side_effect = [
            MockResult([stealer]), # select Group stealer
            MockResult([victim]),  # select Group victim
        ]
        
        # Perform upgrade & theft
        result = await game_logic.apply_level_upgrade(
            mock_db,
            group_id=1,
            station_level_id=101,
            recorded_by="test_user",
            steal_target_group_id=2
        )
        
        # Verify stolen amount is exactly 10% (10% of 100 = 10)
        assert result["stolen_amount"] == 10, f"Expected 10 stolen, got {result['stolen_amount']}"
        assert victim.population == 90, f"Expected victim left with 90, got {victim.population}"
        assert result["new_population"] == 110, f"Expected stealer new population 110, got {result['new_population']}"
        assert result["theft_applied"] is True
        print("✅ 10% Steal Calculation Verified!")

        # Verify safety net
        # Victim population = 15. 10% is 1.5 -> round to 2. Safety net limit is target - 10 = 5. Round(min(1.5, 5.0)) = 2.
        stealer.population = 100.0
        stealer.church_level = 0
        victim.population = 15.0
        
        mock_db.execute.side_effect = [
            MockResult([stealer]),
            MockResult([victim]),
        ]
        
        result_safety = await game_logic.apply_level_upgrade(
            mock_db,
            group_id=1,
            station_level_id=101,
            recorded_by="test_user",
            steal_target_group_id=2
        )
        
        assert result_safety["stolen_amount"] == 2, f"Expected 2 stolen, got {result_safety['stolen_amount']}"
        assert victim.population == 13, f"Expected victim left with 13, got {victim.population}"
        
        # Victim population = 10. 10% is 1. Max stolen = 0. round(min(1.0, 0.0)) = 0.
        stealer.population = 100.0
        stealer.church_level = 0
        victim.population = 10.0
        
        mock_db.execute.side_effect = [
            MockResult([stealer]),
            MockResult([victim]),
        ]
        
        result_safety_strict = await game_logic.apply_level_upgrade(
            mock_db,
            group_id=1,
            station_level_id=101,
            recorded_by="test_user",
            steal_target_group_id=2
        )
        
        assert result_safety_strict["stolen_amount"] == 0, f"Expected 0 stolen, got {result_safety_strict['stolen_amount']}"
        assert victim.population == 10, f"Expected victim left with 10, got {victim.population}"
        
        print("✅ Safety Net (10-member limit) Verified!")
    print("✅ Test 2 Passed!")
    print("-" * 60)

    # Test 3: Eligibility Broadcast Trigger conditions
    print("Test 3: Verifying Upgrade Eligibility Broadcast Conditions...")
    
    # We will test check_and_broadcast_upgrade_eligibility
    # Old pop = 10, new pop = 15. Next level is level 1, required min pop = 14. Old pop < 14 <= new pop is True!
    # Let's mock a group crossing threshold
    test_group = Group(id=3, name="Threshold Group", population=15.0, church_level=0)
    
    mock_db = AsyncMock()
    mock_db.execute.side_effect = [
        MockResult([test_group]), # Group fetch
        MockResult(["123456"]),   # ChatState.chat_id scalars fetch
    ]
    
    # Mocking Bot
    mock_bot_instance = AsyncMock()
    
    with patch("aiogram.Bot", return_value=mock_bot_instance) as mock_bot_cls:
        await game_logic.check_and_broadcast_upgrade_eligibility(mock_db, group_id=3, old_pop=10.0, new_pop=15.0)
        
        # Verify Bot was created and send_message was called
        assert mock_bot_cls.called, "Expected Bot to be instantiated"
        mock_bot_instance.send_message.assert_called_once()
        sent_args = mock_bot_instance.send_message.call_args[1]
        assert "CHURCH ELIGIBLE FOR UPGRADE!" in sent_args["text"], "Eligibility message content invalid"
        assert "14" in sent_args["text"], "Expected required threshold value in message"
        print("✅ Upgrade eligibility crossing correctly triggers Telegram broadcast!")

        # Verify not triggered when already met
        # Old pop = 16, new pop = 20. Required = 14. Old pop < 14 <= new pop is False!
        mock_bot_instance.reset_mock()
        mock_bot_cls.reset_mock()
        mock_db.execute.side_effect = [
            MockResult([test_group]),
        ]
        await game_logic.check_and_broadcast_upgrade_eligibility(mock_db, group_id=3, old_pop=16.0, new_pop=20.0)
        assert not mock_bot_cls.called, "Should not broadcast if threshold was already met previously"
        
        # Verify not triggered when not met yet
        # Old pop = 10, new pop = 13. Required = 14. Old pop < 14 <= new pop is False!
        mock_db.execute.side_effect = [
            MockResult([test_group]),
        ]
        await game_logic.check_and_broadcast_upgrade_eligibility(mock_db, group_id=3, old_pop=10.0, new_pop=13.0)
        assert not mock_bot_cls.called, "Should not broadcast if threshold is not met yet"
        
        print("✅ Non-crossing bounds verified correctly (no duplicate/premature broadcasts)!")

    print("✅ Test 3 Passed!")
    print("=" * 60)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
