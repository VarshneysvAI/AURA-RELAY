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
        """Find an input or textarea field by direct CSS selector, label, or placeholder."""
        raw_target = (label_or_placeholder or "").strip()
        target = raw_target.lower()
        
        # Strategy 0: If target looks like a valid CSS selector, try directly first
        if any(c in raw_target for c in ["#", ".", "[", ">", ":"]) or raw_target in ("textarea", "input"):
            try:
                direct_loc = self.page.locator(raw_target).first
                if await direct_loc.is_visible(timeout=1200):
                    return direct_loc
            except Exception:
                pass

        # Try various input/textarea strategies
        strategies = [
            (f'input[placeholder*="{target}" i], textarea[placeholder*="{target}" i]', "placeholder"),
            (f'input[aria-label*="{target}" i], textarea[aria-label*="{target}" i]', "aria-label"),
            (f'input[data-testid*="{target}" i], textarea[data-testid*="{target}" i]', "data-testid"),
            (f'input[name*="{target}" i], textarea[name*="{target}" i]', "name attribute"),
            (f'input[id*="{target}" i], textarea[id*="{target}" i]', "id attribute"),
            (f'label:has-text("{target}") + input, label:has-text("{target}") + textarea', "label+input"),
            (f'//label[contains(text(), "{target}")]/following-sibling::input | //label[contains(text(), "{target}")]/following-sibling::textarea', "xpath label"),
            (f'input[type="search"], textarea, input[type="text"]', "generic fallback search input"),
        ]
        
        for selector, strategy in strategies:
            try:
                locator = self.page.locator(selector).first
                await locator.wait_for(state="visible", timeout=1200)
                return locator
            except Exception:
                continue
        
        return None
    
    async def fill_input(self, label: str, value: str) -> bool:
        """Fill an input field or textarea intelligently."""
        input_field = await self.find_input_field(label)
        
        if input_field is not None:
            try:
                await input_field.fill(value)
                return True
            except Exception:
                try:
                    await input_field.click()
                    await self.page.keyboard.type(value)
                    return True
                except Exception:
                    pass
        
        # Direct fallback on raw label if it was a CSS selector
        if any(c in label for c in ["#", ".", "[", ">", ":"]):
            try:
                await self.page.fill(label, value, timeout=2000)
                return True
            except Exception:
                pass

        return False
    
    async def detect_captcha(self) -> Dict[str, Any]:
        """
        Detects Google 'unusual traffic', reCAPTCHA, Cloudflare Turnstile/Challenge,
        or 'I am not a robot' checkpoints.
        """
        try:
            url = self.page.url.lower()
            text = (await self.page.evaluate("() => (document.body?.innerText || '').slice(0, 3000)")).lower()
            
            captcha_signatures = [
                "i'm not a robot",
                "i am not a robot",
                "unusual traffic from your computer network",
                "verify you are human",
                "checking your browser",
                "attention required! | cloudflare",
                "just a moment...",
                "please solve the challenge below",
                "security verification",
                "bot detection",
            ]
            for sig in captcha_signatures:
                if sig in text:
                    return {"detected": True, "type": sig, "url": self.page.url}

            iframe_signatures = [
                'iframe[src*="recaptcha"]',
                'iframe[title*="recaptcha" i]',
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                '.g-recaptcha',
                '#cf-turnstile',
                '#captcha-form',
            ]
            for sel in iframe_signatures:
                if await self.check_element_exists(sel, timeout=300):
                    return {"detected": True, "type": sel, "url": self.page.url}
            
            if "google.com/sorry/index" in url:
                return {"detected": True, "type": "google_sorry_captcha", "url": self.page.url}
        except Exception:
            pass
        return {"detected": False, "type": "", "url": ""}

    async def try_click_captcha_checkbox(self) -> bool:
        """Attempt to click interactive 'I'm not a robot' or Cloudflare checkboxes."""
        try:
            for frame in self.page.frames:
                for selector in [
                    '.recaptcha-checkbox-border',
                    '#recaptcha-anchor',
                    'input[type="checkbox"]',
                    '[role="checkbox"]',
                    '.cb-lb',
                ]:
                    try:
                        loc = frame.locator(selector).first
                        if await loc.is_visible(timeout=500):
                            await loc.click(timeout=1500)
                            await self.page.wait_for_timeout(1000)
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
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
                print(f"DEBUG: safe_click target='{target}', element_type='{element_type}'")
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
            
            # Inject JavaScript to draw a glowing red box with auto-cleanup after 800ms
            await element.evaluate("""
                (el) => {
                    // Store original styles for restoration
                    el.setAttribute('data-original-outline', el.style.outline || '');
                    el.setAttribute('data-original-shadow', el.style.boxShadow || '');
                    
                    // Apply Iron Man vision styles
                    el.style.outline = '4px solid #FF0000';
                    el.style.outlineOffset = '3px';
                    el.style.boxShadow = '0 0 25px rgba(255, 0, 0, 0.9), 0 0 10px rgba(255, 0, 0, 0.7)';
                    el.style.transition = 'all 0.2s ease-in-out';
                    el.style.zIndex = '9999';
                    el.style.position = 'relative';

                    // Auto-cleanup after 800ms to eliminate permanent "Iron Man" stains
                    setTimeout(() => {
                        try {
                            el.style.outline = el.getAttribute('data-original-outline') || '';
                            el.style.boxShadow = el.getAttribute('data-original-shadow') || '';
                            el.removeAttribute('data-original-outline');
                            el.removeAttribute('data-original-shadow');
                        } catch (e) {}
                    }, 800);
                }
            """)
            
            # Brief pause so the highlight is visible (for screenshots/demo)
            await self.page.wait_for_timeout(350)
            return True
            
        except Exception as e:
            # If highlighting fails, continue without it
            print(f"Highlight failed: {e}")
            return False

    async def click_best_candidate(self, selectors: List[str], max_retries: int = 3) -> bool:
        """
        Try multiple selectors in order of preference until one succeeds.
        Uses safe_click for each attempt to get retry logic and Iron Man vision.
        """
        import re
        for selector in selectors:
            # Extract clean text from selectors:
            # Handles formats like "button:has-text('Sign in')", "text=Download", "'Sign in'"
            has_text_match = re.search(r":has-text\((['\"]?)(.*?)\1\)", selector)
            if has_text_match:
                target_text = has_text_match.group(2)
            else:
                match = re.search(r"text[=:](['\"]?)([^'\"]+)\1", selector)
                if match:
                    target_text = match.group(2)
                elif selector.startswith('"') or selector.startswith("'"):
                    target_text = selector.strip("\"'")
                else:
                    target_text = selector
            
            # Try safe_click with the extracted text
            if await self.safe_click(target_text, "button", max_retries=max_retries):
                return True
                
        return False
    
    async def get_page_hash(self) -> str:
        """Get a stable semantic hash representing current page state for circuit breaker."""
        try:
            import urllib.parse
            import hashlib
            title = (await self.page.title()) or ""
            raw_url = self.page.url or ""
            parsed_url = urllib.parse.urlparse(raw_url)
            norm_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
            
            # Stable content text: inspect primary headers & body text without length volatility
            text_snippet = await self.page.evaluate("""
                () => {
                    const h = document.querySelector('h1, h2')?.textContent || '';
                    const main = document.querySelector('main, #content, article')?.textContent || document.body?.textContent || '';
                    return (h + ' ' + main).replace(/\\s+/g, ' ').trim().slice(0, 300);
                }
            """)
            raw_key = f"{title}:{norm_url}:{text_snippet}"
            return hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:16]
        except Exception:
            return "unknown"

    async def extract_listings(self) -> List[Dict[str, Any]]:
        """
        Intelligently extracts product listings, prices, titles, ratings, and links
        from the current page (YouTube, eBay, Amazon, Google, web stores).
        """
        try:
            listings = await self.page.evaluate("""
                () => {
                    const results = [];
                    const host = window.location.hostname || '';

                    // 1. YouTube Video Results
                    if (host.includes('youtube.com')) {
                        const ytItems = document.querySelectorAll('ytd-video-renderer, ytd-rich-item-renderer, ytd-grid-video-renderer, ytd-compact-video-renderer');
                        for (const item of ytItems) {
                            try {
                                const titleEl = item.querySelector('#video-title, #video-title-link, a#thumbnail');
                                const linkEl = item.querySelector('a#video-title, a#thumbnail, a[href*="/watch"]');
                                const metaEls = item.querySelectorAll('#metadata-line span, span.badge-shape-wiz__text');
                                const channelEl = item.querySelector('#channel-name, ytd-channel-name');
                                
                                const rawTitle = titleEl ? (titleEl.innerText || titleEl.getAttribute('title') || '') : '';
                                const title = rawTitle.split('\\n')[0].trim();
                                const url = linkEl ? linkEl.href : '';
                                const meta = Array.from(metaEls).map(e => e.innerText.trim()).filter(Boolean).join(' • ');
                                const channel = channelEl ? channelEl.innerText.trim() : '';
                                
                                if (title && url && url.includes('/watch') && !results.some(r => r.url === url)) {
                                    results.push({
                                        title: title,
                                        price: channel ? `${channel}${meta ? ' • ' + meta : ''}` : (meta || 'YouTube Video'),
                                        url: url,
                                        source: 'YouTube'
                                    });
                                }
                                if (results.length >= 8) break;
                            } catch (e) {}
                        }
                    }
                    
                    // 2. eBay selectors
                    if (results.length === 0) {
                        const ebayItems = document.querySelectorAll('li.s-item, .s-card, .s-item__wrapper');
                        if (ebayItems.length > 0) {
                            for (const item of ebayItems) {
                                try {
                                    const titleEl = item.querySelector('.s-item__title, .s-item__title span, [role="heading"]');
                                    const priceEl = item.querySelector('.s-item__price');
                                    const linkEl = item.querySelector('a.s-item__link, a[href*="/itm/"]');
                                    const shippingEl = item.querySelector('.s-item__shipping, .s-item__logisticsCost');
                                    
                                    const title = titleEl ? titleEl.innerText.trim() : '';
                                    const price = priceEl ? priceEl.innerText.trim() : '';
                                    const url = linkEl ? linkEl.href : '';
                                    const shipping = shippingEl ? shippingEl.innerText.trim() : '';
                                    
                                    if (title && !title.toLowerCase().includes('shop on ebay') && price) {
                                        results.push({
                                            title: title,
                                            price: price,
                                            url: url,
                                            shipping: shipping,
                                            source: 'eBay'
                                        });
                                    }
                                    if (results.length >= 8) break;
                                } catch (e) {}
                            }
                        }
                    }
                    
                    // 3. Amazon selectors (if eBay yielded nothing)
                    if (results.length === 0) {
                        const amzItems = document.querySelectorAll('div[data-component-type="s-search-result"]');
                        for (const item of amzItems) {
                            try {
                                const titleEl = item.querySelector('h2 a span, h2 span, [data-cy="title-recipe"] h2');
                                const priceEl = item.querySelector('.a-price .a-offscreen, .a-price');
                                const linkEl = item.querySelector('h2 a');
                                
                                const title = titleEl ? titleEl.innerText.trim() : '';
                                const price = priceEl ? priceEl.innerText.trim() : '';
                                const url = linkEl ? linkEl.href : '';
                                
                                if (title && price) {
                                    results.push({
                                        title: title,
                                        price: price,
                                        url: url,
                                        source: 'Amazon'
                                    });
                                }
                                if (results.length >= 8) break;
                            } catch (e) {}
                        }
                    }

                    // 4. Search engine results (Google, Bing, DuckDuckGo)
                    if (results.length === 0 && (host.includes('google.') || host.includes('bing.') || host.includes('duckduckgo.'))) {
                        const searchItems = document.querySelectorAll('div.g, div[data-sokoban-container], li.b_algo');
                        for (const item of searchItems) {
                            try {
                                const titleEl = item.querySelector('h3');
                                const linkEl = item.querySelector('a[href^="http"]');
                                const snippetEl = item.querySelector('div[style*="-webkit-line-clamp"], .VwiC3b, .b_caption p');
                                const title = titleEl ? titleEl.innerText.trim() : '';
                                const url = linkEl ? linkEl.href : '';
                                const snippet = snippetEl ? snippetEl.innerText.trim() : '';
                                if (title && url && !url.includes('google.com/search') && !results.some(r => r.url === url)) {
                                    results.push({
                                        title: title,
                                        price: snippet ? snippet.slice(0, 80) : 'Web Result',
                                        url: url,
                                        source: 'Search'
                                    });
                                }
                                if (results.length >= 8) break;
                            } catch (e) {}
                        }
                    }
                    
                    // 5. Generic fallback for shopping/catalog pages
                    if (results.length === 0) {
                        const cards = document.querySelectorAll('[class*="product"], [class*="item"], [class*="card"]');
                        for (const card of cards) {
                            try {
                                const text = card.innerText || '';
                                const priceMatch = text.match(/\\$[0-9]+(?:\\.[0-9]{2})?/);
                                if (priceMatch) {
                                    const link = card.querySelector('a[href]');
                                    const heading = card.querySelector('h2, h3, h4, [class*="title"]') || card;
                                    const titleText = heading ? (heading.innerText || '').split('\\n')[0].trim() : '';
                                    if (titleText && titleText.length > 5 && titleText.length < 120) {
                                        results.push({
                                            title: titleText,
                                            price: priceMatch[0],
                                            url: link ? link.href : '',
                                            source: 'Web Store'
                                        });
                                    }
                                }
                                if (results.length >= 8) break;
                            } catch (e) {}
                        }
                    }
                    
                    return results;
                }
            """)
            return listings or []
        except Exception as e:
            print(f"Error extracting listings: {e}")
            return []

