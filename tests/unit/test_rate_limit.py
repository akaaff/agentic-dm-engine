"""Issue #43's TokenBucket - a fake clock (a plain mutable counter, not real
time.sleep) makes every case deterministic and fast, matching this project's
existing "inject the RNG, don't rely on real timing" discipline elsewhere.
"""

from src.api.ws.rate_limit import TokenBucket


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_capacity_requests_allowed_then_the_next_one_rejected() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(capacity=3, refill_per_second=0, now_fn=clock)

    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_bucket_refills_over_time_and_allows_more_after_the_window() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_second=1, now_fn=clock)

    assert bucket.try_consume() is True
    assert bucket.try_consume() is False  # no time has passed yet

    clock.advance(1.0)  # exactly one token's worth of refill
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_refill_never_exceeds_capacity() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_second=1, now_fn=clock)

    clock.advance(100.0)  # would refill far past capacity if unclamped
    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_partial_refill_is_not_enough_for_a_full_token() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_second=1, now_fn=clock)

    bucket.try_consume()
    clock.advance(0.5)  # only half a token back
    assert bucket.try_consume() is False
    clock.advance(0.5)  # now a full token
    assert bucket.try_consume() is True
