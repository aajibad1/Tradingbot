from __future__ import annotations
from replay import SignalOutcome, label, replay


def _o(realized, reached=True, family="momentum_dislocation", regime="trending"):
    return SignalOutcome(family=family, regime=regime, direction="long",
                         gross_edge_bps=30.0, realized_bps=realized, reached_execution=reached)


def test_label_true_false_decayed():
    assert label(_o(20)) == "true_positive"
    assert label(_o(-5)) == "false_positive"
    assert label(_o(20, reached=False)) == "decayed"


def test_replay_false_positive_rate():
    r = replay([_o(20), _o(-5), _o(10), _o(-8)])  # 2 of 4 executed lost
    assert r.false_positive_rate == 0.5
    assert r.n == 4 and r.decay_rate == 0.0


def test_decay_rate_excludes_from_fp():
    r = replay([_o(20), _o(-5, reached=False), _o(10)])  # 1 decayed; executed=2, 0 fp
    assert r.decay_rate == round(1/3, 4)
    assert r.false_positive_rate == 0.0


def test_breakdown_by_family_and_regime():
    r = replay([_o(20, family="momentum_dislocation", regime="trending"),
                _o(-5, family="stat_arb_reversion", regime="mean_reverting")])
    assert "momentum_dislocation" in r.by_family and "stat_arb_reversion" in r.by_family
    assert r.by_regime["trending"]["win_rate"] == 1.0
    assert r.by_regime["mean_reverting"]["false_positive_rate"] == 1.0


def test_empty():
    r = replay([])
    assert r.n == 0 and r.false_positive_rate == 0.0
