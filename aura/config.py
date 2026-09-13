"""
AURA Relay Configuration Manager
"""
import os
from typing import Optional
from pydantic import BaseModel, Field


class Config(BaseModel):
    """Application configuration loaded from environment variables."""
    
    mode: str = Field(default="sandbox", description="sandbox or live")
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    base_url: str = Field(default="http://127.0.0.1:8000")
    
    headless: bool = Field(default=False)
    slow_mo: int = Field(default=250)
    screenshot_after_each_action: bool = Field(default=True)
    redact_secrets: bool = Field(default=True)
    
    target_url: Optional[str] = Field(default=None)
    target_username: Optional[str] = Field(default=None)
    target_password: Optional[str] = Field(default=None)
    
    email_provider: str = Field(default="local")
    email_poll_interval_seconds: int = Field(default=1)
    email_timeout_seconds: int = Field(default=30)
    
    imap_host: Optional[str] = Field(default=None)
    imap_port: int = Field(default=993)
    imap_user: Optional[str] = Field(default=None)
    imap_password: Optional[str] = Field(default=None)
    imap_folder: str = Field(default="INBOX")
    
    email_http_url: Optional[str] = Field(default=None)
    email_http_api_key: Optional[str] = Field(default=None)
    email_sender_filter: Optional[str] = Field(default=None)
    email_subject_filter: Optional[str] = Field(default=None)
    
    openai_api_key: Optional[str] = Field(default=None)
    anakin_api_key: Optional[str] = Field(default=None)
    anakin_base_url: str = Field(default="https://api.anakin.io/v1")
    
    nvidia_llm_api_key: Optional[str] = Field(default=None)
    nvidia_llm_model: str = Field(default="openai/gpt-oss-20b")
    nvidia_llm_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    groq_api_key: Optional[str] = Field(default=None)
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    nvidia_tts_api_key: Optional[str] = Field(default=None)
    nvidia_tts_voice: str = Field(default="Magpie-Multilingual.EN-US.Aria")
    nvidia_tts_base_url: str = Field(default="https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com/v1/audio/synthesize")
    
    @property
    def is_sandbox(self) -> bool:
        return self.mode == "sandbox"
    
    @property
    def is_live(self) -> bool:
        return self.mode == "live"
    
    @classmethod
    def load(cls) -> "Config":
        """Load configuration from environment variables and .env file."""
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass
        return cls(
            mode=os.getenv("MODE", "sandbox"),
            app_host=os.getenv("APP_HOST", "127.0.0.1"),
            app_port=int(os.getenv("APP_PORT", "8000")),
            base_url=os.getenv("BASE_URL", "http://127.0.0.1:8000"),
            headless=os.getenv("HEADLESS", "false").lower() == "true",
            slow_mo=int(os.getenv("SLOW_MO", "250")),
            screenshot_after_each_action=os.getenv("SCREENSHOT_AFTER_EACH_ACTION", "true").lower() == "true",
            redact_secrets=os.getenv("REDACT_SECRETS", "true").lower() == "true",
            target_url=os.getenv("TARGET_URL") or None,
            target_username=os.getenv("TARGET_USERNAME") or None,
            target_password=os.getenv("TARGET_PASSWORD") or None,
            email_provider=os.getenv("EMAIL_PROVIDER", "local"),
            email_poll_interval_seconds=int(os.getenv("EMAIL_POLL_INTERVAL_SECONDS", "1")),
            email_timeout_seconds=int(os.getenv("EMAIL_TIMEOUT_SECONDS", "30")),
            imap_host=os.getenv("IMAP_HOST") or None,
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            imap_user=os.getenv("IMAP_USER") or None,
            imap_password=os.getenv("IMAP_PASSWORD") or None,
            imap_folder=os.getenv("IMAP_FOLDER", "INBOX"),
            email_http_url=os.getenv("EMAIL_HTTP_URL") or None,
            email_http_api_key=os.getenv("EMAIL_HTTP_API_KEY") or None,
            email_sender_filter=os.getenv("EMAIL_SENDER_FILTER") or None,
            email_subject_filter=os.getenv("EMAIL_SUBJECT_FILTER") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            anakin_api_key=os.getenv("ANAKIN_API_KEY") or None,
            anakin_base_url=os.getenv("ANAKIN_BASE_URL", "https://api.anakin.io/v1"),
            nvidia_llm_api_key=os.getenv("NVIDIA_LLM_API_KEY") or None,
            nvidia_llm_model=os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
            nvidia_llm_base_url=os.getenv("NVIDIA_LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            nvidia_tts_api_key=os.getenv("NVIDIA_TTS_API_KEY") or None,
            nvidia_tts_voice=os.getenv("NVIDIA_TTS_VOICE", "Magpie-Multilingual.EN-US.Aria"),
            nvidia_tts_base_url=os.getenv("NVIDIA_TTS_BASE_URL", "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com/v1/audio/synthesize"),
        )


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config() -> Config:
    """Reload configuration from environment."""
    global _config
    _config = Config.load()
    return _config
