from sleeper_advisor.context_builder import (
    BLOWOUT_SPREAD_THRESHOLD,
    classify_game_script,
)
from sleeper_advisor.odds_client import GameOdds


def test_classify_no_odds_returns_none():
    flag, note = classify_game_script("KC", None)
    assert flag is None
    assert note is None


def test_classify_big_favorite_flags_blowout_risk():
    odds = GameOdds(
        favorite="KC",
        spread=BLOWOUT_SPREAD_THRESHOLD + 1,
        total=44.0,
        team_implied_total={"KC": 27.0, "LV": 17.0},
    )
    flag, note = classify_game_script("KC", odds)
    assert flag == "blowout_risk_favorite"
    assert "ease" in note.lower() or "gas" in note.lower() or "risk" in note.lower()


def test_classify_big_underdog_flags_blowout_risk_underdog():
    odds = GameOdds(
        favorite="KC",
        spread=BLOWOUT_SPREAD_THRESHOLD + 1,
        total=44.0,
        team_implied_total={"KC": 27.0, "LV": 17.0},
    )
    flag, note = classify_game_script("LV", odds)
    assert flag == "blowout_risk_underdog"


def test_classify_low_total_flags_grind_it_out():
    odds = GameOdds(favorite="NYJ", spread=3.0, total=38.0, team_implied_total={})
    flag, note = classify_game_script("NYJ", odds)
    assert flag == "low_total"


def test_classify_competitive_game():
    odds = GameOdds(favorite="SF", spread=2.5, total=47.0, team_implied_total={})
    flag, note = classify_game_script("SF", odds)
    assert flag == "competitive"
