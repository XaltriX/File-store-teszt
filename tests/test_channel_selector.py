import random

from app.services.channel_selector import select_channels


def chans(*presented):
    return [{"_id": i, "presented": p} for i, p in enumerate(presented, 1)]


def test_least_presented_first():
    out = select_channels(chans(5, 1, 3), 2, "least", random.Random(0))
    assert [c["_id"] for c in out] == [2, 3]


def test_ties_are_broken_randomly_but_only_among_ties():
    seen = set()
    for seed in range(40):
        out = select_channels(chans(0, 0, 0, 9), 1, "least", random.Random(seed))
        seen.add(out[0]["_id"])
    assert seen == {1, 2, 3}


def test_no_repeats_within_a_flow():
    out = select_channels(chans(0, 0, 0), 3, "least", random.Random(1))
    assert len({c["_id"] for c in out}) == 3


def test_fewer_channels_than_steps_reduces_steps():
    assert len(select_channels(chans(0), 3)) == 1
    assert select_channels([], 2) == []


def test_random_strategy_returns_distinct():
    out = select_channels(chans(100, 0, 0, 0), 3, "random", random.Random(3))
    assert len({c["_id"] for c in out}) == 3
