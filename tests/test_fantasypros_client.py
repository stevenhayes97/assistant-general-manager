from sleeper_advisor.fantasypros_client import FantasyProsClient


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
            "/projections": {
                "players": [
                    {
                        "fpid": 11690,
                        "name": "Jordan Reed",
                        "position_id": "TE",
                        "team_id": "WAS",
                        "stats": {
                            "points": 9.78,
                            "points_ppr": 15.52,
                            "points_half": 12.65,
                            "rec_rec": 5.74,
                        },
                    },
                    {
                        "fpid": 11180,
                        "name": "Russell Wilson",
                        "position_id": "QB",
                        "team_id": "SEA",
                        "stats": {"points": 20.64},
                    },
                ]
            }
        }
    )
    client = FantasyProsClient("test-key", session=session)
    out = client.get_week_projections(2026, 1, scoring="ppr")
    assert out["11690"].source == "fantasypros"
    assert out["11690"].pts_ppr == 15.52
    assert out["11690"].bonus_rec_te == 5.74
    assert out["11180"].pts_ppr == 20.64  # QB falls back to points
    assert session.calls[0]["headers"]["x-api-key"] == "test-key"
    assert session.calls[0]["params"]["scoring"] == "PPR"


def test_get_week_projections_retries_week_0_in_preseason():
    payloads = [
        FakeResp({"players": []}),
        FakeResp(
            {
                "players": [
                    {
                        "fpid": 1,
                        "position_id": "WR",
                        "stats": {"points": 10.0, "points_ppr": 12.0, "points_half": 11.0},
                    }
                ]
            }
        ),
    ]

    class SequencingSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append({"url": url, "params": params or {}})
            return payloads[len(self.calls) - 1]

    seq = SequencingSession()
    client = FantasyProsClient("k", session=seq)
    out = client.get_week_projections(2026, 1, scoring="ppr", season_type="pre")
    assert [c["params"]["week"] for c in seq.calls] == [1, 0]
    assert out["1"].pts_ppr == 12.0


def test_map_projections_to_sleeper_via_sportradar():
    session = FakeSession(
        {
            "/players": {
                "players": [
                    {
                        "player_id": 11690,
                        "sportsdata_player_id": "aaaa-bbbb",
                    }
                ]
            },
            "/projections": {
                "players": [
                    {
                        "fpid": 11690,
                        "position_id": "TE",
                        "stats": {
                            "points": 9.0,
                            "points_ppr": 14.0,
                            "points_half": 11.5,
                            "rec_rec": 4.0,
                        },
                    }
                ]
            },
        }
    )
    client = FantasyProsClient("k", session=session)
    # Bypass disk cache by forcing empty path via monkeypatch of cache file
    client.get_sportradar_to_fpid = lambda force_refresh=False: {"aaaa-bbbb": "11690"}  # type: ignore
    sleeper_players = {
        "12518": {"sportradar_id": "AAAA-BBBB", "full_name": "Tyler Warren"},
        "999": {"sportradar_id": "nope"},
    }
    out = client.get_projections_by_sleeper_id(
        sleeper_players=sleeper_players,
        season=2026,
        week=1,
        scoring="ppr",
        roster_player_ids=["12518", "999"],
    )
    assert set(out) == {"12518"}
    assert out["12518"].pts_ppr == 14.0
