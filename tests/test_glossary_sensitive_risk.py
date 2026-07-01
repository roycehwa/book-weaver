from __future__ import annotations

from pdf_translator.glossary_sensitive_risk import assess_sensitive_content_risk


def test_religion_hong_kong_book_scores_high() -> None:
    book = {
        "metadata": {
            "title": "Religion, Secularism, and Love Hong Kong",
            "subtitle": "From Revolution to Cold War",
            "author": "Example Author",
        }
    }
    candidates = [
        {"source": "Chinese Communist Party"},
        {"source": "Cultural Revolution"},
    ]
    result = assess_sensitive_content_risk(book, candidates)
    assert result["sensitive_content_risk"] == "high"
    assert "glossary_suggest_strategy" not in result
    assert "love hong kong" in result["sensitive_content_signals"]


def test_neutral_book_scores_low() -> None:
    book = {
        "metadata": {
            "title": "Good Company",
            "author": "Lenore Palladino",
        }
    }
    result = assess_sensitive_content_risk(book, [])
    assert result["sensitive_content_risk"] == "low"
    assert "glossary_suggest_strategy" not in result
