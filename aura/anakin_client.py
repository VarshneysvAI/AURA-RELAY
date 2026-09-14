"""
AURA Relay — Anakin.io Async Client
Provides web search, anti-block URL scraping, and research intelligence.
Includes caching to conserve user API credits.
"""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from aura.config import get_config

logger = logging.getLogger("aura.anakin")

CACHE_DIR = Path("runtime/cache/anakin")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class AnakinClient:
    """Asynchronous client for the Anakin.io REST API."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        cfg = get_config()
        self.api_key = api_key or cfg.anakin_api_key or os.getenv("ANAKIN_API_KEY")
        self.base_url = (base_url or cfg.anakin_base_url or "https://api.anakin.io/v1").rstrip("/")
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def is_configured(self) -> bool:
        """Check if Anakin API key is available."""
        return bool(self.api_key and self.api_key.startswith("ask_"))

    def _get_cache_path(self, cache_key: str) -> Path:
        h = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return CACHE_DIR / f"{h}.json"

    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]
        cache_file = self._get_cache_path(cache_key)
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                self._memory_cache[cache_key] = data
                return data
            except Exception as e:
                logger.debug(f"Failed to read Anakin cache: {e}")
        return None

    def _set_cached(self, cache_key: str, data: Dict[str, Any]):
        self._memory_cache[cache_key] = data
        try:
            cache_file = self._get_cache_path(cache_key)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to persist Anakin cache: {e}")

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "anakin-cli/0.2.0 (AURA-Relay)",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def search(self, prompt: str, limit: int = 3) -> Dict[str, Any]:
        """
        Execute an AI-powered web search via Anakin.io.
        Caches results to preserve API credits.
        """
        if not self.is_configured():
            logger.warning("Anakin API key not configured. Returning empty search results.")
            return {"results": [], "error": "Anakin API key not configured"}

        cache_key = f"search:{prompt.strip().lower()}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            logger.info(f"Anakin search cache HIT: '{prompt}'")
            return cached

        logger.info(f"Querying Anakin Search for: '{prompt}' (limit={limit})")
        url = f"{self.base_url}/search"
        payload = {"prompt": prompt, "limit": limit}

        try:
            for attempt in range(3):
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, headers=self._get_headers(), json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        self._set_cached(cache_key, data)
                        return data
                    elif resp.status_code == 401:
                        logger.error("Anakin authentication error: Invalid API key")
                        return {"results": [], "error": "Authentication failed"}
                    elif resp.status_code == 429:
                        if attempt < 2:
                            logger.warning("Anakin search rate limit reached, retrying...")
                            await asyncio.sleep(2.0)
                            continue
                        logger.warning("Anakin rate limit reached")
                        return {"results": [], "error": "Rate limit reached"}
                    else:
                        logger.error(f"Anakin Search error {resp.status_code}: {resp.text[:200]}")
                        return {"results": [], "error": f"API error: {resp.status_code}"}
        except Exception as e:
            logger.error(f"Anakin Search request exception: {e}")
            return {"results": [], "error": str(e)}

    async def scrape_url(
        self,
        target_url: str,
        use_browser: bool = False,
        generate_json: bool = False,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Scrape a single URL using Anakin's anti-block scraper.
        Polls the job until completed or timed out.
        """
        if not self.is_configured():
            return {"error": "Anakin API key not configured"}

        cache_key = f"scrape:{target_url}:{use_browser}:{generate_json}"
        cached = self._get_cached(cache_key)
        if cached:
            logger.info(f"Anakin scrape cache HIT for: {target_url}")
            return cached

        start_url = f"{self.base_url}/url-scraper"
        payload = {
            "url": target_url,
            "useBrowser": use_browser,
            "generateJson": generate_json,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                start_resp = await client.post(start_url, headers=self._get_headers(), json=payload)
                if start_resp.status_code not in (200, 201, 202):
                    logger.error(f"Failed to start scrape: {start_resp.text[:200]}")
                    return {"error": f"Start scrape failed: {start_resp.status_code}"}

                job_data = start_resp.json()
                job_id = job_data.get("jobId") or job_data.get("id")
                if not job_id:
                    return job_data

                # Poll job result
                poll_url = f"{self.base_url}/url-scraper/{job_id}"
                elapsed = 0.0
                poll_interval = 2.0

                while elapsed < timeout:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    poll_resp = await client.get(poll_url, headers=self._get_headers())
                    if poll_resp.status_code == 200:
                        poll_data = poll_resp.json()
                        status = poll_data.get("status")
                        if status == "completed":
                            self._set_cached(cache_key, poll_data)
                            return poll_data
                        elif status == "failed":
                            return {"error": poll_data.get("error", "Job failed")}

                return {"error": f"Scrape job timed out after {timeout}s"}
        except Exception as e:
            logger.error(f"Anakin Scrape exception: {e}")
            return {"error": str(e)}

    async def scrape_batch(self, urls: List[str], timeout: float = 40.0) -> Dict[str, Any]:
        """Batch scrape up to 10 URLs simultaneously."""
        if not self.is_configured() or not urls:
            return {"error": "Invalid configuration or empty URLs list"}

        batch_urls = urls[:10]
        start_url = f"{self.base_url}/url-scraper/batch"
        payload = {"urls": batch_urls}

        try:
            for attempt in range(3):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(start_url, headers=self._get_headers(), json=payload)
                    if resp.status_code in (200, 201):
                        return resp.json()
                    elif resp.status_code == 429 and attempt < 2:
                        logger.warning("Anakin scrape_batch rate limited (429). Retrying in 2s...")
                        await asyncio.sleep(2.0)
                        continue
                    return {"error": f"Batch scrape error: {resp.status_code}"}
        except Exception as e:
            logger.error(f"Batch scrape exception: {e}")
            return {"error": str(e)}


# Global instance
_anakin_client: Optional[AnakinClient] = None


def get_anakin_client() -> AnakinClient:
    global _anakin_client
    if _anakin_client is None:
        _anakin_client = AnakinClient()
    return _anakin_client
