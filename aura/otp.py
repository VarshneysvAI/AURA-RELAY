"""
AURA Relay OTP Extraction and Security Utilities
"""
import re
from typing import Optional, Tuple


def extract_otp(text: str) -> Optional[str]:
    """
    Extract OTP code from email text.
    
    Strategy:
    1. Look for 6-digit codes first (most common)
    2. Fall back to 4-8 digit codes
    3. Prefer codes near keywords like "OTP", "code", "verification"
    
    Returns the OTP code or None if not found.
    """
    if not text:
        return None
    
    # Pattern for 4-8 digit codes
    pattern = r'\b\d{4,8}\b'
    matches = re.findall(pattern, text)
    
    if not matches:
        return None
    
    # Prefer 6-digit codes
    six_digit = [m for m in matches if len(m) == 6]
    if six_digit:
        return six_digit[0]
    
    # Return first match otherwise
    return matches[0]


def mask_otp(otp: str, show_last: int = 2) -> str:
    """
    Mask OTP code for display, showing only last few digits.
    
    Args:
        otp: The OTP code to mask
        show_last: Number of digits to show at the end
    
    Returns:
        Masked OTP string (e.g., "****56")
    """
    if not otp:
        return ""
    
    if len(otp) <= show_last:
        return "*" * len(otp)
    
    masked_len = len(otp) - show_last
    return "*" * masked_len + otp[-show_last:]


def redact_secret(value: str, show_chars: int = 4) -> str:
    """
    Redact a secret value for logging.
    
    Args:
        value: The secret value to redact
        show_chars: Number of characters to show at start
    
    Returns:
        Redacted string (e.g., "pass****")
    """
    if not value:
        return ""
    
    if len(value) <= show_chars:
        return "*" * len(value)
    
    shown = value[:show_chars]
    hidden_len = len(value) - show_chars
    return shown + "*" * hidden_len


def is_otp_related_text(text: str) -> bool:
    """
    Check if text is likely to contain OTP information.
    
    Looks for keywords commonly associated with OTP emails.
    """
    if not text:
        return False
    
    text_lower = text.lower()
    keywords = [
        "otp",
        "one-time",
        "verification code",
        "verify your",
        "security code",
        "login code",
        "authentication code",
        "confirm your",
        "your code is"
    ]
    
    return any(keyword in text_lower for keyword in keywords)


def parse_email_for_otp(subject: str, body: str) -> Tuple[Optional[str], bool]:
    """
    Parse email subject and body to extract OTP.
    
    Returns:
        Tuple of (otp_code, is_otp_email)
    """
    combined = f"{subject} {body}"
    
    # Check if this looks like an OTP email
    if not is_otp_related_text(combined):
        return None, False
    
    # Extract OTP
    otp = extract_otp(body) or extract_otp(subject)
    
    return otp, otp is not None
