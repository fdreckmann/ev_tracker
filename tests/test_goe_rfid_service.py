"""
Tests for PR 5 — go-e RFID/Card mapping service.

Test numbers align with spec section 19:
  26  one card energy increases → vehicle from mapping confirmed
  27  multiple cards increase → conflict → unassigned
  28  no card energy increases → unassigned

Additional coverage:
  - snapshot parsing from go-e v2 cae array
  - card not in mapping → unassigned
  - resolve_vehicle_from_cards API
"""
import pytest
from services.goe_rfid_service import (
    _parse_card_snapshot,
    resolve_vehicle_from_cards,
    CARD_DELTA_MIN_WH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(card_energies_wh: list[float],
              names: list[str] | None = None) -> dict:
    """Build a snapshot dict as returned by read_card_energy_snapshot()."""
    cards = []
    for slot, energy_wh in enumerate(card_energies_wh):
        name = (names or [])[slot] if names and slot < len(names) else f"Karte {slot + 1}"
        cards.append({"slot": slot, "name": name, "energy_wh": energy_wh})
    return {"cards": cards, "raw": {}}


def _cfg(card_vehicle_map: dict | None = None) -> dict:
    return {
        "goe_rfid_enabled": True,
        "goe_card_vehicle_map": card_vehicle_map or {0: "v0", 1: "extra_v1"},
    }


# ---------------------------------------------------------------------------
# Test 26 — one card increases → vehicle confirmed
# ---------------------------------------------------------------------------

class TestOneCardIncreases:
    def test_26_one_card_confirmed(self):
        """Exactly one card energy increases → vehicle confirmed from mapping."""
        before = _snapshot([10000.0, 5000.0, 0.0])
        after  = _snapshot([10000.0, 5000.0 + 18000.0, 0.0])  # slot 1 used: +18 kWh

        vid, status, card_name = resolve_vehicle_from_cards(
            _cfg({0: "v0", 1: "extra_v1"}), before, after)

        assert vid == "extra_v1"
        assert status == "confirmed"

    def test_26b_slot_0_confirmed(self):
        """Slot 0 used → vehicle v0."""
        before = _snapshot([1000.0, 2000.0])
        after  = _snapshot([1000.0 + 15000.0, 2000.0])

        vid, status, _ = resolve_vehicle_from_cards(
            _cfg({0: "v0", 1: "extra_v1"}), before, after)

        assert vid == "v0"
        assert status == "confirmed"

    def test_26c_small_delta_below_threshold_not_triggered(self):
        """Delta below CARD_DELTA_MIN_WH does not trigger assignment."""
        before = _snapshot([10000.0])
        after  = _snapshot([10000.0 + CARD_DELTA_MIN_WH - 1])  # just below threshold

        vid, status, _ = resolve_vehicle_from_cards(_cfg({0: "v0"}), before, after)

        assert status == "unassigned"
        assert vid is None

    def test_26d_exactly_at_threshold_triggers(self):
        """Delta exactly at CARD_DELTA_MIN_WH triggers assignment."""
        before = _snapshot([10000.0])
        after  = _snapshot([10000.0 + CARD_DELTA_MIN_WH])

        vid, status, _ = resolve_vehicle_from_cards(_cfg({0: "v0"}), before, after)

        assert status == "confirmed"
        assert vid == "v0"


# ---------------------------------------------------------------------------
# Test 27 — multiple cards increase → conflict → unassigned
# ---------------------------------------------------------------------------

class TestMultipleCardsIncrease:
    def test_27_multiple_cards_conflict(self):
        """Multiple card energy counters increase → conflict → unassigned."""
        before = _snapshot([1000.0, 2000.0, 3000.0])
        after  = _snapshot([1000.0 + 5000.0, 2000.0 + 8000.0, 3000.0])

        vid, status, _ = resolve_vehicle_from_cards(
            _cfg({0: "v0", 1: "extra_v1"}), before, after)

        assert vid is None
        assert status == "unassigned"

    def test_27b_two_of_three_increase(self):
        """Two cards increase even with only one mapped → still unassigned."""
        before = _snapshot([100.0, 200.0, 300.0])
        after  = _snapshot([100.0 + 10000.0, 200.0 + 10000.0, 300.0])

        vid, status, _ = resolve_vehicle_from_cards(
            _cfg({0: "v0"}), before, after)

        assert vid is None
        assert status == "unassigned"


# ---------------------------------------------------------------------------
# Test 28 — no card increases → unassigned
# ---------------------------------------------------------------------------

class TestNoCardIncreases:
    def test_28_no_increase_unassigned(self):
        """No card energy counter increases → unassigned."""
        before = _snapshot([5000.0, 3000.0, 1000.0])
        after  = _snapshot([5000.0, 3000.0, 1000.0])

        vid, status, _ = resolve_vehicle_from_cards(
            _cfg({0: "v0", 1: "extra_v1"}), before, after)

        assert vid is None
        assert status == "unassigned"

    def test_28b_no_cards_at_all(self):
        """Empty card arrays → unassigned."""
        before = _snapshot([])
        after  = _snapshot([])

        vid, status, _ = resolve_vehicle_from_cards(_cfg(), before, after)

        assert vid is None
        assert status == "unassigned"

    def test_28c_card_decreases_not_triggered(self):
        """A decreasing counter (e.g. reset) must not trigger assignment."""
        before = _snapshot([5000.0])
        after  = _snapshot([4000.0])

        vid, status, _ = resolve_vehicle_from_cards(_cfg({0: "v0"}), before, after)

        assert vid is None
        assert status == "unassigned"


# ---------------------------------------------------------------------------
# Additional coverage
# ---------------------------------------------------------------------------

class TestCardNotInMapping:
    def test_card_not_mapped_unassigned(self):
        """Active card not in goe_card_vehicle_map → unassigned but card_name set."""
        before = _snapshot([0.0, 0.0])
        after  = _snapshot([0.0, 15000.0])

        # Slot 1 is NOT in the map
        vid, status, card_name = resolve_vehicle_from_cards(
            _cfg({0: "v0"}), before, after)

        assert vid is None
        assert status == "unassigned"
        assert card_name == "Karte 2"

    def test_card_name_returned_on_confirmed(self):
        """Card name is returned alongside confirmed vehicle."""
        before = _snapshot([0.0], names=["Mein ID.7"])
        after  = _snapshot([20000.0], names=["Mein ID.7"])

        vid, status, card_name = resolve_vehicle_from_cards(
            _cfg({0: "v0"}), before, after)

        assert status == "confirmed"
        assert card_name == "Mein ID.7"


class TestSnapshotParsing:
    def test_parse_cae_array(self):
        """_parse_card_snapshot correctly converts cae (0.1 Wh) to energy_wh."""
        # go-e stores cae in units of 0.1 Wh → multiply by 10 to get Wh
        data = {"cae": [1000, 2000, 0], "cards": []}
        snap = _parse_card_snapshot(data)

        assert len(snap["cards"]) == 3
        assert snap["cards"][0]["energy_wh"] == pytest.approx(10000.0)  # 1000 * 10
        assert snap["cards"][1]["energy_wh"] == pytest.approx(20000.0)

    def test_parse_cards_with_names(self):
        """Card names from ``cards`` array are preserved."""
        data = {
            "cae": [500, 0],
            "cards": [{"name": "Familie", "rfid": "abc"}, {"name": "Gast", "rfid": "def"}],
        }
        snap = _parse_card_snapshot(data)
        assert snap["cards"][0]["name"] == "Familie"
        assert snap["cards"][1]["name"] == "Gast"

    def test_parse_empty_response(self):
        """Empty go-e response → empty cards list."""
        snap = _parse_card_snapshot({})
        assert snap["cards"] == []


class TestConfigMapping:
    def test_string_keys_in_map(self):
        """goe_card_vehicle_map with string keys (as stored in JSON) works."""
        before = _snapshot([0.0, 0.0])
        after  = _snapshot([0.0, 18000.0])

        cfg = {
            "goe_rfid_enabled": True,
            "goe_card_vehicle_map": {"0": "v0", "1": "extra_v1"},
        }
        vid, status, _ = resolve_vehicle_from_cards(cfg, before, after)

        assert vid == "extra_v1"
        assert status == "confirmed"

    def test_json_string_map(self):
        """goe_card_vehicle_map stored as JSON string is parsed correctly."""
        import json
        before = _snapshot([0.0])
        after  = _snapshot([15000.0])

        cfg = {
            "goe_rfid_enabled": True,
            "goe_card_vehicle_map": json.dumps({"0": "v0"}),
        }
        vid, status, _ = resolve_vehicle_from_cards(cfg, before, after)

        assert vid == "v0"
        assert status == "confirmed"
