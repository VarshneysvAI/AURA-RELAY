"""
AURA Relay Security Manager - Secret handling and redaction
"""
from typing import Any, Dict, Optional
from .otp import redact_secret, mask_otp


class SecurityManager:
    """Handles secret masking and redaction for logs and UI."""
    
    def __init__(self, redact_secrets: bool = True):
        self.redact_secrets = redact_secrets
        self._secrets: Dict[str, str] = {}
    
    def register_secret(self, name: str, value: str) -> None:
        """Register a secret value for automatic redaction."""
        if value:
            self._secrets[name] = value
    
    def redact(self, text: str) -> str:
        """Redact all registered secrets from text."""
        if not self.redact_secrets:
            return text
        
        result = text
        for name, value in self._secrets.items():
            if value and len(value) > 3:
                result = result.replace(value, redact_secret(value))
        return result
    
    def redact_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Redact secrets from a dictionary."""
        if not self.redact_secrets:
            return data
        
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.redact(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.redact(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                result[key] = value
        
        return result
    
    def mask_password(self, password: str) -> str:
        """Mask a password for display."""
        if not self.redact_secrets:
            return password
        return mask_otp(password, show_last=1)
    
    def mask_otp(self, otp: str) -> str:
        """Mask an OTP code for display."""
        if not self.redact_secrets:
            return otp
        return mask_otp(otp)
    
    def clear(self) -> None:
        """Clear all registered secrets."""
        self._secrets.clear()
