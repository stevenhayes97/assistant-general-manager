from sleeper_advisor.stadiums import TEAM_STADIUMS

ALL_32_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB",
    "TEN", "WAS",
}


def test_all_32_teams_present():
    assert set(TEAM_STADIUMS.keys()) == ALL_32_TEAMS


def test_roof_values_are_valid():
    valid_roofs = {"outdoor", "dome", "retractable"}
    for team, stadium in TEAM_STADIUMS.items():
        assert stadium.roof in valid_roofs, f"{team} has invalid roof: {stadium.roof}"


def test_dome_teams_not_outdoor_exposed():
    for team, stadium in TEAM_STADIUMS.items():
        if stadium.roof == "dome":
            assert stadium.is_dome
            assert not stadium.should_fetch_weather
            assert not stadium.is_outdoor_exposed, f"{team} dome should not be outdoor-exposed"
        else:
            assert stadium.should_fetch_weather
            assert stadium.is_outdoor_exposed
