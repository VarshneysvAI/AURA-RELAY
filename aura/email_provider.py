"""
AURA Relay Email Provider Interface and Implementations
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class Email:
    """Represents an email message."""
    sender: str
    subject: str
    body: str
    timestamp: str
    otp_code: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "subject": self.subject,
            "body": self.body,
            "timestamp": self.timestamp,
            "otp_code": self.otp_code
        }


class EmailProvider(ABC):
    """Abstract interface for email providers."""
    
    @abstractmethod
    def wait_for_email(self, since_timestamp: str, timeout_seconds: int = 30) -> Optional[Email]:
        """Wait for a new email arriving after the given timestamp."""
        pass
    
    @abstractmethod
    def get_inbox(self) -> List[Email]:
        """Get all emails in the inbox."""
        pass
    
    @abstractmethod
    def clear_inbox(self) -> None:
        """Clear the inbox."""
        pass


class LocalEmailProvider(EmailProvider):
    """
    Local email provider for sandbox mode.
    Stores emails in memory and provides them via API.
    """
    
    def __init__(self):
        self._emails: List[Email] = []
        self._lock = __import__('threading').RLock()
    
    def add_email(self, email: Email) -> None:
        """Add an email to the inbox (called by sandbox API)."""
        with self._lock:
            self._emails.append(email)
    
    def wait_for_email(self, since_timestamp: str, timeout_seconds: int = 30) -> Optional[Email]:
        """Wait for a new email."""
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            with self._lock:
                for email in self._emails:
                    if email.timestamp > since_timestamp:
                        return email
            
            time.sleep(0.5)
        
        return None
    
    def get_inbox(self) -> List[Email]:
        """Get all emails."""
        with self._lock:
            return self._emails.copy()
    
    def clear_inbox(self) -> None:
        """Clear all emails."""
        with self._lock:
            self._emails.clear()
    
    def get_latest_otp(self) -> Optional[str]:
        """Get the latest OTP from emails."""
        with self._lock:
            if not self._emails:
                return None
            
            # Get most recent email
            latest = max(self._emails, key=lambda e: e.timestamp)
            
            # Extract OTP using regex
            import re
            match = re.search(r'\b\d{4,8}\b', latest.body)
            if match:
                return match.group()
            
            return None


class ImapEmailProvider(EmailProvider):
    """
    IMAP email provider for real email access.
    Currently a stub - requires valid IMAP credentials.
    """
    
    def __init__(self, host: str, port: int, user: str, password: str, folder: str = "INBOX"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.folder = folder
        self._connected = False
    
    def _connect(self):
        """Establish IMAP connection."""
        try:
            import imaplib
            self._mail = imaplib.IMAP4_SSL(self.host, self.port)
            self._mail.login(self.user, self.password)
            self._mail.select(self.folder)
            self._connected = True
        except Exception as e:
            raise RuntimeError(f"Failed to connect to IMAP server: {e}")
    
    def wait_for_email(self, since_timestamp: str, timeout_seconds: int = 30) -> Optional[Email]:
        """Wait for a new email via IMAP."""
        if not self._connected:
            self._connect()
        
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                emails = self.get_inbox()
                if emails:
                    # Find email newer than since_timestamp
                    for email in reversed(emails):
                        if email.timestamp > since_timestamp:
                            return email
            except Exception:
                pass
            
            time.sleep(2)
        
        return None
    
    def get_inbox(self) -> List[Email]:
        """Get emails from IMAP inbox."""
        if not self._connected:
            self._connect()
        
        emails = []
        try:
            _, message_ids = self._mail.search(None, 'ALL')
            ids = message_ids[0].split()
            
            for msg_id in ids[-10:]:  # Last 10 emails
                _, msg_data = self._mail.fetch(msg_id, '(RFC822)')
                # Parse email (simplified)
                raw_email = msg_data[0][1]
                # In production, use email.parser here
                emails.append(Email(
                    sender="unknown",
                    subject="unknown",
                    body=raw_email.decode('utf-8', errors='ignore'),
                    timestamp=datetime.utcnow().isoformat() + "Z"
                ))
        except Exception as e:
            raise RuntimeError(f"Failed to fetch emails: {e}")
        
        return emails
    
    def clear_inbox(self) -> None:
        """IMAP doesn't support clearing inbox safely - no-op."""
        pass


class HttpEmailProvider(EmailProvider):
    """
    HTTP API email provider for external email services.
    Stub implementation for future API integration.
    """
    
    def __init__(self, base_url: str, api_key: str, sender_filter: Optional[str] = None, subject_filter: Optional[str] = None):
        self.base_url = base_url
        self.api_key = api_key
        self.sender_filter = sender_filter
        self.subject_filter = subject_filter
    
    def wait_for_email(self, since_timestamp: str, timeout_seconds: int = 30) -> Optional[Email]:
        """Wait for email via HTTP API."""
        import httpx
        
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                emails = self.get_inbox()
                if emails:
                    for email in reversed(emails):
                        if email.timestamp > since_timestamp:
                            return email
            except Exception:
                pass
            
            time.sleep(1)
        
        return None
    
    def get_inbox(self) -> List[Email]:
        """Fetch inbox from HTTP API."""
        import httpx
        
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {}
        
        if self.sender_filter:
            params["sender"] = self.sender_filter
        if self.subject_filter:
            params["subject"] = self.subject_filter
        
        try:
            response = httpx.get(f"{self.base_url}/inbox", headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            emails = []
            for item in data.get("emails", []):
                emails.append(Email(
                    sender=item.get("from", ""),
                    subject=item.get("subject", ""),
                    body=item.get("body", ""),
                    timestamp=item.get("timestamp", "")
                ))
            
            return emails
        except Exception as e:
            raise RuntimeError(f"Failed to fetch emails from HTTP API: {e}")
    
    def clear_inbox(self) -> None:
        """Clear inbox via HTTP API."""
        import httpx
        
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            httpx.post(f"{self.base_url}/inbox/clear", headers=headers)
        except Exception:
            pass
