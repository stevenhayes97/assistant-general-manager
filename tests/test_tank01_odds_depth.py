from sleeper_advisor.tank01_client import (
    Tank01Client,
    _kickoff_to_gamedate,
    _parse_game_odds_consensus,
)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        for key, payload in self.routes.items():
            if key in url:
                return FakeResp(payload)
        raise AssertionError(f"Unexpected URL {url}")


def test_kickoff_to_gamedate():
    assert _kickoff_to_gamedate("2026-09-10T00:20:00Z") == "20260910"
    assert _kickoff_to_gamedate(None) is None


def test_parse_game_odds_consensus_median_and_disagreement():
    row = {
        "homeTeam": "KC",
        "awayTeam": "LV",
        "draftkings": {
            "homeTeamSpread": "-10",
            "awayTeamSpread": "+10",
            "totalOver": "44.5",
            "totalUnder": "44.5",
        },
        "fanduel": {
            "homeTeamSpread": "-9",
            "awayTeamSpread": "+9",
            "totalOver": "43.5",
            "totalUnder": "43.5",
        },
        "betmgm": {
            "homeTeamSpread": "-7",
            "awayTeamSpread": "+7",
            "totalOver": "45.5",
            "totalUnder": "45.5",
            "homeTotal": "26.5",
            "awayTotal": "19.0",
        },
    }
    consensus = _parse_game_odds_consensus(row)
    assert consensus is not None
    assert consensus.favorite == "KC"
    assert consensus.spread == 9.0  # median of 10, 9, 7
    assert consensus.total == 44.5  # median of 44.5, 43.5, 45.5
    assert consensus.spread_disagreement is True  # 10-7 = 3 >= 1.5
    assert consensus.books_count == 3
    assert consensus.team_implied_total["KC"] == 26.5
    assert "disagree on spread" in (consensus.note or "")


def test_get_odds_for_dates_uses_cache(tmp_path, monkeypatch):
    import sleeper_advisor.tank01_client as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    session = FakeSession(
        {
            "/getNFLBettingOdds": {
                "body": {
                    "LV@KC_20260910": {
                        "homeTeam": "KC",
                        "awayTeam": "LV",
                        "draftkings": {
                            "homeTeamSpread": "-6.5",
                            "awayTeamSpread": "+6.5",
                            "totalOver": "47",
                        },
                        "fanduel": {
                            "homeTeamSpread": "-6.5",
                            "awayTeamSpread": "+6.5",
                            "totalOver": "47.5",
                        },
                    }
                }
            }
        }
    )
    client = Tank01Client("k", session=session)
    out = client.get_odds_for_dates(["20260910"], force_refresh=True)
    assert set(out) == {"KC", "LV"}
    assert out["KC"].favorite == "KC"
    assert out["KC"].spread == 6.5
    assert len(session.calls) == 1
    client.get_odds_for_dates(["20260910"])
    assert len(session.calls) == 1


def test_get_depth_spots_by_sleeper_id(tmp_path, monkeypatch):
    import sleeper_advisor.tank01_client as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    session = FakeSession(
        {
            "/getNFLDepthCharts": {
                "body": [
                    {
                        "teamAbv": "DET",
                        "depthChart": {
                            "WR": [
                                {
                                    "depthPosition": "1",
                                    "playerID": "111",
                                    "longName": "Amon-Ra St. Brown",
                                },
                                {
                                    "depthPosition": "2",
                                    "playerID": "222",
                                    "longName": "Jameson Williams",
                                },
                            ]
                        },
                    }
                ]
            },
            "/getNFLPlayerList": {
                "body": [
                    {
                        "playerID": "222",
                        "sleeperBotID": "7564",
                        "espnID": "999",
                    }
                ]
            },
        }
    )
    client = Tank01Client("k", session=session)
    spots = client.get_depth_spots_by_sleeper_id(
        sleeper_players={
            "7564": {
                "team": "DET",
                "position": "WR",
                "full_name": "Jameson Williams",
            }
        },
        roster_player_ids=["7564"],
        force_refresh=True,
    )
    assert spots["7564"].role_label == "WR2"
    assert spots["7564"].depth_order == 2
    assert spots["7564"].starter_name == "Amon-Ra St. Brown"
    assert "Amon-Ra St. Brown" in spots["7564"].chart_line
