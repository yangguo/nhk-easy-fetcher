from __future__ import annotations

from nhk_easy_fetcher.contract_summary import ContractSummary


def test_contract_summary_has_no_body_or_cookie_values() -> None:
    summary = ContractSummary(
        status_code=200,
        page_mode="classic_complete",
        body_hash="abc123",
        content_type="text/html",
        has_article_title=True,
        has_article_body=True,
        article_id="ne2026090512345",
    )
    rendered = summary.to_json()
    assert "cookie" not in rendered.lower()
    assert "article body" not in rendered.lower()
    assert "abc123" in rendered


def test_body_hash_prefix_is_stable() -> None:
    assert ContractSummary.body_hash_prefix("hello") == ContractSummary.body_hash_prefix("hello")
    assert len(ContractSummary.body_hash_prefix("hello")) == 12
