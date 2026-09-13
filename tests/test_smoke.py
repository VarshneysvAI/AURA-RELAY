"""
AURA Relay Test Suite
"""
import pytest
from aura.otp import extract_otp, mask_otp, redact_secret, is_otp_related_text
from aura.state import StateManager


class TestOtpExtraction:
    """Test OTP extraction functionality."""
    
    def test_extract_six_digit_otp(self):
        text = "Your verification code is 123456. Please enter it."
        assert extract_otp(text) == "123456"
    
    def test_extract_four_digit_otp(self):
        text = "Your code: 1234"
        assert extract_otp(text) == "1234"
    
    def test_prefer_six_digit_over_four(self):
        text = "Code 1234 or 123456"
        assert extract_otp(text) == "123456"
    
    def test_no_otp_in_text(self):
        text = "Hello world, no numbers here"
        assert extract_otp(text) is None
    
    def test_empty_text(self):
        assert extract_otp("") is None
        assert extract_otp(None) is None
    
    def test_otp_from_subject_and_body(self):
        subject = "Your OTP Code"
        body = "The code is 789012"
        from aura.otp import parse_email_for_otp
        otp, is_otp = parse_email_for_otp(subject, body)
        assert otp == "789012"
        assert is_otp is True


class TestOtpMasking:
    """Test OTP masking for security."""
    
    def test_mask_six_digit(self):
        assert mask_otp("123456") == "****56"
    
    def test_mask_four_digit(self):
        assert mask_otp("1234") == "**34"
    
    def test_mask_short(self):
        assert mask_otp("12") == "**"
    
    def test_mask_empty(self):
        assert mask_otp("") == ""


class TestSecretRedaction:
    """Test secret redaction."""
    
    def test_redact_password(self):
        # Shows first 4 chars, rest masked
        assert redact_secret("mypassword123") == "mypa*********"
    
    def test_redact_short(self):
        assert redact_secret("abc") == "***"
    
    def test_redact_empty(self):
        assert redact_secret("") == ""


class TestOtpDetection:
    """Test OTP-related text detection."""
    
    def test_detect_otp_keywords(self):
        assert is_otp_related_text("Your OTP code is ready") is True
        assert is_otp_related_text("verification code") is True
        assert is_otp_related_text("one-time password") is True
    
    def test_non_otp_text(self):
        assert is_otp_related_text("Hello friend") is False
        assert is_otp_related_text("Meeting reminder") is False


class TestStateManager:
    """Test state management."""
    
    def test_initial_state(self):
        state = StateManager()
        s = state.to_dict()
        
        assert s["status"] == "idle"
        assert s["instruction"] == ""
        assert len(s["events"]) == 0
    
    def test_set_status(self):
        state = StateManager()
        state.set_status("running")
        assert state.to_dict()["status"] == "running"
    
    def test_add_event(self):
        state = StateManager()
        state.add_event("test_event", "Test message", {"key": "value"})
        
        events = state.to_dict()["events"]
        assert len(events) == 1
        assert events[0]["type"] == "test_event"
        assert events[0]["message"] == "Test message"
    
    def test_ask_question(self):
        state = StateManager()
        question_id = state.ask_question("What should I do?")
        
        s = state.to_dict()
        assert s["question"] == "What should I do?"
        assert s["question_id"] == question_id
        assert s["status"] == "paused"
    
    def test_answer_question(self):
        state = StateManager()
        qid = state.ask_question("Question?")
        
        success = state.answer_question("Answer", qid)
        assert success is True
        
        s = state.to_dict()
        assert s["question"] is None
        assert s["status"] == "running"
    
    def test_answer_wrong_question(self):
        state = StateManager()
        state.ask_question("Question?")
        
        success = state.answer_question("Answer", 999)
        assert success is False


class TestDomIntelligenceStrategy:
    """Test DOM intelligence locator strategies (unit tests)."""
    
    def test_candidate_building_logic(self):
        # Test that we understand the strategy order
        from aura.dom_intelligence import CandidateLocator
        
        candidate = CandidateLocator(
            selector='[data-testid="login"]',
            strategy="data-testid",
            description="data-testid=login",
            score=0.95
        )
        
        assert candidate.score == 0.95
        assert "data-testid" in candidate.selector


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
