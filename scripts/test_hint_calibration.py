import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import game_logic
from app.models import Group, StationLevel, Station

async def run_tests():
    print("🧪 Running Hint Pricing Calibration Tests...")
    print("=" * 60)

    # We will test buy_church_hint for various hint numbers and preceded group counts (N)
    # Test cases: (hint_number, N, group_population, expected_cost_percentage, expected_cost)
    test_cases = [
        # Hint 1 (base 5%)
        (1, 0, 1000, 0.05, 50),   # N=0: 5% cost -> 50 members
        (1, 1, 1000, 0.04, 40),   # N=1: 4% cost -> 40 members
        (1, 2, 1000, 0.03, 30),   # N=2: 3% cost -> 30 members
        (1, 3, 1000, 0.03, 30),   # N=3: 3% cost (floor capped) -> 30 members
        (1, 10, 1000, 0.03, 30),  # N=10: 3% cost (floor capped) -> 30 members

        # Hint 2 (base 10%)
        (2, 0, 1000, 0.10, 100),  # N=0: 10% cost -> 100 members
        (2, 5, 1000, 0.05, 50),   # N=5: 5% cost -> 50 members
        (2, 7, 1000, 0.03, 30),   # N=7: 3% cost -> 30 members
        (2, 8, 1000, 0.03, 30),   # N=8: 3% cost (floor capped) -> 30 members

        # Hint 3 (base 15%)
        (3, 0, 1000, 0.15, 150),  # N=0: 15% cost -> 150 members
        (3, 10, 1000, 0.05, 50),  # N=10: 5% cost -> 50 members
        (3, 12, 1000, 0.03, 30),  # N=12: 3% cost -> 30 members
        (3, 15, 1000, 0.03, 30),  # N=15: 3% cost (floor capped) -> 30 members
    ]

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
        def scalar(self):
            return self._data[0] if self._data else None

    for idx, (hint_num, N, pop, expected_pct, expected_cost) in enumerate(test_cases):
        print(f"Subtest {idx+1}: Hint {hint_num}, N={N}, pop={pop}...")
        
        # Setup fresh group
        group = Group(id=1, name="Test Group", population=float(pop), church_level=1)
        
        # Setup station level
        station_level = StationLevel(
            id=202,
            station_id=9,
            level_number=2,
            reward_multiplier=1.0,
            station=Station(name="Church Upgrade")
        )

        mock_db = AsyncMock()
        
        # Mock database execute calls:
        # 1. Fetch group -> return group
        # 2. Fetch level -> return station_level
        # 3. Check if already purchased -> return None (no purchase yet)
        # 4. Fetch N (number of completions) -> return N
        mock_db.execute.side_effect = [
            MockResult([group]),
            MockResult([station_level]),
            MockResult([]),
            MockResult([N])
        ]

        # Patch trigger and any extra network or side-effect heavy logic if any
        result = await game_logic.buy_church_hint(mock_db, group_id=1, station_level_id=202, hint_number=hint_num)

        # Assert correct cost calculation
        calculated_cost = result["cost"]
        assert calculated_cost == expected_cost, f"Expected cost {expected_cost}, got {calculated_cost} for Hint {hint_num}, N={N}, pop={pop}"
        
        # Assert population deducted correctly
        expected_new_pop = pop - expected_cost
        assert group.population == expected_new_pop, f"Expected group pop {expected_new_pop}, got {group.population}"
        
        # Assert database commit was called
        mock_db.commit.assert_called_once()
        
        print(f"   ↳ Passed! Cost: {calculated_cost} (matches expected {expected_cost})")

    # Safety Net test:
    # A purchase that drops population below 10 should be blocked
    print("\nSubtest Safety Net: Verification that purchase dropping below 10 is blocked...")
    group = Group(id=1, name="Test Group", population=12.0, church_level=1)
    station_level = StationLevel(
        id=202,
        station_id=9,
        level_number=2,
        reward_multiplier=1.0,
        station=Station(name="Church Upgrade")
    )
    mock_db = AsyncMock()
    mock_db.execute.side_effect = [
        MockResult([group]),
        MockResult([station_level]),
        MockResult([]),
        MockResult([0])  # N=0
    ]
    # For Hint 1, base is 5%. 5% of 12 = 0.6 -> rounded to 1.
    # Group pop after purchase: 12 - 1 = 11 >= 10. (Should pass)
    result = await game_logic.buy_church_hint(mock_db, group_id=1, station_level_id=202, hint_number=1)
    assert result["cost"] == 1
    assert group.population == 11

    # Now let's try Hint 3: base is 15%. 15% of 11 = 1.65 -> rounded to 2.
    # Group pop after purchase: 11 - 2 = 9 < 10. (Should be blocked!)
    mock_db = AsyncMock()
    mock_db.execute.side_effect = [
        MockResult([group]),
        MockResult([station_level]),
        MockResult([]),
        MockResult([0])  # N=0
    ]
    try:
        await game_logic.buy_church_hint(mock_db, group_id=1, station_level_id=202, hint_number=3)
        assert False, "Should have raised ValueError due to safety net"
    except ValueError as e:
        assert "safety net" in str(e).lower(), f"Expected safety net error message, got: {e}"
        print("   ↳ Passed! Safety net correctly blocked purchase.")

    print("=" * 60)
    print("🎉 ALL HINT PRICING TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
