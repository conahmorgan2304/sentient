"""Local semantic ranking is opt-in and cannot expand memory permissions."""
import pytest

from custom_components.sentient.semantic_memory import (
    allowed_endpoint, rank_vectors, similarity, async_rerank,
)


def test_embedding_endpoint_is_explicitly_local_and_non_redirecting():
    assert allowed_endpoint("http://127.0.0.1:11434")
    assert allowed_endpoint("http://192.168.1.15:11434/api/embed")
    assert not allowed_endpoint("https://api.openai.com:443")
    assert not allowed_endpoint("http://169.254.169.254:80")
    assert not allowed_endpoint("http://example.com:11434")
    assert not allowed_endpoint("http://127.0.0.1:11434/redirect")
    assert not allowed_endpoint("http://localhost:11434/?token=abc")


def test_semantic_ranking_remains_within_preapproved_candidates():
    allowed = [{"text": "The kettle is in the kitchen"}, {"text": "The heating boiler is in the shed"}]
    vectors = [[1, 0], [0, 1], [1, 0]]
    sorted_rows = rank_vectors(allowed, vectors)
    assert sorted_rows[0] is allowed[1]
    assert sorted_rows[1] is allowed[0]
    assert len(sorted_rows) == len(allowed)
    assert similarity([1, 0], [float("nan"), 1]) is None
    assert rank_vectors(allowed, [[1], [1]]) == allowed


async def test_disabled_semantic_search_never_calls_network(hass, monkeypatch):
    rows = [{"text": "A"}, {"text": "B"}]
    from custom_components.sentient import semantic_memory
    def forbidden(_):
        raise AssertionError("No network request should be made when disabled")
    monkeypatch.setattr(semantic_memory, "async_get_clientsession", forbidden)
    assert await async_rerank(hass, {}, "question", rows) is rows
    assert await async_rerank(hass, {"memory_semantic_enabled": True,
                                     "memory_semantic_url": "https://external.invalid:11434",
                                     "memory_semantic_model": "nomic-embed-text"}, "question", rows) is rows
