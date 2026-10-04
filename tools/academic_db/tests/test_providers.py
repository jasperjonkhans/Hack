"""Provider plumbing that needs no network: arXiv requests go through the shared limiter."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from academic_db import providers


def test_arxiv_uses_the_shared_limiter(monkeypatch):
    slots, requests = [], []

    @contextmanager
    def fake_slot(provider, interval):
        slots.append((provider, interval))
        yield

    monkeypatch.setattr(providers, "request_slot", fake_slot)
    monkeypatch.setattr(providers, "_request",
                        lambda method, url, **kw: requests.append(kw["params"]["id_list"]) or SimpleNamespace(text="<feed/>"))
    ids = [f"2101.{n:05d}" for n in range(providers.ARXIV_CHUNK + 1)]
    assert providers.arxiv_feeds(ids) == ["<feed/>", "<feed/>"]
    assert slots == [("arxiv", providers.ARXIV_INTERVAL)] * 2  # one slot per chunked request
    assert len(requests) == 2


def test_shared_cooldown_is_reported_as_rate_limited(monkeypatch):
    if providers.SearchError is None:
        pytest.skip("literature_tools not installed")

    @contextmanager
    def cooling_down(provider, interval):
        raise providers.SearchError("rate_limited", "Provider cooldown is active", retryable=True, retry_after=30)
        yield

    monkeypatch.setattr(providers, "request_slot", cooling_down)
    with pytest.raises(providers.RateLimited):
        providers.arxiv_feeds(["1706.03762"])
