"""
AURA Relay Browser Manager - Playwright browser control with persistence

CRITICAL ARCHITECTURAL FIX:
This version implements ensure_launched() to check if the browser is still alive.
If it is, it reuses it. If it crashed, it restarts it without losing session data.
This enables true persistent browser sessions across multiple tasks.
"""
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import json

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class BrowserManager:
    """
    Manages Playwright browser lifecycle with persistent session support.

    Features:
    - Persistent user data directory for session storage
    - Automatic screenshot capture
    - Headful/headless fallback
    - Navigation and interaction helpers
    - CRITICAL: ensure_launched() checks if browser is still alive before reusing
    """

    def __init__(self, headless: bool = False, slow_mo: int = 250,
                 screenshot_dir: str = "runtime/screenshots",
                 user_data_dir: Optional[str] = None):
        self.headless = headless
        self.slow_mo = slow_mo
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        # Persistent session directory
        if user_data_dir:
            self.user_data_dir = Path(user_data_dir)
        else:
            self.user_data_dir = Path("runtime/browser_session")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._launched = False

    async def ensure_launched(self) -> None:
        """
        Ensure the browser is launched and context is valid.
        
        CRITICAL FIX: Checks if the browser/context is still alive.
        If the context is closed or invalid, it relaunches while preserving session data.
        This prevents crashes when trying to reuse a dead browser.
        """
        if self._launched:
            try:
                # Test if context is still alive by accessing pages
                _ = self._context.pages
                # Context is valid, no need to relaunch
                return
            except Exception:
                # Context closed or invalid, need to relaunch
                print("Browser context detected as closed, relaunching...")
                self._launched = False
                if self._playwright:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None

        # Launch fresh browser with persistent session
        self._playwright = await async_playwright().start()

        # Browser launch arguments for better compatibility
        browser_args = [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor",
            "--disable-blink-features=AutomationControlled",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]

        # Try to launch with display first, fallback to headless
        try:
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                slow_mo=self.slow_mo,
                args=browser_args,
                viewport={"width": 1280, "height": 720},
                accept_downloads=True,
                ignore_https_errors=True
            )
            # For persistent context, the context IS the browser
            self._context = self._browser
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        except Exception as e:
            # If headful fails, try headless
            if not self.headless:
                print(f"Headful launch failed: {e}, falling back to headless")
                self.headless = True
                try:
                    self._browser = await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.user_data_dir),
                        headless=True,
                        slow_mo=self.slow_mo,
                        args=browser_args[:5],  # Fewer args for headless
                        viewport={"width": 1280, "height": 720},
                        accept_downloads=True,
                        ignore_https_errors=True
                    )
                    self._context = self._browser
                    self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                except Exception as e2:
                    # Last resort: non-persistent headless
                    print(f"Persistent launch failed: {e2}, using non-persistent")
                    browser = await self._playwright.chromium.launch(
                        headless=True,
                        slow_mo=self.slow_mo,
                        args=browser_args[:5]
                    )
                    self._context = await browser.new_context(
                        viewport={"width": 1280, "height": 720}
                    )
                    self._page = await self._context.new_page()
                    self._browser = browser  # Store browser for cleanup
            else:
                raise

        self._launched = True
        print(f"Browser launched successfully (headless={self.headless})")

    async def launch(self, reuse_existing: bool = True) -> None:
        """Legacy launch method - delegates to ensure_launched."""
        await self.ensure_launched()

    async def close(self, keep_session: bool = True) -> None:
        """
        Close the browser.

        Args:
            keep_session: If True, preserve user data for next run
        """
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser and hasattr(self._browser, 'close'):
            try:
                await self._browser.close()
            except Exception:
                pass

        self._browser = None
        self._page = None

        if not keep_session:
            # Clean up session data
            try:
                import shutil
                shutil.rmtree(self.user_data_dir, ignore_errors=True)
            except Exception:
                pass

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._launched = False

    @property
    def page(self) -> Page:
        """Get the current page."""
        if self._page is None:
            raise RuntimeError("Browser not launched. Call ensure_launched() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Get the current browser context."""
        if self._context is None:
            raise RuntimeError("Browser not launched. Call ensure_launched() first.")
        return self._context

    async def navigate(self, url: str, wait_until: str = "networkidle", timeout: float = 30000) -> None:
        """Navigate to a URL with configurable wait strategy."""
        await self._page.goto(url, wait_until=wait_until, timeout=timeout)

    async def screenshot(self, name: str, step: str = "", full_page: bool = False) -> str:
        """
        Take a screenshot and save it.

        Returns the filename of the saved screenshot.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in name if c.isalnum() or c in "_-")[:50]
        filename = f"{timestamp}_{safe_name}.png"
        filepath = self.screenshot_dir / filename

        try:
            await self._page.screenshot(path=str(filepath), full_page=full_page)
            return filename
        except Exception as e:
            print(f"Screenshot failed: {e}")
            return ""

    async def get_content(self) -> str:
        """Get the page HTML content."""
        return await self._page.content()

    async def get_title(self) -> str:
        """Get the page title."""
        return await self._page.title()

    async def get_url(self) -> str:
        """Get the current URL."""
        return self._page.url

    async def wait_for_timeout(self, ms: float) -> None:
        """Wait for specified milliseconds."""
        await self._page.wait_for_timeout(ms)

    async def wait_for_selector(self, selector: str, timeout: float = 5000, state: str = "visible") -> bool:
        """Wait for an element to appear."""
        try:
            await self._page.wait_for_selector(selector, timeout=timeout, state=state)
            return True
        except Exception:
            return False

    async def reload(self) -> None:
        """Reload the current page."""
        await self._page.reload()

    async def go_back(self) -> None:
        """Go back in history."""
        await self._page.go_back()

    async def go_forward(self) -> None:
        """Go forward in history."""
        await self._page.go_forward()

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        """Execute JavaScript in the page context."""
        return await self._page.evaluate(script, arg)

    async def save_session(self) -> Dict[str, Any]:
        """Save current session state."""
        session_data = {
            "url": await self.get_url(),
            "title": await self.get_title(),
            "cookies": await self._context.cookies(),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        session_file = self.user_data_dir / "session.json"
        with open(session_file, "w") as f:
            json.dump(session_data, f, indent=2)

        return session_data

    async def restore_session(self) -> bool:
        """Restore session from saved state."""
        session_file = self.user_data_dir / "session.json"
        if not session_file.exists():
            return False

        try:
            with open(session_file, "r") as f:
                session_data = json.load(f)

            if "url" in session_data:
                await self.navigate(session_data["url"])
                return True
        except Exception as e:
            print(f"Session restore failed: {e}")

        return False

    async def clear_session(self) -> None:
        """Clear all session data."""
        await self._context.clear_cookies()
        await self._context.clear_permissions()
