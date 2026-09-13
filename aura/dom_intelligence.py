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
