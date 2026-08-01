from sleeper_advisor.schedule_client import (
    ScheduleClient,
    _espn_response_season_year,
    games_from_tank01_week,
)


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.last_params = params
        return FakeResp(self.payload)


def test_espn_season_mismatch_returns_empty():
    payload = {
        "season": {"year": 2025, "type": 2},
        "events": [
            {
                "competitions": [
                    {
                        "date": "2025-09-09T00:15Z",
                        "competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "CHI"}},
                            {"homeAway": "away", "team": {"abbreviation": "MIN"}},
                        ],
                        "venue": {"fullName": "Soldier Field", "indoor": False, "address": {}},
                    }
                ]
            }
        ],
    }
    client = ScheduleClient(session=FakeSession(payload))
    games = client.get_week_games(week=1, season=2026)
    assert games == {}
    assert client.last_schedule_note and "2025" in client.last_schedule_note


def test_espn_response_season_year():
    assert _espn_response_season_year({"season": {"year": 2026}}) == 2026


def test_games_from_tank01_week_chi_at_car():
    body = [
        {
            "away": "CHI",
            "home": "CAR",
            "gameDate": "20260913",
            "gameTime_epoch": "1789318800.0",
        }
    ]
    games = games_from_tank01_week(body)
    assert games["CHI"].opponent == "CAR"
    assert games["CHI"].home_away == "away"
    assert games["CAR"].opponent == "CHI"
    assert games["CAR"].home_away == "home"
    assert games["CHI"].kickoff_utc.startswith("2026-09-13")
