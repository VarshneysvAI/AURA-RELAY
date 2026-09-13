"""
AURA Relay Agent - Persistent execution orchestrator

Runs on a single dedicated background thread with its own persistent event loop.
Browser and Playwright objects stay alive across tasks for true session persistence.
"""
import asyncio
import re
import threading
import urllib.parse
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from .browser_manager import BrowserManager
from .dom_intelligence import DomIntelligence
from .otp import extract_otp, mask_otp
from .anakin_client import get_anakin_client
from .llm_provider import get_llm_provider
from .query_cleaner import clean_search_query, build_search_url


class AgentExecutor:
    """
    Persistent Agent Executor.
    Runs on a dedicated background thread with its own persistent event loop.
    """
    def __init__(self, state_manager, email_provider, config, security_manager):
        self.state = state_manager
        self.email_provider = email_provider
        self.config = config
        self.security_manager = security_manager
        self.anakin = get_anakin_client()
        self.llm = get_llm_provider()
        
        # Initialize browser manager (lazy launch)
        self.browser = BrowserManager(
            headless=config.headless,
            slow_mo=config.slow_mo
        )
        self.dom = None # Initialized after browser launch
        
        self._stop_requested = False
        self._pause_requested = False
        self._last_user_answer = ""
        self._gathered_info = {}
        
        # Single persistent event loop for Playwright
        self.loop = asyncio.new_event_loop()
        def _proactor_handler(lp, ctx):
            exc = ctx.get("exception")
            if isinstance(exc, ConnectionResetError) or (isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054):
                return
            lp.default_exception_handler(ctx)
        self.loop.set_exception_handler(_proactor_handler)
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        self._current_task_future = None
        self._question_resolved = threading.Event()

    def _run_loop(self):
        """Run the persistent asyncio event loop."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def is_running(self) -> bool:
        return self._current_task_future is not None and not self._current_task_future.done()

    def start(self, instruction: str, gathered_info: Optional[dict] = None) -> bool:
        if self.is_running():
            return False
            
        self.state.start_execution(instruction)
        self._stop_requested = False
        self._pause_requested = False
        self._question_resolved.clear()
        self._gathered_info = gathered_info or {}
        
        coro = self.run(instruction, self._gathered_info)
        self._current_task_future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return True

    def stop(self):
        self._stop_requested = True
        self._pause_requested = False
        self.state.stop_execution()
        if self._current_task_future and not self._current_task_future.done():
            self._current_task_future.cancel()

    def pause(self):
        self._pause_requested = True
        self.state.set_status("paused")
        self.state.add_event("warning", "Agent paused for human takeover. Interact with browser window, then click Resume.")
        try:
            if hasattr(self, 'browser') and self.browser:
                asyncio.run_coroutine_threadsafe(self.browser.bring_to_front(), self.loop)
                asyncio.run_coroutine_threadsafe(
                    self._capture_and_record_screenshot("pause_takeover.png", "Human Takeover Active"),
                    self.loop
                )
        except Exception:
            pass

    def resume(self):
        self._pause_requested = False
        self.state.set_status("running")
        self.state.add_event("action_succeeded", "Agent resumed execution from human takeover.")
        try:
            if hasattr(self, 'browser') and self.browser:
                asyncio.run_coroutine_threadsafe(
                    self._capture_and_record_screenshot("resume_autonomous.png", "Autonomous Resumed"),
                    self.loop
                )
        except Exception:
            pass

    def submit_answer(self, answer: str, question_id: Optional[int] = None) -> bool:
        if question_id is not None:
            self.state.answer_question(answer, question_id)
        else:
            current_q_id = self.state.get_state().question_id
            if current_q_id is not None:
                self.state.answer_question(answer, current_q_id)
        
        self._last_user_answer = answer
        self.state.add_event("user_answer_received", f"User answered: {answer}")
        self._question_resolved.set()  # Signal resume
        return True

    async def run(self, instruction: str, gathered_info: Optional[dict] = None):
        self._stop_requested = False
        self._pause_requested = False
        self.state.set_status("running")
        self.state.add_event("plan_created", f"Starting task: {instruction}")
        
        try:
            # Ensure browser is launched (persistent across runs)
            await self.browser.ensure_launched()
            self.dom = DomIntelligence(self.browser.page)
            
            # Determine flow type: Portal / Sandbox or Arbitrary Web Task
            is_portal_flow = any(
                term in instruction.lower()
                for term in ["sandbox", "portal", "tax document", "statement", "2024 tax", "login"]
            ) and ("ebay" not in instruction.lower() and "amazon" not in instruction.lower() and "shop" not in instruction.lower())
            
            if is_portal_flow:
                await self._execute_secure_portal_flow(instruction)
            else:
                await self._execute_web_coworker_flow(instruction, gathered_info or {})
            
            # Finalize
            if not self._stop_requested:
                self.state.set_current_step("Completed", "Task finished successfully")
                current_result = self.state.get_state().result
                if not current_result:
                    current_result = await self.llm.summarize_task(instruction, [], gathered_info or {})
                    self.state.set_result(current_result)
                
                # Only add completed event if not already emitted
                events = self.state.get_state().events
                if not (events and events[-1].event_type == "completed"):
                    self.state.add_event("completed", current_result)
                self.state.set_status("done")
            
        except asyncio.CancelledError:
            self.state.set_status("stopped")
            self.state.add_event("warning", "Task stopped by user")
        except Exception as e:
            self.state.set_status("error")
            error_msg = str(e)
            self.state.set_error(error_msg)
            self.state.add_event("error", f"Task failed: {error_msg}")
            import traceback
            traceback.print_exc()

    async def _capture_and_record_screenshot(self, name: str, step_label: str = "") -> str:
        """Capture screenshot and register it with StateManager for live UI inspection."""
        filename = await self.browser.screenshot(name)
        if filename:
            step = step_label or self.state.get_state().current_step or "Execution"
            self.state.add_screenshot(filename, step, f"/screenshots/{filename}")
        return filename

    async def _execute_web_coworker_flow(self, instruction: str, gathered_info: dict):
        """
        Executes arbitrary web browsing / e-commerce tasks using Anakin.io intelligence
        and the NVIDIA ReAct decision loop.
        """
        # 1. Clean and optimize search query
        clean_info = clean_search_query(instruction)
        optimized_query = gathered_info.get("optimized_query")
        clean_keywords = optimized_query or gathered_info.get("clean_keywords") or clean_info["keywords"]
        clean_query = optimized_query or clean_info["anakin_query"]
        max_price = gathered_info.get("max_price") or clean_info["max_price"]

        gathered_info["optimized_query"] = clean_keywords
        gathered_info["clean_keywords"] = clean_keywords
        gathered_info["max_price"] = max_price

        # 2. Check if target URL and web intelligence were already resolved upfront
        target_url = gathered_info.get("target_url")
        search_results = None
        
        if not target_url and self.anakin.is_configured():
            self.state.set_current_step("Web Intelligence", "Consulting Anakin.io web search")
            self.state.add_event("anakin_query", f"Querying Anakin web intelligence for: '{clean_query}'")
            search_results = await self.anakin.search(clean_query, limit=3)
            
            if search_results and "results" in search_results:
                results = search_results.get("results", [])
                if results:
                    intel_summary = "\n".join(
                        [f"- {r.get('title', '')}: {r.get('snippet', '')[:140]}" for r in results[:3]]
                    )
                    self.state.add_event("anakin_intel_found", f"Discovered {len(results)} live web sources via Anakin.io")
                    gathered_info["web_intelligence"] = intel_summary
        
        # 2. Determine starting target URL:
        # If user gave explicit URL, navigate there.
        # If platform is specific store/site (eBay, Wikipedia, YouTube), go there directly.
        # If Anakin discovered live verified sources for user's research, navigate directly to top source!
        # Only fallback to search engine if no direct source was found.
        target_url = gathered_info.get("target_url")
        if not target_url:
            url_match = re.search(r"https?://[^\s]+", instruction)
            if url_match:
                target_url = url_match.group(0)
            elif search_results and search_results.get("results") and search_results["results"][0].get("url", "").startswith("http"):
                target_url = search_results["results"][0]["url"]
                self.state.add_event("direct_source_selected", f"Navigating to primary source from Anakin: {target_url}")
            else:
                target_url = build_search_url(clean_info.get("platform", "google"), clean_keywords, max_price)
                
        gathered_info["target_url"] = target_url
        self.state.set_current_step("Navigating", f"Opening {target_url}")
        await self.browser.navigate(target_url)
        self.state.add_event("page_opened", f"Navigated to {target_url}")
        await self._capture_and_record_screenshot("web_start.png", "Navigated to target site")
        
        action_history = []
        max_iterations = 12
        consecutive_same_action = 0
        last_action_key = ""
        
        for iteration in range(max_iterations):
            if self._stop_requested:
                break
                
            was_paused = False
            while self._pause_requested:
                was_paused = True
                await asyncio.sleep(1)
                if self._stop_requested:
                    break
            
            if self._stop_requested:
                break

            # If execution just resumed from human takeover, log the event and capture the new viewport
            if was_paused:
                try:
                    current_takeover_url = self.browser.page.url
                    self.state.add_event("action_succeeded", f"Resumed execution from human takeover at {current_takeover_url}")
                    await self._capture_and_record_screenshot(f"step_{iteration}_takeover_resumed.png", "Human Takeover Resumed")
                except Exception as e:
                    self.state.add_event("warning", f"Could not capture state after human takeover: {e}")

            # Observe DOM
            try:
                url = self.browser.page.url
                title = await self.browser.get_title()
                
                # Extract prominent visible interactive elements and page content summary
                page_info = await self.browser.page.evaluate("""
                    () => {
                        const items = [];
                        const tags = document.querySelectorAll('input, button, a[href], [role="button"], [role="link"], select, textarea, div.price, span.price, div[data-component-type="s-search-result"]');
                        for (const el of tags) {
                            if (el.offsetParent !== null) { // visible
                                const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
                                if (text && text.length < 80) {
                                    items.push({
                                        tag: el.tagName.toLowerCase(),
                                        id: el.id || '',
                                        text: text,
                                        type: el.type || '',
                                        placeholder: el.placeholder || ''
                                    });
                                }
                            }
                            if (items.length >= 35) break;
                        }
                        const heading = document.querySelector('h1, h2')?.textContent || '';
                        const snippets = Array.from(document.querySelectorAll('p, article, .s-result-item')).map(e => (e.textContent || '').trim()).filter(Boolean).join(' | ').slice(0, 500);
                        return { elements: items, page_summary: (heading + ': ' + snippets).trim() };
                    }
                """)
            except Exception as e:
                self.state.add_event("warning", f"DOM observe error (possibly due to tab change): {e}")
                await asyncio.sleep(1)
                continue
                
            elements = page_info.get("elements", [])
            if page_info.get("page_summary"):
                summary_key = f"page_summary_step_{iteration}"
                gathered_info[summary_key] = page_info["page_summary"]
                action_history.append(f"page_scanned: found {len(elements)} elements, summary '{page_info['page_summary'][:50]}...'")

            # Check for Bot Check / CAPTCHA / "I am not a robot"
            captcha_state = await self.dom.detect_captcha()
            if captcha_state.get("detected"):
                sig = captcha_state.get("type", "captcha")
                self.state.add_event("warning", f"Bot checkpoint / 'I am not a robot' detected ({sig}). Engaging Anakin.ai anti-block solver.")
                self.state.set_current_step(f"Bypassing Security Check ({iteration+1})", "Resolving bot challenge via Anakin.ai")
                
                # 1. Try clicking verification checkbox if present
                clicked_cb = await self.dom.try_click_captcha_checkbox()
                if clicked_cb:
                    await asyncio.sleep(2.0)
                    recheck = await self.dom.detect_captcha()
                    if not recheck.get("detected"):
                        self.state.add_event("action_succeeded", "Resolved bot check via interactive verification.")
                        await self._capture_and_record_screenshot(f"step_{iteration}_captcha_resolved.png", "Captcha Resolved")
                        continue

                # 2. Use Anakin.ai anti-block scraper / search credits to bypass
                if self.anakin.is_configured():
                    current_url = self.browser.page.url
                    if "google.com" in current_url or "search" in current_url:
                        self.state.add_event("anakin_query", f"Querying Anakin web intelligence for '{clean_keywords or clean_query}' to bypass search block")
                        anakin_res = await self.anakin.search(clean_keywords or clean_query, limit=5)
                        if anakin_res and anakin_res.get("results"):
                            results = anakin_res["results"]
                            snippets = [f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in results]
                            gathered_info["captcha_bypassed_intel"] = "\n".join(snippets)
                            self.state.add_event("action_succeeded", f"Anakin retrieved {len(results)} search results bypassing bot detection!")
                            top_url = results[0].get("url")
                            if top_url and top_url.startswith("http"):
                                await self.browser.navigate(top_url)
                                await self._capture_and_record_screenshot(f"step_{iteration}_anakin_nav.png", "Navigated to Anakin source")
                                continue
                    else:
                        self.state.add_event("anakin_query", f"Bypassing blocked page using Anakin anti-block cloud scraper for {current_url}")
                        scraped = await self.anakin.scrape_url(current_url, use_browser=True, timeout=25.0)
                        if scraped and not scraped.get("error"):
                            page_text = scraped.get("text") or scraped.get("content") or str(scraped)
                            gathered_info["scraped_content"] = page_text[:4000]
                            self.state.add_event("action_succeeded", "Anakin anti-block scraper extracted page content successfully.")
            
            # Extract structured product cards / listings on search & shopping pages
            listings = await self.dom.extract_listings()
            if listings:
                prev_len = len(gathered_info.get("listings", []))
                gathered_info["listings"] = listings
                if prev_len == 0 or iteration % 2 == 0:
                    self.state.add_event("action_succeeded", f"Identified {len(listings)} options on page")

            inst_lower = instruction.lower()
            clean_term = clean_keywords
            if iteration == 0 and ("wikipedia.org" in url or "google.com" in url) and not gathered_info.get("last_user_answer"):
                if "wikipedia" in inst_lower and len(clean_term) <= 2:
                    decision = {
                        "action": "ask_user",
                        "value": "I have opened Wikipedia for you. What topic or article would you like me to look up?",
                        "reasoning": "Need user's topic choice to search Wikipedia",
                        "confidence": 0.95
                    }
                else:
                    self.state.set_current_step(f"Reasoning ({iteration+1}/{max_iterations})", "Analyzing page state")
                    decision = await self.llm.decide_action(
                        goal=instruction,
                        current_url=url,
                        page_title=title,
                        interactive_elements=elements,
                        action_history=action_history,
                        gathered_info=gathered_info,
                    )
            else:
                self.state.set_current_step(f"Reasoning ({iteration+1}/{max_iterations})", "Analyzing page state")
                decision = await self.llm.decide_action(
                    goal=instruction,
                    current_url=url,
                    page_title=title,
                    interactive_elements=elements,
                    action_history=action_history,
                    gathered_info=gathered_info,
                )
            
            # Clear last_user_answer so it doesn't permanently pollute future iterations
            gathered_info.pop("last_user_answer", None)
            
            action_type = decision.get("action", "done")
            target = decision.get("target", "")
            value = decision.get("value", "")
            confidence = decision.get("confidence", 1.0)
            reasoning = decision.get("reasoning", "")
            
            # Anti-Loop Detection: prevent repeated identical actions that stall execution
            action_key = f"{action_type}:{target}:{str(value)[:40]}"
            if action_key == last_action_key:
                consecutive_same_action += 1
            else:
                consecutive_same_action = 0
            last_action_key = action_key

            if consecutive_same_action >= 2:
                self.state.add_event("warning", f"Action loop detected on '{action_type}:{target}'. Executing anti-loop recovery.")
                if "google.com" in url and ("search?q=" not in url or "sorry" in url):
                    # Direct navigation recovery on Google
                    direct_url = build_search_url("google", clean_keywords or clean_query)
                    await self.browser.navigate(direct_url)
                    await asyncio.sleep(0.5)
                    continue
                else:
                    # Submit by pressing Enter
                    await self.browser.page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)
                    continue

            self.state.add_event("agent_thought", f"Decision: {action_type.upper()} ({reasoning[:80]})")
            action_history.append(f"{action_type}: {target or value}")
            
            if action_type == "done":
                # Guard against premature completion on 404 / missing article pages
                page_text_sample = ""
                try:
                    page_text_sample = await self.browser.page.evaluate("() => (document.body?.innerText || '').slice(0, 5000)")
                except Exception:
                    pass
                
                is_missing_page = any(
                    err_phrase in page_text_sample.lower()
                    for err_phrase in [
                        "does not have an article with this exact name",
                        "does not exist",
                        "page not found",
                        "no results found",
                        "no exact match found",
                        "404 not found"
                    ]
                )
                
                if is_missing_page and iteration < max_iterations - 1:
                    self.state.add_event("warning", "Page indicates topic does not exist directly. Refusing premature completion and searching...")
                    current_url = self.browser.page.url
                    if "wikipedia.org" in current_url:
                        wiki_search = f"https://en.wikipedia.org/w/index.php?search={urllib.parse.quote(clean_keywords)}"
                        await self.browser.navigate(wiki_search)
                        await asyncio.sleep(0.4)
                        await self._capture_and_record_screenshot(f"step_{iteration}_wiki_search.png", "Searched Wikipedia")
                        continue
                    else:
                        searched = False
                        search_links = ["Search for", "search in existing articles", "Special:Search"]
                        for sl in search_links:
                            if await self.dom.click_element(sl, "a"):
                                searched = True
                                await asyncio.sleep(0.4)
                                break
                        if not searched:
                            await self.dom.fill_input("search", clean_keywords)
                            await self.browser.page.keyboard.press("Enter")
                            await asyncio.sleep(0.4)
                        await self._capture_and_record_screenshot(f"step_{iteration}_search_recovery.png", "Searched related articles")
                        continue

                final_summary = value
                if not final_summary or "Explored findings for" in final_summary or "Explored page for" in final_summary:
                    final_summary = await self.llm.summarize_task(instruction, action_history, gathered_info)
                self.state.set_result(final_summary)
                self.state.add_event("completed", final_summary)
                break
                
            elif action_type in ("click_listing", "inspect_item"):
                idx = int(value) if str(value).isdigit() else 0
                current_listings = gathered_info.get("listings", [])
                if current_listings and idx < len(current_listings):
                    target_item = current_listings[idx]
                    item_url = target_item.get("url")
                    if item_url:
                        await self.browser.navigate(item_url)
                        self.state.add_event("action_succeeded", f"Inspecting listing #{idx+1}: {target_item.get('title', '')[:45]}")
                        await self._capture_and_record_screenshot(f"step_{iteration}_inspect_{idx}.png", f"Inspect #{idx+1}")
                await asyncio.sleep(0.3)

            elif action_type == "ask_user":
                question_text = value or f"I need clarification: {reasoning}"
                
                audio_url = None
                try:
                    from .tts_provider import get_tts_provider
                    tts = get_tts_provider()
                    audio_url, _ = await tts.synthesize(question_text)
                except Exception:
                    pass
                    
                self._question_resolved.clear()
                self.state.ask_question(question_text, audio_url=audio_url)
                self.state.add_event("user_question", question_text)
                await self._wait_for_resume()
                
                user_ans = self._get_last_user_answer()
                action_history.append(f"user_answered: {user_ans}")
                gathered_info["last_user_answer"] = user_ans
                gathered_info[question_text] = user_ans
                self.state.add_event("action_succeeded", f"Received user response: '{user_ans}'")
                
            elif action_type == "navigate":
                dest = value or "https://www.ebay.com"
                await self.browser.navigate(dest)
                self.state.add_event("action_succeeded", f"Navigated to {dest}")
                await self._capture_and_record_screenshot(f"step_{iteration}_nav.png", f"Navigate {dest}")
                
            elif action_type == "fill":
                fill_val = value
                is_search = any(s in (target or "").lower() for s in ["search", "gh-ac", "q", "_nkw", "input", "textarea", "ti6dpd"])
                if is_search:
                    if any(w in str(fill_val).lower() for w in ["can you", "browse", "for me", "please", "search for"]) or fill_val == instruction:
                        fill_val = clean_keywords
                filled = await self.dom.fill_input(target or "search", fill_val)
                if filled:
                    display_val = fill_val if "pass" not in str(target).lower() else "********"
                    self.state.add_event("action_succeeded", f"Entered '{display_val}' into {target}")
                    if is_search or ("google.com" in self.browser.page.url and "search?q=" not in self.browser.page.url):
                        await self.browser.page.keyboard.press("Enter")
                        await asyncio.sleep(0.5)
                await self._capture_and_record_screenshot(f"step_{iteration}_fill.png", f"Fill {target}")
                
            elif action_type in ("press_key", "press"):
                key_to_press = value or "Enter"
                await self.browser.page.keyboard.press(key_to_press)
                self.state.add_event("action_succeeded", f"Pressed key '{key_to_press}'")
                await self._capture_and_record_screenshot(f"step_{iteration}_press.png", f"Press {key_to_press}")
                await asyncio.sleep(0.4)

            elif action_type == "click":
                clicked = await self.dom.click_best_candidate([target]) if target else False
                if not clicked and target:
                    try:
                        await self.browser.page.click(target, timeout=3000)
                        clicked = True
                    except Exception:
                        pass
                if clicked:
                    self.state.add_event("action_succeeded", f"Clicked '{target}'")
                await self._capture_and_record_screenshot(f"step_{iteration}_click.png", f"Click {target}")
                await asyncio.sleep(0.3)

            elif action_type == "scroll":
                try:
                    scroll_amount = int(str(value).strip())
                except (ValueError, TypeError):
                    scroll_amount = 600
                direction = "up" if scroll_amount < 0 else "down"
                try:
                    await self.browser.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    self.state.add_event("action_succeeded", f"Scrolled {direction} {abs(scroll_amount)}px")
                    await self._capture_and_record_screenshot(f"step_{iteration}_scroll.png", f"Scroll {scroll_amount}px")
                except Exception as e:
                    pass
                await asyncio.sleep(0.3)
                
            await asyncio.sleep(0.2)

        # Generate a detailed summary if not already set
        if not self.state.get_state().result:
            self.state.set_current_step("Summarizing", "Generating final report")
            final_summary = await self.llm.summarize_task(instruction, action_history, gathered_info)
            self.state.set_result(final_summary)
            self.state.add_event("completed", final_summary)

    async def _execute_secure_portal_flow(self, instruction: str):
        """Executes the specific sandbox flow with self-healing logic."""
        self.state.set_current_step("Opening Portal", "Navigating to login page")
        target_url = self.config.target_url or f"{self.config.base_url}/sandbox/login.html"
        await self.browser.navigate(target_url)
        self.state.add_event("page_opened", f"Navigated to {target_url}")
        await self._capture_and_record_screenshot("01_login_page.png", "Login Page")

        self.state.set_current_step("Filling Credentials", "Entering username and password")
        await self.dom.fill_input("username", self.config.target_username or "testuser")
        await self.dom.fill_input("password", self.config.target_password or "testpass123")
        self.state.add_event("action_succeeded", "Credentials filled")
        await self._capture_and_record_screenshot("02_credentials_filled.png", "Credentials filled")

        self.state.set_current_step("Submitting Login", "Clicking login button")
        login_clicked = await self._execute_with_circuit_breaker(
            "Click Login",
            "login_button",
            lambda: self.dom.click_best_candidate(["Sign in", "Continue", "Submit", "Login"])
        )
        if not login_clicked:
            raise Exception("Could not find login button")
        self.state.add_event("action_succeeded", "Login submitted")
        await self._capture_and_record_screenshot("03_login_submitted.png", "Login submitted")
        
        await asyncio.sleep(2)

        self.state.set_current_step("Detecting OTP", "Checking for OTP field")
        otp_page_detected = await self.dom.check_element_exists("input[placeholder='Enter OTP'], input[type='text'] >> nth=0")
        
        if otp_page_detected:
            self.state.add_event("otp_required", "OTP verification required")
            self.state.set_current_step("Retrieving OTP", "Checking email inbox")
            self.state.add_event("email_check_started", "Polling for OTP email")
            
            since_ts = datetime.now(timezone.utc).isoformat()
            email_data = None
            start_wait = time.time()
            while time.time() - start_wait < 30 and not self._stop_requested:
                while self._pause_requested:
                    await asyncio.sleep(0.5)
                    if self._stop_requested:
                        break
                if self._stop_requested:
                    break
                
                # Non-blocking check from email provider
                if hasattr(self.email_provider, '_mail'):
                    # It's IMAP, so run it in a thread to not block event loop
                    inbox = await asyncio.to_thread(self.email_provider.get_inbox)
                else:
                    inbox = self.email_provider.get_inbox()
                for em in inbox:
                    if not since_ts or em.timestamp >= since_ts:
                        email_data = em
                        break
                if email_data:
                    break
                await asyncio.sleep(3.0) # Prevent spamming IMAP server
                
            if self._stop_requested:
                return
            if not email_data:
                raise Exception("OTP email not received within timeout")
                
            sender = getattr(email_data, 'sender', 'Unknown')
            body = getattr(email_data, 'body', '')
            subject = getattr(email_data, 'subject', '')
            self.state.add_event("email_received", f"Email from: {sender}")
            
            otp_text = body + " " + subject
            otp_code = extract_otp(otp_text)
            if not otp_code:
                raise Exception("Could not extract OTP from email")
                
            masked = mask_otp(otp_code)
            self.state.add_event("otp_found", f"OTP extracted: {masked}")
            await self._capture_and_record_screenshot("04_email_otp.png", "Email OTP")

            self.state.set_current_step("Filling OTP", "Entering OTP code")
            await self.dom.fill_input("otp", otp_code)
            self.state.add_event("otp_filled", "OTP entered in form")
            await self._capture_and_record_screenshot("05_otp_filled.png", "OTP filled")

            self.state.set_current_step("Submitting OTP", "Verifying code")
            verify_clicked = await self._execute_with_circuit_breaker(
                "Submit OTP",
                "otp_submit_button",
                lambda: self.dom.click_best_candidate(["Verify", "Continue", "Submit"])
            )
            if not verify_clicked:
                raise Exception("Could not find OTP submit button")
            self.state.add_event("action_succeeded", "OTP submitted")
            await self._capture_and_record_screenshot("06_otp_submitted.png", "OTP submitted")
            await asyncio.sleep(2)

        self.state.set_current_step("Accessing Dashboard", "Navigating to documents")
        await self._capture_and_record_screenshot("07_dashboard.png", "Dashboard")

        self.state.set_current_step("Selecting Document", "Downloading tax document")
        doc_clicked = await self.dom.click_best_candidate([
            "Download Tax Document",
            "Download Statement",
            "Tax Document",
            "Download"
        ])
        
        if doc_clicked:
            self.state.add_event("action_succeeded", "Document download initiated")
            await self._capture_and_record_screenshot("08_document_selected.png", "Document selected")
            await asyncio.sleep(1)

        self.state.set_current_step("Verifying Completion", "Checking success state")
        success_detected = await self.dom.check_element_exists("text=Completed, text=Success, text=Dashboard")
        
        if success_detected:
            self.state.add_event("completed", "Task verified successful")
            self.state.set_result("Portal login and document retrieval completed successfully.")
        else:
            self.state.add_event("warning", "Task completed but confirmation message not explicitly found")
            self.state.set_result("Portal workflow completed.")

    async def _execute_with_circuit_breaker(self, action_name: str, target_element: str, click_func) -> bool:
        """Prevents infinite loops if the same action fails repeatedly."""
        page_hash = await self.dom.get_page_hash()
        
        if not hasattr(self, '_action_attempts'):
            self._action_attempts = {}
        
        key = f"{page_hash}:{action_name}:{target_element}"
        self._action_attempts[key] = self._action_attempts.get(key, 0) + 1
        
        if self._action_attempts[key] >= 3:
            self.state.add_event("warning", f"Circuit breaker triggered! Tried '{action_name}' on '{target_element}' 3 times with no change.")
            
            question = f"I'm stuck. I tried {action_name} on '{target_element}' but nothing happened after 3 attempts. What should I do?"
            self._question_resolved.clear()
            self.state.ask_question(question)
            self.state.add_event("user_question", "Waiting for user guidance...")
            
            await self._wait_for_resume()
            self._action_attempts[key] = 0
            return True
        
        try:
            result = await click_func()
            return result
        except Exception as e:
            self.state.add_event("retrying", f"Attempt {self._action_attempts[key]} failed: {str(e)}")
            raise
    
    async def _wait_for_resume(self) -> None:
        """Pause execution until user answers or stop is requested."""
        while self.state.get_state().question is not None and not self._stop_requested:
            if self._question_resolved.is_set():
                break
            await asyncio.sleep(0.3)
                
    def _get_last_user_answer(self) -> str:
        if self._last_user_answer:
            ans = self._last_user_answer
            self._last_user_answer = ""
            return ans
        events = self.state.get_state().events
        for event in reversed(events):
            if event.event_type == "user_answer_received":
                return event.message.replace("User answered:", "").strip()
        return ""

    async def safe_get_live_frame(self, image_type: str = "jpeg", quality: int = 65) -> Optional[bytes]:
        """Thread-safe live viewport frame retrieval for streaming."""
        if not hasattr(self, "browser") or not self.browser or not self.browser._launched:
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.browser.screenshot_bytes(image_type=image_type, quality=quality),
                self.loop
            )
            return await asyncio.wrap_future(future)
        except Exception:
            return None

    async def safe_click_ratio(self, x_ratio: float, y_ratio: float) -> Dict[str, Any]:
        """Thread-safe click on browser page at given ratios."""
        async def _do_click():
            await self.browser.ensure_launched()
            page = self.browser.page
            viewport = page.viewport_size or {"width": 1280, "height": 800}
            if not page.viewport_size:
                try:
                    dim = await page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                    if dim and dim.get("width"):
                        viewport = dim
                except Exception:
                    pass
            cx = max(0, min(viewport["width"], x_ratio * viewport["width"]))
            cy = max(0, min(viewport["height"], y_ratio * viewport["height"]))
            await page.mouse.click(cx, cy)
            await asyncio.sleep(0.3)
            fn = await self.browser.screenshot("user_click.png")
            if fn:
                self.state.add_screenshot(fn, "User Viewport Click", f"/screenshots/{fn}")
            return {
                "status": "clicked",
                "screenshot": f"/screenshots/{fn}" if fn else None,
                "x": cx,
                "y": cy,
                "url": page.url,
                "title": await self.browser.get_title()
            }
        future = asyncio.run_coroutine_threadsafe(_do_click(), self.loop)
        return await asyncio.wrap_future(future)

    async def safe_type(self, text: str, press_enter: bool = False) -> Dict[str, Any]:
        """Thread-safe type text into browser."""
        async def _do_type():
            await self.browser.ensure_launched()
            page = self.browser.page
            if text:
                await page.keyboard.type(text)
            if press_enter:
                await page.keyboard.press("Enter")
            await asyncio.sleep(0.3)
            fn = await self.browser.screenshot("user_type.png")
            if fn:
                self.state.add_screenshot(fn, "User Viewport Type", f"/screenshots/{fn}")
            return {
                "status": "typed",
                "screenshot": f"/screenshots/{fn}" if fn else None,
                "url": page.url,
                "title": await self.browser.get_title()
            }
        future = asyncio.run_coroutine_threadsafe(_do_type(), self.loop)
        return await asyncio.wrap_future(future)

    async def safe_navigate(self, url: str) -> Dict[str, Any]:
        """Thread-safe navigation."""
        async def _do_nav():
            await self.browser.ensure_launched()
            await self.browser.navigate(url)
            fn = await self.browser.screenshot("user_nav.png")
            if fn:
                self.state.add_screenshot(fn, "User Viewport Nav", f"/screenshots/{fn}")
            return {
                "status": "navigated",
                "screenshot": f"/screenshots/{fn}" if fn else None,
                "url": self.browser.page.url if self.browser.page else url,
                "title": await self.browser.get_title() if self.browser.page else ""
            }
        future = asyncio.run_coroutine_threadsafe(_do_nav(), self.loop)
        return await asyncio.wrap_future(future)
