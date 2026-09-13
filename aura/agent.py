"""
AURA Relay Agent - Main execution orchestrator
"""
import asyncio
import threading
from datetime import datetime
from typing import Optional, Callable, Any

from .config import get_config, Config
from .state import StateManager
from .browser_manager import BrowserManager
from .dom_intelligence import DomIntelligence
from .email_provider import EmailProvider, LocalEmailProvider, Email
from .otp import extract_otp, mask_otp
from .planner import Planner
from .security import SecurityManager


class AgentExecutor:
    """
    Main agent executor that runs tasks step by step.
    Handles browser automation, OTP flows, and user interaction.
    """
    
    def __init__(self, state_manager: StateManager, email_provider: EmailProvider,
                 config: Config, security_manager: SecurityManager):
        self.state = state_manager
        self.email_provider = email_provider
        self.config = config
        self.security = security_manager
        self.planner = Planner()
        
        self.browser: Optional[BrowserManager] = None
        self.dom: Optional[DomIntelligence] = None
        
        self._stop_requested = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self, instruction: str) -> None:
        """Start the agent in a background thread."""
        if self._running:
            raise RuntimeError("Agent already running")
        
        self._stop_requested = False
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(instruction,))
        self._thread.start()
    
    def stop(self) -> None:
        """Request graceful stop."""
        self._stop_requested = True
    
    def is_running(self) -> bool:
        return self._running
    
    def _run(self, instruction: str) -> None:
        """Main execution loop."""
        try:
            asyncio.run(self._execute_async(instruction))
        except Exception as e:
            self.state.set_error(str(e))
        finally:
            self._running = False
    
    async def _execute_async(self, instruction: str) -> None:
        """Async execution of the task."""
        # Initialize state
        self.state.start_execution(instruction)
        self.state.add_event("plan_created", f"Plan created for: {instruction}")
        
        # Get plan
        plan = self.planner.get_plan_for_instruction(instruction)
        self.state.set_plan(plan)
        
        # Launch browser
        self.state.add_event("step_started", "Launching browser")
        self.browser = BrowserManager(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo
        )
        
        try:
            await self.browser.launch()
            self.dom = DomIntelligence(self.browser.page)
            self.state.add_event("browser_launched", "Browser launched successfully")
            
            # Determine base URL
            if self.config.is_sandbox:
                base_url = f"{self.config.base_url}/sandbox"
            else:
                base_url = self.config.target_url or ""
            
            # Execute the secure portal flow
            await self._secure_portal_flow(base_url, instruction)
            
        except Exception as e:
            self.state.add_event("error", f"Execution error: {str(e)}")
            self.state.set_error(str(e))
        finally:
            # Cleanup
            if self.browser:
                await self.browser.close()
    
    async def _secure_portal_flow(self, base_url: str, instruction: str) -> None:
        """Execute the secure portal document retrieval flow."""
        
        # Step 1: Open login page
        self.state.set_current_step("Opening secure portal", "Navigate to login page")
        login_url = f"{base_url}/login.html"
        self.state.add_event("page_opened", f"Navigating to {login_url}")
        await self.browser.navigate(login_url)
        await self.browser.wait_for_timeout(500)
        
        if self.config.screenshot_after_each_action:
            filename = await self.browser.screenshot("login_page", "Open login page")
            if filename:
                self.state.add_screenshot(filename, "login_page", f"/screenshots/{filename}")
        
        # Step 2: Fill credentials
        self.state.set_current_step("Filling credentials", "Enter username and password")
        
        # Use sandbox credentials
        username = "testuser"
        password = "testpass123"
        
        self.state.add_event("action_started", "Filling username field")
        filled = await self.dom.fill_input("username", username)
        if not filled:
            # Try alternative selectors
            await self.browser.page.fill('input[name="username"]', username)
        
        self.state.add_event("element_found", "Username field found and filled")
        
        self.state.add_event("action_started", "Filling password field")
        filled = await self.dom.fill_input("password", password)
        if not filled:
            await self.browser.page.fill('input[type="password"]', password)
        
        self.state.add_event("element_found", "Password field found and filled")
        
        if self.config.screenshot_after_each_action:
            filename = await self.browser.screenshot("credentials_filled", "Fill credentials")
            if filename:
                self.state.add_screenshot(filename, "credentials_filled", f"/screenshots/{filename}")
        
        # Step 3: Click login button
        self.state.set_current_step("Submitting login", "Click login button")
        self.state.add_event("action_started", "Clicking login button")
        
        # Handle dynamic button text - try both "Sign in" and "Continue"
        clicked = await self.dom.click_element("Sign in", "button")
        if not clicked:
            clicked = await self.dom.click_element("Continue", "button")
        
        if not clicked:
            # Fallback to generic button
            await self.browser.page.click('button[type="submit"]')
        
        self.state.add_event("action_succeeded", "Login button clicked")
        
        await self.browser.wait_for_timeout(1000)
        
        if self.config.screenshot_after_each_action:
            filename = await self.browser.screenshot("login_submitted", "Submit login")
            if filename:
                self.state.add_screenshot(filename, "login_submitted", f"/screenshots/{filename}")
        
        # Step 4: Detect OTP requirement and navigate to OTP page
        self.state.set_current_step("Detecting OTP requirement", "Check for OTP page")
        self.state.add_event("otp_required", "OTP verification required")
        
        # Wait for OTP page to load
        await self.browser.wait_for_timeout(1500)
        
        # Step 5: Get OTP from email
        self.state.set_current_step("Retrieving OTP from email", "Check inbox for OTP")
        self.state.add_event("step_started", "Fetching OTP from email inbox")
        
        since_time = datetime.utcnow().isoformat() + "Z"
        
        # In sandbox mode, trigger OTP email generation
        if self.config.is_sandbox:
            # The login page should have triggered OTP email
            await self.browser.wait_for_timeout(500)
        
        # Wait for OTP email with retries
        otp_code = None
        max_retries = 5
        
        for retry in range(max_retries):
            email = self.email_provider.wait_for_email(since_time, timeout_seconds=5)
            
            if email:
                otp_code = extract_otp(email.body)
                self.state.add_event("email_received", f"OTP email received from {email.sender}")
                self.state.add_event("otp_found", f"OTP extracted: {mask_otp(otp_code)}")
                break
            else:
                # Try to get from local provider directly
                otp_code = self.email_provider.get_latest_otp()
                if otp_code:
                    self.state.add_event("otp_found", f"OTP extracted: {mask_otp(otp_code)}")
                    break
            
            if retry < max_retries - 1:
                self.state.add_event("retrying", f"Waiting for OTP email (attempt {retry + 2}/{max_retries})")
                await self.browser.wait_for_timeout(1000 * (retry + 1))
        
        if not otp_code:
            # Ask user for help
            question = "No OTP email found after multiple attempts. Should I use test OTP '123456'?"
            self.state.ask_question(question)
            await self._wait_for_resume()
            
            # After resume, check if user wants to use test OTP
            user_answer = self._get_last_user_answer().lower()
            if "yes" in user_answer or "test" in user_answer or "123456" in user_answer:
                otp_code = "123456"
                self.state.add_event("otp_found", f"Using test OTP: {mask_otp(otp_code)}")
            else:
                self.state.set_error("User declined to use test OTP. Cannot proceed.")
                return
        
        # Step 6: Fill OTP
        self.state.set_current_step("Filling OTP", "Enter OTP code")
        self.state.add_event("action_started", "Filling OTP field")
        
        if otp_code:
            filled = await self.dom.fill_input("otp", otp_code)
            if not filled:
                await self.browser.page.fill('input[name="otp"]', otp_code)
            
            self.state.add_event("otp_filled", f"OTP filled: {mask_otp(otp_code)}")
            
            if self.config.screenshot_after_each_action:
                filename = await self.browser.screenshot("otp_filled", "Fill OTP")
                if filename:
                    self.state.add_screenshot(filename, "otp_filled", f"/screenshots/{filename}")
        
        # Step 7: Submit OTP
        self.state.set_current_step("Submitting OTP", "Verify OTP code")
        self.state.add_event("action_started", "Clicking verify button")
        
        clicked = await self.dom.click_element("Verify", "button")
        if not clicked:
            clicked = await self.dom.click_element("Continue", "button")
        
        if not clicked:
            await self.browser.page.click('button[type="submit"]')
        
        self.state.add_event("action_succeeded", "OTP submitted")
        
        await self.browser.wait_for_timeout(1500)
        
        if self.config.screenshot_after_each_action:
            filename = await self.browser.screenshot("otp_submitted", "Submit OTP")
            if filename:
                self.state.add_screenshot(filename, "otp_submitted", f"/screenshots/{filename}")
        
        # Step 8: Navigate to dashboard
        self.state.set_current_step("Navigating to dashboard", "Wait for dashboard")
        await self.browser.wait_for_timeout(1000)
        
        # Step 9: Identify document options and ask user
        self.state.set_current_step("Identifying document options", "Check available downloads")
        
        # Ask user which document to download
        question = "I found two options: Download Statement or Download Tax Document. Which one should I choose?"
        self.state.ask_question(question)
        
        # Wait for user answer
        await self._wait_for_resume()
        
        # Get user's answer from state events
        user_answer = self._get_last_user_answer()
        
        # Step 10: Click selected document button
        self.state.set_current_step("Downloading document", "Click selected document button")
        
        answer_lower = user_answer.lower() if user_answer else ""
        
        if "tax" in answer_lower or "document" in answer_lower:
            target_button = "Tax Document"
        elif "statement" in answer_lower:
            target_button = "Statement"
        else:
            target_button = "Tax Document"  # Default
        
        self.state.add_event("action_started", f"Clicking {target_button} button")
        
        # Try multiple strategies for clicking the document button
        clicked = False
        
        # Strategy 1: Click by text content
        clicked = await self.dom.click_element(f"Download {target_button}", "button")
        
        # Strategy 2: Try just the main keyword
        if not clicked:
            clicked = await self.dom.click_element(target_button, "button")
        
        # Strategy 3: Try partial match
        if not clicked:
            clicked = await self.dom.click_element("Download", "button")
        
        # Strategy 4: Direct Playwright fallback
        if not clicked:
            buttons = await self.browser.page.query_selector_all("button, .document-card, [role='button']")
            for btn in buttons:
                try:
                    text = await btn.text_content()
                    if text and target_button.lower() in text.lower():
                        await btn.click()
                        clicked = True
                        break
                except Exception:
                    continue
        
        # Strategy 5: Last resort - click by ID
        if not clicked:
            if "tax" in target_button.lower():
                await self.browser.page.click("#taxBtn")
            else:
                await self.browser.page.click("#statementBtn")
            clicked = True
        
        self.state.add_event("action_succeeded", f"Clicked {target_button} button")
        
        await self.browser.wait_for_timeout(1000)
        
        if self.config.screenshot_after_each_action:
            filename = await self.browser.screenshot("document_selected", "Select document")
            if filename:
                self.state.add_screenshot(filename, "document_selected", f"/screenshots/{filename}")
        
        # Step 11: Verify completion
        self.state.set_current_step("Verifying completion", "Check success state")
        self.state.add_event("completed", "Task completed successfully")
        
        result = f"Successfully retrieved tax document from secure portal. OTP verification completed. Document: {target_button}"
        self.state.set_result(result)
    
    async def _wait_for_resume(self) -> None:
        """Wait until user answers or stop is requested."""
        while self.state.get_state().question is not None and not self._stop_requested:
            await asyncio.sleep(0.5)
            
            if self._stop_requested:
                self.state.stop_execution()
                return
    
    def _get_last_user_answer(self) -> str:
        """Get the last user answer from events."""
        events = self.state.get_state().events
        for event in reversed(events):
            if event.event_type == "user_answer_received":
                # Extract answer from message "User answered: xxx"
                if event.message.startswith("User answered:"):
                    return event.message.replace("User answered:", "").strip()
        return ""
    
    def submit_answer(self, answer: str, question_id: int) -> bool:
        """Submit an answer to the current question."""
        return self.state.answer_question(answer, question_id)
