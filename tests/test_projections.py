from sleeper_advisor.projections import (
    PlayerProjection,
    describe_reception_bonuses,
    detect_scoring_format,
    league_adjusted_points,
)


def test_detect_scoring_full_ppr():
    assert detect_scoring_format({"rec": 1.0}) == "ppr"


def test_detect_scoring_half_ppr():
    assert detect_scoring_format({"rec": 0.5}) == "half_ppr"


def test_detect_scoring_standard():
    assert detect_scoring_format({"rec": 0}) == "std"
    assert detect_scoring_format({}) == "std"


def test_detect_scoring_missing_settings_defaults_ppr():
    assert detect_scoring_format(None) == "ppr"


def test_detect_scoring_snaps_near_buckets():
    assert detect_scoring_format({"rec": 0.8}) == "ppr"
    assert detect_scoring_format({"rec": 0.4}) == "half_ppr"
    assert detect_scoring_format({"rec": 0.1}) == "std"


def test_player_projection_points_for_scoring():
    proj = PlayerProjection(
        source="rotowire",
        pts_ppr=18.5,
        pts_half_ppr=16.0,
        pts_std=13.5,
    )
    assert proj.points_for("ppr") == 18.5
    assert proj.points_for("half_ppr") == 16.0
    assert proj.points_for("std") == 13.5


def test_te_premium_adds_bonus_per_projected_reception():
    # Full PPR base + 0.25 TE premium on 5.08 projected TE receptions.
    proj = PlayerProjection(
        source="rotowire",
        pts_ppr=12.94,
        pts_half_ppr=10.4,
        pts_std=7.86,
        rec=5.08,
        bonus_rec_te=5.08,
        position="TE",
    )
    settings = {"rec": 1.0, "bonus_rec_te": 0.25}
    assert league_adjusted_points(proj, "ppr", settings) == 14.21


def test_non_te_unaffected_by_te_premium():
    proj = PlayerProjection(
        source="rotowire",
        pts_ppr=15.11,
        pts_half_ppr=13.0,
        pts_std=10.5,
        rec=6.0,
        position="WR",
    )
    settings = {"rec": 1.0, "bonus_rec_te": 0.25}
    assert league_adjusted_points(proj, "ppr", settings) == 15.11


def test_te_premium_falls_back_to_rec_when_bonus_stat_missing():
    proj = PlayerProjection(
        source="rotowire",
        pts_ppr=10.0,
        pts_half_ppr=8.0,
        pts_std=6.0,
        rec=4.0,
        position="TE",
    )
    assert league_adjusted_points(proj, "ppr", {"bonus_rec_te": 0.25}) == 11.0


def test_describe_reception_bonuses():
    assert describe_reception_bonuses({"rec": 1.0, "bonus_rec_te": 0.25}) == [
        "TE +0.25/rec"
    ]
    assert describe_reception_bonuses({"rec": 1.0}) == []


def test_get_week_projections_parses_rotowire_rows():
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "player_id": "4046",
                    "company": "rotowire",
                    "stats": {
                        "pts_ppr": 20.1,
                        "pts_half_ppr": 18.2,
                        "pts_std": 16.3,
                    },
                },
                {
                    "player_id": "9999",
                    "company": "rotowire",
                    "stats": {"adp_dd_ppr": 100.0},  # no point totals — skip
                },
            ]

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            assert "projections/nfl/2025/1" in url
            assert params == {"season_type": "regular"}
            return FakeResp()

    from sleeper_advisor.projections import SleeperProjectionsClient

    client = SleeperProjectionsClient(session=FakeSession())
    out = client.get_week_projections(2025, 1, season_type="regular")
    assert out.season_type == "regular"
    assert set(out.by_player_id) == {"4046"}
    assert out.by_player_id["4046"].source == "rotowire"
    assert out.by_player_id["4046"].pts_ppr == 20.1


def test_get_week_projections_falls_back_from_pre_to_regular():
    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append(params["season_type"])
            if params["season_type"] == "pre":
                return FakeResp(
                    [{"player_id": "1", "company": "rotowire", "stats": {"adp_dd_ppr": 1.0}}]
                )
            return FakeResp(
                [
                    {
                        "player_id": "1",
                        "company": "rotowire",
                        "stats": {"pts_ppr": 12.0, "pts_half_ppr": 11.0, "pts_std": 10.0},
                    }
                ]
            )

    from sleeper_advisor.projections import SleeperProjectionsClient

    session = FakeSession()
    client = SleeperProjectionsClient(session=session)
    out = client.get_week_projections(2026, 1, season_type="pre")
    assert session.calls == ["pre", "regular"]
    assert out.season_type == "regular"
    assert out.by_player_id["1"].pts_ppr == 12.0
