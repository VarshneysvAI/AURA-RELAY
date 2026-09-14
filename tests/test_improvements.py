"""
Unit tests for search loop fixes, CAPTCHA detection, press_key actions, and live streaming.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aura.dom_intelligence import DomIntelligence
from aura.query_cleaner import build_search_url, clean_search_query
from aura.llm_provider import extract_json_object


def test_build_search_url_google():
    url = build_search_url("google", "what is AI definition")
    assert "google.com/search?q=what%20is%20AI%20definition" in url


def test_build_search_url_ebay():
    url = build_search_url("ebay", "Seiko watches", 300)
    assert "ebay.com/sch/i.html?_nkw=Seiko%20watches&_udhi=300" in url


def test_extract_json_with_press_key():
    raw_llm = '{"action": "press_key", "value": "Enter", "reasoning": "Submit search", "confidence": 0.9}'
    obj = extract_json_object(raw_llm)
    assert obj is not None
    assert obj["action"] == "press_key"
    assert obj["value"] == "Enter"


import asyncio

def test_detect_captcha_signatures():
    async def _run():
        mock_page = MagicMock()
        mock_page.url = "https://www.google.com/sorry/index?continue=..."
        mock_page.evaluate = AsyncMock(return_value="Our systems have detected unusual traffic from your computer network. Please solve this CAPTCHA to continue.")
        mock_page.locator = MagicMock()
        
        dom = DomIntelligence(mock_page)
        res = await dom.detect_captcha()
        assert res["detected"] is True
        assert "unusual traffic" in res["type"] or "google_sorry_captcha" in res["type"]
    asyncio.run(_run())


def test_detect_no_captcha_on_normal_page():
    async def _run():
        mock_page = MagicMock()
        mock_page.url = "https://www.google.com/search?q=artificial+intelligence"
        mock_page.evaluate = AsyncMock(return_value="Artificial intelligence is the intelligence of machines or software...")
        
        dom = DomIntelligence(mock_page)
        dom.check_element_exists = AsyncMock(return_value=False)
        res = await dom.detect_captcha()
        assert res["detected"] is False
    asyncio.run(_run())


def test_find_input_field_with_css_selector():
    async def _run():
        mock_page = MagicMock()
        mock_locator = MagicMock()
        mock_locator.first = mock_locator
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_page.locator = MagicMock(return_value=mock_locator)

        dom = DomIntelligence(mock_page)
        loc = await dom.find_input_field("textarea#ti6dpd")
        assert loc is not None
        mock_page.locator.assert_called_with("textarea#ti6dpd")
    asyncio.run(_run())


def test_browser_manager_screenshot_bytes_method():
    from aura.browser_manager import BrowserManager
    bm = BrowserManager()
    assert hasattr(bm, "screenshot_bytes")
    assert callable(bm.screenshot_bytes)


def test_intent_classification():
    from aura.query_cleaner import is_stop_intent, is_conversational_closure, is_conversational_inquiry

    # Stop intents
    assert is_stop_intent("stop") is True
    assert is_stop_intent("stop please") is True
    assert is_stop_intent("please stop") is True
    assert is_stop_intent("cancel") is True
    assert is_stop_intent("cancel task") is True
    assert is_stop_intent("stop it") is True
    assert is_stop_intent("stop that") is True
    assert is_stop_intent("halt") is True
    assert is_stop_intent("abort") is True
    assert is_stop_intent("browse google for latest news") is False
    assert is_stop_intent("find watches on ebay under 200") is False

    # Conversational closure
    assert is_conversational_closure("no thank you") is True
    assert is_conversational_closure("no thanks") is True
    assert is_conversational_closure("no, thank you") is True
    assert is_conversational_closure("that's all") is True
    assert is_conversational_closure("nothing else") is True
    assert is_conversational_closure("i'm good") is True
    assert is_conversational_closure("thanks") is True
    assert is_conversational_closure("thank you") is True
    assert is_conversational_closure("goodbye") is True
    assert is_conversational_closure("bye") is True
    assert is_conversational_closure("browse google for latest news") is False
    assert is_conversational_closure("search for watches") is False

    # Inquiries
    assert is_conversational_inquiry("hello") is True
    assert is_conversational_inquiry("what can you do") is True
    assert is_conversational_inquiry("who are you") is True
    assert is_conversational_inquiry("describe your work") is True
    assert is_conversational_inquiry("search ebay for shoes") is False


def test_chat_endpoint_stop_and_closure():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # 1. Test conversational closure
    res_closure = client.post("/api/chat", json={"message": "no thank you"})
    assert res_closure.status_code == 200
    data_c = res_closure.json()
    assert data_c["type"] == "chat_reply"
    assert data_c["action"] == "closure"
    assert "welcome" in data_c["reply"].lower() or "problem" in data_c["reply"].lower()

    # 2. Test stop intent
    res_stop = client.post("/api/chat", json={"message": "stop"})
    assert res_stop.status_code == 200
    data_s = res_stop.json()
    assert data_s["type"] == "chat_reply"
    assert data_s["action"] == "stopped"

