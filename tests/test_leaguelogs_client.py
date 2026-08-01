from sleeper_advisor.leaguelogs_client import (
    LeagueLogsClient,
    skill_position_ids,
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
        self.calls.append({"url": url, "headers": headers or {}})
        for key, payload in self.routes.items():
            if key in url:
                if isinstance(payload, tuple):
                    body, status = payload
                    return FakeResp(body, status=status)
                return FakeResp(payload)
        raise AssertionError(f"Unexpected URL {url}")


def test_select_profile_dynasty_superflex_ppr():
    league = {
        "settings": {"type": 2},
        "roster_positions": ["QB", "RB", "WR", "TE", "SUPER_FLEX", "BN"],
        "total_rosters": 10,
    }
    assert LeagueLogsClient.select_profile(league, "ppr") == "dynasty-2qb-12t-ppr1"


def test_select_profile_redraft_half_ppr():
    league = {
        "settings": {"type": 0},
        "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "BN"],
        "total_rosters": 12,
    }
    assert (
        LeagueLogsClient.select_profile(league, "half_ppr")
        == "redraft-1qb-12t-ppr0_5"
    )


def test_select_profile_keeper_maps_to_redraft():
    league = {
        "settings": {"type": 1},
        "roster_positions": ["QB", "RB", "WR"],
    }
    assert LeagueLogsClient.select_profile(league, "ppr") == "redraft-1qb-12t-ppr1"


def test_get_market_values_parses_rows(tmp_path, monkeypatch):
    import sleeper_advisor.leaguelogs_client as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    session = FakeSession(
        {
            "/market/redraft-1qb-12t-ppr1": {
                "_attribution": {
                    "text": "Powered by LeagueLogs API",
                    "url": "https://leaguelogs.com",
                },
                "data": [
                    {
                        "sleeperPlayerId": "9221",
                        "value": 100,
                        "rawValue": 10000,
                        "overallRank": 1,
                        "positionRank": 1,
                    }
                ],
            }
        }
    )
    client = LeagueLogsClient(session=session)
    values, attr = client.get_market_values("redraft-1qb-12t-ppr1", force_refresh=True)
    assert attr is not None
    assert attr.text.startswith("Powered by LeagueLogs")
    assert values["9221"].value == 100.0
    assert values["9221"].overall_rank == 1
    assert len(session.calls) == 1
    # cache hit
    client.get_market_values("redraft-1qb-12t-ppr1")
    assert len(session.calls) == 1


def test_get_blurb_parses_and_404_returns_none():
    session = FakeSession(
        {
            "/players/4046/blurb": {
                "_attribution": {"text": "Powered by LeagueLogs API", "url": "https://leaguelogs.com"},
                "sleeperPlayerId": "4046",
                "blurb": "Knee issue in practice.",
                "generatedAt": "2026-07-31T11:52:20.377Z",
                "signals": ["injury"],
            },
            "/players/8858/blurb": ({"error": {"code": "not_found"}}, 404),
        }
    )
    client = LeagueLogsClient(session=session)
    blurb = client.get_blurb("4046")
    assert blurb is not None
    assert blurb.text.startswith("Knee")
    assert blurb.signals == ["injury"]
    assert client.get_blurb("8858") is None


def test_get_blurbs_parallel_skips_missing():
    session = FakeSession(
        {
            "/players/1/blurb": {
                "sleeperPlayerId": "1",
                "blurb": "ok",
                "signals": [],
            },
            "/players/2/blurb": ({"error": {"code": "not_found"}}, 404),
        }
    )
    client = LeagueLogsClient(session=session, max_blurb_workers=2)
    out = client.get_blurbs(["1", "2"])
    assert set(out) == {"1"}
    assert out["1"].text == "ok"


def test_skill_position_ids_filters():
    players = {
        "1": {"position": "QB"},
        "2": {"position": "K"},
        "3": {"position": "WR"},
        "SF": {"position": "DEF"},
    }
    assert skill_position_ids(["1", "2", "3", "SF", "missing"], players) == [
        "1",
        "3",
    ]
