import pytest
from aura.query_cleaner import clean_search_query
from aura.llm_provider import clean_llm_response, extract_json_object


def test_clean_search_query():
    # E-commerce query with budget and conversational padding
    res = clean_search_query("can you browse eBay for me for $300 watches")
    assert res["platform"] == "ebay"
    assert "watches" in res["keywords"]
    assert res["max_price"] == 300
    assert "_udhi=300" in res["target_url"]

    # Wikipedia inquiry
    wiki_res = clean_search_query("browse Wikipedia for Quantum Computing")
    assert wiki_res["platform"] == "wikipedia"
    assert "Quantum Computing" in wiki_res["keywords"]
    assert "wikipedia.org" in wiki_res["target_url"]

    # Conversational speech preamble and speech-to-text typo
    voice_res = clean_search_query("okay opening browser is nice but can you please go and proud some latest news about AI")
    assert "opening browser is nice" not in voice_res["keywords"]
    assert "can you please" not in voice_res["keywords"]
    assert "latest news AI" in voice_res["keywords"] or "AI" in voice_res["keywords"]

    # Complex conversational STT with filler, budget disclaimer, and brand acoustic error
    complex_res = clean_search_query("do one thing a watches which are professional I don t have a target budget more priority the brand can be seafood")
    assert complex_res["platform"] == "ebay"
    assert "Seiko" in complex_res["keywords"]
    assert "watches" in complex_res["keywords"]
    assert "do one thing" not in complex_res["keywords"].lower()
    assert "target budget" not in complex_res["keywords"].lower()
    assert "more priority" not in complex_res["keywords"].lower()
    assert complex_res["max_price"] is None
    assert complex_res["target_url"] == "https://www.ebay.com/sch/i.html?_nkw=Seiko%20watches%20professional"


def test_clean_llm_response():
    sample = "<thought>Thinking about watches</thought>```json\n{\"action\": \"navigate\"}\n```"
    cleaned = clean_llm_response(sample)
    assert "<thought>" not in cleaned
    obj = extract_json_object(sample)
    assert obj == {"action": "navigate"}
