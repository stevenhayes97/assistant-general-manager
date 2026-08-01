from sleeper_advisor.projections import PlayerProjection
from sleeper_advisor.tank01_client import Tank01Client


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
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        for key, payload in self.routes.items():
            if key in url:
                return FakeResp(payload)
        raise AssertionError(f"Unexpected URL {url}")


def test_get_week_projections_parses_points_and_te_rec():
    session = FakeSession(
        {
            "/getNFLProjections": {
                "body": {
                    "playerProjections": {
                        "123": {
                            "playerID": "123",
                            "longName": "Jordan Reed",
                            "pos": "TE",
                            "Receiving": {"receptions": "5.74", "recYds": "60"},
                            "fantasyPointsDefault": {
                                "standard": "9.78",
                                "PPR": "15.52",
                                "halfPPR": "12.65",
                            },
                        },
                        "456": {
                            "playerID": "456",
                            "longName": "Patrick Mahomes",
                            "pos": "QB",
                            "fantasyPointsDefault": {
                                "standard": "20.64",
                                "PPR": "20.64",
                                "halfPPR": "20.64",
                            },
                        },
                    },
                    "teamDefenseProjections": {
                        "1": {
                            "teamAbv": "SF",
                            "fantasyPointsDefault": "8.5",
                        }
                    },
                }
            }
        }
    )
    client = Tank01Client("test-key", session=session)
    out = client.get_week_projections(1, scoring="ppr")
    assert out["123"].source == "tank01"
    assert out["123"].pts_ppr == 15.52
    assert out["123"].bonus_rec_te == 5.74
    assert out["456"].pts_ppr == 20.64
    assert out["SF"].pts_ppr == 8.5
    assert out["SF"].position == "DEF"
    assert session.calls[0]["headers"]["x-rapidapi-key"] == "test-key"
    assert session.calls[0]["params"]["week"] == "1"


def test_map_projections_to_sleeper_via_sleeper_bot_id():
    client = Tank01Client("k", session=FakeSession({}))
    client.get_week_projections = lambda week, scoring="ppr", archive_season=None: {  # type: ignore
        "4381786": PlayerProjection(
            source="tank01",
            pts_ppr=18.0,
            pts_half_ppr=17.0,
            pts_std=16.0,
            position="QB",
        ),
        "SF": PlayerProjection(
            source="tank01",
            pts_ppr=7.0,
            pts_half_ppr=7.0,
            pts_std=7.0,
            position="DEF",
        ),
    }
    client.get_id_maps = lambda force_refresh=False: (  # type: ignore
        {"8183": "4381786"},
        {"3117251": "3117251"},
    )
    sleeper_players = {
        "8183": {"full_name": "Brock Purdy"},
        "4034": {"espn_id": 3117251, "full_name": "Christian McCaffrey"},
        "SF": {"full_name": "San Francisco 49ers", "position": "DEF"},
        "999": {"full_name": "Nobody"},
    }
    # CMC has no sleeperBotID mapping and no projection under espn tank id — skip
    out = client.get_projections_by_sleeper_id(
        sleeper_players=sleeper_players,
        week=1,
        scoring="ppr",
        roster_player_ids=["8183", "SF", "999", "4034"],
    )
    assert set(out) == {"8183", "SF"}
    assert out["8183"].pts_ppr == 18.0
    assert out["SF"].pts_ppr == 7.0


def test_espn_fallback_join():
    client = Tank01Client("k", session=FakeSession({}))
    client.get_week_projections = lambda week, scoring="ppr", archive_season=None: {  # type: ignore
        "3117251": PlayerProjection(
            source="tank01",
            pts_ppr=22.0,
            pts_half_ppr=20.0,
            pts_std=18.0,
            position="RB",
        ),
    }
    client.get_id_maps = lambda force_refresh=False: ({}, {"3117251": "3117251"})  # type: ignore
    out = client.get_projections_by_sleeper_id(
        sleeper_players={"4034": {"espn_id": 3117251}},
        week=2,
        roster_player_ids=["4034"],
    )
    assert out["4034"].pts_ppr == 22.0


def test_get_id_maps_parses_player_list_once(tmp_path, monkeypatch):
    import sleeper_advisor.tank01_client as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    session = FakeSession(
        {
            "/getNFLPlayerList": {
                "body": [
                    {
                        "playerID": "4381786",
                        "sleeperBotID": "8183",
                        "espnID": "4422415",
                    },
                    {"playerID": "3117251", "espnID": "3117251"},
                ]
            }
        }
    )
    client = Tank01Client("k", session=session)
    sleeper_map, espn_map = client.get_id_maps(force_refresh=True)
    assert sleeper_map == {"8183": "4381786"}
    assert espn_map == {"4422415": "4381786", "3117251": "3117251"}
    assert len(session.calls) == 1
    # Second call hits cache
    client.get_id_maps()
    assert len(session.calls) == 1
