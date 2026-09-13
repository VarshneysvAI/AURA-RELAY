"""
AURA Relay OTP Extraction and Security Utilities
"""
import re
from typing import Optional, Tuple


def extract_otp(text: str) -> Optional[str]:
    """
    Extract OTP code from email text.
    
    Strategy:
    1. Extract all 4-8 digit numbers as candidate codes.
    2. Filter out calendar years (1990-2035) unless they are the only match.
    3. Prefer 6-digit codes (industry standard for OTP / 2FA verification).
       Check if any 6-digit candidate is keyword-adjacent; otherwise return the first 6-digit candidate.
    4. Fall back to keyword-adjacent 4-8 digit codes.
    5. Fall back to the first valid candidate.
    """
    if not text:
        return None

    # Find all 4-8 digit numbers
    pattern = r'\b\d{4,8}\b'
    matches = re.findall(pattern, text)
    if not matches:
        return None

    # Filter out calendar years (1990-2035) unless it's the only match
    valid_candidates = [m for m in matches if not (len(m) == 4 and 1990 <= int(m) <= 2035)]
    if not valid_candidates:
        valid_candidates = matches

    # Check for explicit keyword adjacency:
    keyword_patterns = [
        r"(?i)(?:otp|code|verification|passcode|one-time|pin|password)[\s:=–-]+([0-9]{4,8})\b",
        r"(?i)\b([0-9]{4,8})\b[\s]+(?:is your (?:otp|verification|code|one-time))",
        r"(?i)(?:enter|use|input)[\s]+([0-9]{4,8})\b",
    ]
    kw_matches = []
    for kw_pat in keyword_patterns:
        for m in re.finditer(kw_pat, text):
            kw_matches.append(m.group(1))

    # Priority 1: 6-digit candidate adjacent to security keywords
    six_digit_kw = [m for m in kw_matches if len(m) == 6]
    if six_digit_kw:
        return six_digit_kw[0]

    # Priority 2: Any valid 6-digit candidate (e.g. "Code 1234 or 123456")
    six_digit = [m for m in valid_candidates if len(m) == 6]
    if six_digit:
        return six_digit[0]

    # Priority 3: Any other candidate adjacent to keywords
    valid_kw = [m for m in kw_matches if m in valid_candidates]
    if valid_kw:
        return valid_kw[0]

    # Priority 4: First valid candidate
    return valid_candidates[0]


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
