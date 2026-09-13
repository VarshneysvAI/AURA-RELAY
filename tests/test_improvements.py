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
