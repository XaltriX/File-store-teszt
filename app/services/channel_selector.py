from __future__ import annotations
import random as _random


def select_channels(channels: list[dict], n: int, strategy: str = "least", rng=None) -> list[dict]:
    """Pick up to n distinct channels. 'least': lowest `presented` first, random tie-break.
    Distinctness guarantees a channel is never reused inside one flow; if fewer than n
    channels exist the flow simply requires fewer steps."""
    rng = rng or _random
    pool = list(channels)
    n = max(0, min(n, len(pool)))
    if n == 0:
        return []
    if strategy == "random":
        return rng.sample(pool, n)
    keyed = [(c.get("presented", 0), rng.random(), c) for c in pool]
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [c for _, _, c in keyed[:n]]


def select_sticky(channels: list[dict], previous_ids: list[int], n: int, strategy: str = "least", rng=None) -> list[dict]:
    """Returning users keep the channels they verified before (so leaving one means they must
    rejoin it, and members of all of them pass instantly). Remaining slots use normal balancing."""
    by_id = {c["_id"]: c for c in channels}
    chosen = [by_id[i] for i in previous_ids if i in by_id][:max(0, n)]
    taken = {c["_id"] for c in chosen}
    rest = [c for c in channels if c["_id"] not in taken]
    return chosen + select_channels(rest, n - len(chosen), strategy, rng)
