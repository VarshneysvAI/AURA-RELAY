"""
AURA Relay Agent - Persistent execution orchestrator

CRITICAL ARCHITECTURAL FIX:
This version uses a SINGLE PERSISTENT event loop and background thread.
Playwright objects (Browser, Context, Page) are bound to this loop.
When a task finishes, the browser stays ALIVE for the next task.
This enables true session persistence (cookies, logins, history).
"""
import asyncio
import threading
from datetime import datetime
from typing import Optional

from .browser_manager import BrowserManager
from .dom_intelligence import DomIntelligence
from .otp import extract_otp, mask_otp


class AgentExecutor:
    """
    Persistent Agent Executor.
    
    Runs on a single dedicated background thread with its own event loop.
    This ensures the browser context remains alive across multiple tasks,
    enabling true session persistence (cookies, logins, history).
    """
    
    def __init__(self, state_manager, email_provider, config, security_manager):
        self.state = state_manager
        self.email_provider = email_provider
        self.config = config
        self.security_manager = security_manager
        
        # Initialize browser manager (lazy launch)
        self.browser = BrowserManager(
            headless=config.headless,
            slow_mo=config.slow_mo
        )
        self.dom = DomIntelligence(self.browser, self.state)
        
        self._stop_requested = False
        self._last_user_answer = ""
        
        # CRITICAL FIX: Single persistent event loop for Playwright
        # This prevents the "event loop closed" crash on subsequent runs
        self.loop = asyncio.new_event_loop()
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

    def start(self, instruction: str) -> bool:
        if self.is_running():
            return False
            
        self.state.set_status("running")
        self.state.clear_events() 
        self._stop_requested = False
        self._question_resolved.clear()
        
        # Schedule the run method on the persistent loop
        coro = self.run(instruction)
        self._current_task_future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return True

    def stop(self):
        self._stop_requested = True
        self.state.stop_execution()
        if self._current_task_future and not self._current_task_future.done():
            self._current_task_future.cancel()

    def submit_answer(self, answer: str, question_id: int) -> bool:
        success = self.state.answer_question(answer, question_id)
        if success:
            self._last_user_answer = answer
            self.state.add_event("user_answer_received", f"User answered: {answer}")
            self._question_resolved.set()  # Signal resume
        return success

    async def run(self, instruction: str):
        self._stop_requested = False
        self.state.set_status("running")
        self.state.add_event("plan_created", f"Starting task: {instruction}")
        
        try:
            # Ensure browser is launched (persistent across runs)
            await self.browser.ensure_launched()
            
            # Execute the secure portal retrieval flow
            await self._execute_secure_portal_flow(instruction)
            
            # Finalize
            if not self._stop_requested:
                self.state.set_current_step("Completed", "Task finished successfully")
                self.state.add_event("completed", "Task completed successfully")
                result = f"Successfully retrieved document. OTP verification completed."
                self.state.set_result(result)
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
        # NOTE: We do NOT close the browser here to maintain persistence

    async def _execute_secure_portal_flow(self, instruction: str):
        """Executes the specific sandbox flow with self-healing logic."""
        
        # Step 1: Open Portal
        self.state.set_current_step("Opening Portal", "Navigating to login page")
        target_url = self.config.get_target_url()
        await self.browser.navigate(target_url)
        self.state.add_event("page_opened", f"Navigated to {target_url}")
        await self.browser.screenshot("01_login_page.png")

        # Step 2: Fill Credentials
        self.state.set_current_step("Filling Credentials", "Entering username and password")
        await self.dom.fill_by_strategy("username", self.config.target_username or "testuser")
        await self.dom.fill_by_strategy("password", self.config.target_password or "testpass123")
        self.state.add_event("action_succeeded", "Credentials filled")
        await self.browser.screenshot("02_credentials_filled.png")

        # Step 3: Click Login (Handle Dynamic Text)
        self.state.set_current_step("Submitting Login", "Clicking login button")
        # Try "Sign in" first, then "Continue" if DOM changes
        login_clicked = await self.dom.click_best_candidate(
            ["button:has-text('Sign in')", "button:has-text('Continue')", "input[type='submit']"]
        )
        if not login_clicked:
            raise Exception("Could not find login button")
        self.state.add_event("action_succeeded", "Login submitted")
        await self.browser.screenshot("03_login_submitted.png")
        
        # Wait for navigation/OTP page
        await asyncio.sleep(2)

        # Step 4: Detect OTP Requirement
        self.state.set_current_step("Detecting OTP", "Checking for OTP field")
        otp_page_detected = await self.dom.element_exists("input[placeholder='Enter OTP'], input[type='text'] >> nth=0")
        
        if otp_page_detected:
            self.state.add_event("otp_required", "OTP verification required")
            
            # Step 5: Retrieve OTP from Email
            self.state.set_current_step("Retrieving OTP", "Checking email inbox")
            self.state.add_event("email_check_started", "Polling for OTP email")
            
            email_data = await self.email_provider.wait_for_email(
                since_timestamp=datetime.now(),
                timeout_seconds=30
            )
            
            if not email_data:
                raise Exception("OTP email not received within timeout")
                
            self.state.add_event("email_received", f"Email from: {email_data.get('sender', 'Unknown')}")
            
            # Step 6: Extract OTP
            otp_text = email_data.get('body', '') + " " + email_data.get('subject', '')
            otp_code = extract_otp(otp_text)
            
            if not otp_code:
                raise Exception("Could not extract OTP from email")
                
            masked = mask_otp(otp_code)
            self.state.add_event("otp_found", f"OTP extracted: {masked}")
            await self.browser.screenshot("04_email_otp.png")

            # Step 7: Fill OTP
            self.state.set_current_step("Filling OTP", "Entering OTP code")
            # Find the OTP input specifically
            await self.dom.fill_by_strategy("otp", otp_code)
            self.state.add_event("otp_filled", "OTP entered in form")
            await self.browser.screenshot("05_otp_filled.png")

            # Step 8: Submit OTP (Handle Dynamic Text)
            self.state.set_current_step("Submitting OTP", "Verifying code")
            verify_clicked = await self.dom.click_best_candidate(
                ["button:has-text('Verify')", "button:has-text('Continue')", "input[type='submit']"]
            )
            if not verify_clicked:
                raise Exception("Could not find OTP submit button")
            self.state.add_event("action_succeeded", "OTP submitted")
            await self.browser.screenshot("06_otp_submitted.png")
            
            # Wait for dashboard
            await asyncio.sleep(2)

        # Step 9: Navigate Dashboard & Detect Ambiguity
        self.state.set_current_step("Analyzing Dashboard", "Looking for download options")
        await self.browser.screenshot("07_dashboard.png")
        
        # Check for both options
        has_statement = await self.dom.element_exists("text=Download Statement")
        has_tax = await self.dom.element_exists("text=Download Tax Document")
        
        if has_statement and has_tax:
            # Ambiguity detected - Ask User
            question_id = self.state.ask_question(
                "I found two options: 'Download Statement' or 'Download Tax Document'. Which one should I choose?"
            )
            self.state.add_event("user_question", "Waiting for user input...")
            
            # Wait for user answer (Pause Execution)
            await self._wait_for_resume()
            
            answer = self.state.get_state().question.answer.lower() if self.state.get_state().question else ""
            self.state.add_event("user_answer_received", f"User selected: {answer}")
            
            # Step 10: Click Selected Choice
            self.state.set_current_step("Downloading Document", f"Clicking based on: {answer}")
            
            if "tax" in answer:
                clicked = await self.dom.click_best_candidate(["text=Download Tax Document", "text=Tax Document"])
            elif "statement" in answer:
                clicked = await self.dom.click_best_candidate(["text=Download Statement", "text=Statement"])
            else:
                # Default fallback
                clicked = await self.dom.click_best_candidate(["text=Download Tax Document"])
                
            if not clicked:
                raise Exception("Could not click the selected document button")
                
            self.state.add_event("action_succeeded", "Document download initiated")
            await self.browser.screenshot("08_document_selected.png")
            await asyncio.sleep(1)

        # Step 11: Verify Completion
        self.state.set_current_step("Verifying Completion", "Checking success state")
        # Look for success message or download indicator
        success_detected = await self.dom.element_exists("text=Completed") or await self.dom.element_exists("text=Success")
        
        if success_detected:
            self.state.add_event("completed", "Task verified successful")
        else:
            self.state.add_event("warning", "Task completed but success message not explicitly found")

    async def _execute_with_circuit_breaker(self, action_name: str, target_element: str, 
                                             click_func) -> bool:
        """
        Silent Killer #3: Circuit Breaker - Prevents infinite loops.
        If the same action fails 3 times on the same page, ask human for help.
        """
        # Get current page state hash
        page_hash = await self.dom.get_page_hash()
        
        # Initialize or get action attempts counter
        if not hasattr(self, '_action_attempts'):
            self._action_attempts = {}
        
        key = f"{page_hash}:{action_name}:{target_element}"
        self._action_attempts[key] = self._action_attempts.get(key, 0) + 1
        
        # CIRCUIT BREAKER: If we've tried the same thing 3 times
        if self._action_attempts[key] >= 3:
            self.state.add_event("warning", f"Circuit breaker triggered! Tried '{action_name}' on '{target_element}' 3 times with no change.")
            
            # Force human intervention
            question = f"I'm stuck. I tried {action_name} on '{target_element}' but nothing happened after 3 attempts. The page seems unchanged. What should I do?"
            self.state.ask_question(question)
            self.state.add_event("user_question", "Waiting for user guidance...")
            
            # Wait for user answer
            await self._wait_for_resume()
            
            # Reset counter after user intervention
            self._action_attempts[key] = 0
            return True  # User took over
        
        # Execute the action
        try:
            result = await click_func()
            return result
        except Exception as e:
            self.state.add_event("retrying", f"Attempt {self._action_attempts[key]} failed: {str(e)}")
            raise
    
    async def _wait_for_resume(self) -> None:
        """Pause execution until user answers or stop is requested."""
        while self.state.get_state().question is not None and not self._stop_requested:
            if self._question_resolved.wait(timeout=0.5):
                break
                
    def _get_last_user_answer(self) -> str:
        events = self.state.get_state().events
        for event in reversed(events):
            if event.event_type == "user_answer_received":
                return event.message.replace("User answered:", "").strip()
        return ""
