"""
AURA Relay DOM Intelligence - Smart element location and self-healing
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from playwright.async_api import Page, Locator


@dataclass
class CandidateLocator:
    """Represents a candidate element locator with reliability score."""
    selector: str
    strategy: str
    description: str
    score: float
    locator: Optional[Locator] = None


class DomIntelligence:
    """
    Smart DOM element location with self-healing capabilities.
    
    Locator strategy order (most reliable first):
    1. data-testid
    2. aria-label
    3. accessible role + name
    4. label text
    5. placeholder
    6. visible text
    7. nearby heading or section context
    8. fuzzy candidate ranking
    """
    
    def __init__(self, page: Page):
        self.page = page
        self._max_retries = 3
        self._retry_delay = 0.5
    
    def _build_candidates(self, target: str, element_type: str = "button") -> List[CandidateLocator]:
        """Build multiple candidate locators for a target."""
        candidates = []
        target_lower = target.lower().strip()
        
        # Strategy 1: data-testid
        testid_selector = f'[data-testid="{target_lower}"]'
        candidates.append(CandidateLocator(
            selector=testid_selector,
            strategy="data-testid",
            description=f"data-testid={target_lower}",
            score=0.95
        ))
        
        # Strategy 2: aria-label
        aria_selector = f'[aria-label*="{target_lower}" i]'
        candidates.append(CandidateLocator(
            selector=aria_selector,
            strategy="aria-label",
            description=f"aria-label contains '{target_lower}'",
            score=0.90
        ))
        
        # Strategy 3: role + name (for buttons, links, etc.)
        if element_type in ("button", "link", "heading"):
            role_selector = f'{element_type}[name*="{target_lower}" i]'
            candidates.append(CandidateLocator(
                selector=role_selector,
                strategy="role+name",
                description=f"{element_type} with name containing '{target_lower}'",
                score=0.85
            ))
        
        # Strategy 4: Button/link with exact text
        if element_type == "button":
            button_text_selector = f'button:has-text("{target_lower}")'
            candidates.append(CandidateLocator(
                selector=button_text_selector,
                strategy="text",
                description=f"button with text '{target_lower}'",
                score=0.80
            ))
            
            input_button_selector = f'input[type="submit"][value*="{target_lower}" i]'
            candidates.append(CandidateLocator(
                selector=input_button_selector,
                strategy="input[value]",
                description=f"input submit with value containing '{target_lower}'",
                score=0.75
            ))
        
        # Strategy 5: Link with text
        if element_type == "link":
            link_selector = f'a:has-text("{target_lower}")'
            candidates.append(CandidateLocator(
                selector=link_selector,
                strategy="link-text",
                description=f"link with text '{target_lower}'",
                score=0.75
            ))
        
        # Strategy 6: Input by placeholder
        if element_type in ("input", "textbox"):
            placeholder_selector = f'input[placeholder*="{target_lower}" i]'
            candidates.append(CandidateLocator(
                selector=placeholder_selector,
                strategy="placeholder",
                description=f"input with placeholder containing '{target_lower}'",
                score=0.70
            ))
        
        # Strategy 7: Generic text search
        text_selector = f':has-text("{target_lower}")'
        candidates.append(CandidateLocator(
            selector=text_selector,
            strategy="text-content",
            description=f"element containing text '{target_lower}'",
            score=0.50
        ))
        
        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)
        
        return candidates
    
    async def find_element(self, target: str, element_type: str = "button", 
                          timeout: float = 5000) -> Optional[Locator]:
        """
        Find an element using intelligent multi-strategy approach.
        
        Args:
            target: The target identifier (text, label, testid, etc.)
            element_type: Type of element to look for
            timeout: Timeout in milliseconds
        
        Returns:
            Playwright Locator or None if not found
        """
        candidates = self._build_candidates(target, element_type)
        
        for attempt in range(self._max_retries):
            for candidate in candidates:
                try:
                    locator = self.page.locator(candidate.selector).first
                    # Check if visible within timeout
                    await locator.wait_for(state="visible", timeout=timeout)
                    candidate.locator = locator
                    return locator
                except Exception:
                    continue
            
            # If all candidates failed, take a snapshot and retry
            if attempt < self._max_retries - 1:
                await self.page.wait_for_timeout(self._retry_delay * 1000 * (attempt + 1))
        
        return None
    
    async def find_input_field(self, label_or_placeholder: str) -> Optional[Locator]:
        """Find an input field by label or placeholder."""
        candidates = []
        target = label_or_placeholder.lower().strip()
        
        # Try various strategies
        strategies = [
            (f'input[placeholder*="{target}" i]', "placeholder"),
            (f'input[aria-label*="{target}" i]', "aria-label"),
            (f'input[data-testid="{target}"]', "data-testid"),
            (f'input[name*="{target}" i]', "name attribute"),
            (f'input[id*="{target}" i]', "id attribute"),
            (f'label:has-text("{target}") + input', "label+input"),
            (f'//label[contains(text(), "{target}")]/following-sibling::input', "xpath label"),
        ]
        
        for selector, strategy in strategies:
            try:
                locator = self.page.locator(selector).first
                await locator.wait_for(state="visible", timeout=3000)
                return locator
            except Exception:
                continue
        
        return None
    
    async def fill_input(self, label: str, value: str) -> bool:
        """Fill an input field intelligently."""
        input_field = await self.find_input_field(label)
        
        if input_field is None:
            return False
        
        try:
            await input_field.fill(value)
            return True
        except Exception:
            return False
    
    async def click_element(self, target: str, element_type: str = "button") -> bool:
        """Click an element intelligently."""
        element = await self.find_element(target, element_type)
        
        if element is None:
            return False
        
        try:
            await element.click()
            return True
        except Exception:
            return False
    
    async def get_page_text(self) -> str:
        """Get the visible text content of the page."""
        try:
            body = await self.page.locator("body").text_content()
            return body or ""
        except Exception:
            return ""
    
    async def check_element_exists(self, selector: str, timeout: float = 1000) -> bool:
        """Check if an element exists on the page."""
        try:
            locator = self.page.locator(selector).first
            await locator.wait_for(state="attached", timeout=timeout)
            return True
        except Exception:
            return False
    
    async def wait_for_text(self, text: str, timeout: float = 5000) -> bool:
        """Wait for specific text to appear on the page."""
        try:
            locator = self.page.locator(f':has-text("{text}")').first
            await locator.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False
    
    async def dismiss_overlays(self) -> bool:
        """
        Silent Killer #1: Modal Buster
        Auto-dismiss cookie banners, consent popups, and newsletter modals.
        """
        overlay_keywords = [
            "Accept", "I agree", "Got it", "Close", "Dismiss", 
            "Allow", "Consent", "Continue", "OK", "Yes"
        ]
        
        dismissed = False
        for keyword in overlay_keywords:
            try:
                # Look for buttons containing these words
                locator = self.page.locator(
                    f"button:has-text('{keyword}'), "
                    f"[role='button']:has-text('{keyword}'), "
                    f"a:has-text('{keyword}'), "
                    f"input[type='button'][value*='{keyword}']"
                ).first
                
                # Only click if visible and enabled
                if await locator.is_visible(timeout=800):
                    is_enabled = await locator.is_enabled()
                    if is_enabled:
                        await locator.click(timeout=2000)
                        await self.page.wait_for_timeout(500)  # Wait for animation
                        dismissed = True
            except Exception:
                pass  # Not found or not clickable, which is fine
        
        return dismissed
    
    async def safe_click(self, target: str, element_type: str = "button", 
                         max_retries: int = 5) -> bool:
        """
        Silent Killer #2: Stale Element Protection
        Safely click an element with auto-wait, retry logic, and overlay dismissal.
        """
        # First dismiss any overlays
        await self.dismiss_overlays()
        
        for attempt in range(max_retries):
            try:
                # Use Playwright's role-based locator with auto-wait
                locator = self.page.get_by_role(element_type, name=target)
                
                # Wait for element to be actionable
                await locator.wait_for(state="attached", timeout=3000)
                await locator.wait_for(state="visible", timeout=3000)
                await locator.wait_for(state="enabled", timeout=3000)
                
                # Highlight before clicking (Iron Man Vision)
                await self.highlight_element(target, element_type)
                
                # Click with force option if needed
                await locator.click(timeout=3000, force=False)
                return True
                
            except Exception as e:
                error_msg = str(e).lower()
                
                # If element is stale/intercepted, retry
                if any(kw in error_msg for kw in ["stale", "intercepted", "attached", "visible"]):
                    await self.page.wait_for_timeout(500 * (attempt + 1))  # Exponential backoff
                    await self.dismiss_overlays()  # Try dismissing again
                    continue
                else:
                    # Different error, log and retry once more
                    print(f"Click attempt {attempt + 1} failed: {e}")
                    await self.page.wait_for_timeout(500)
        
        return False
    
    async def highlight_element(self, target: str, element_type: str = "button") -> bool:
        """
        WOW FACTOR: Iron Man Vision
        Draws a glowing red bounding box around the target element.
        """
        try:
            # Find the element
            locator = self.page.get_by_role(element_type, name=target)
            
            # Get the first matching element
            element = locator.first
            
            # Check if element exists and is visible
            if not await element.is_visible():
                return False
            
            # Inject JavaScript to draw a glowing red box
            await element.evaluate("""
                (el) => {
                    // Store original styles for restoration if needed
                    el.setAttribute('data-original-outline', el.style.outline || '');
                    el.setAttribute('data-original-shadow', el.style.boxShadow || '');
                    
                    // Apply Iron Man vision styles
                    el.style.outline = '4px solid #FF0000';
                    el.style.outlineOffset = '3px';
                    el.style.boxShadow = '0 0 25px rgba(255, 0, 0, 0.9), 0 0 10px rgba(255, 0, 0, 0.7)';
                    el.style.transition = 'all 0.2s ease-in-out';
                    el.style.zIndex = '9999';
                    el.style.position = 'relative';
                }
            """)
            
            # Brief pause so the highlight is visible (for screenshots/demo)
            await self.page.wait_for_timeout(400)
            
            return True
            
        except Exception as e:
            # If highlighting fails, continue without it
            print(f"Highlight failed: {e}")
            return False
    
    async def get_page_hash(self) -> str:
        """Get a hash representing current page state for circuit breaker."""
        try:
            title = await self.page.title()
            url = self.page.url
            content = await self.page.content()
            # Simple hash based on title, URL, and content length
            return f"{title}:{url}:{len(content)}"
        except Exception:
            return "unknown"
