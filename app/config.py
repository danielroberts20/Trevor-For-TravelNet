"""
Central configuration. All environment variables are read here.
Other modules import `settings` from this file rather than reading os.environ directly.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # LLM provider selection
    llm_provider: str = Field(alias="LLM_PROVIDER")  # "ollama" | "openai" | "anthropic"

    # Ollama
    ollama_base_url: str = Field(alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(alias="OLLAMA_MODEL")
    ollama_embed_model: str = Field(alias="OLLAMA_EMBED_MODEL")

    # OpenAI
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_model: str = Field(alias="OPENAI_MODEL")

    # Anthropic
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(alias="ANTHROPIC_MODEL")

    # Storage paths (inside container)
    db_path: str = Field(alias="DB_PATH")
    chroma_path: str = Field(alias="CHROMA_PATH")

    # Ingestion filter
    travel_start_date: str = Field(alias="TRAVEL_START_DATE")

    # TravelNet API (same Docker network — use service name, not Tailscale hostname)
    travelnet_url: str = Field(alias="TRAVELNET_URL")
    travelnet_api_key: str = Field(alias="TRAVELNET_API_KEY")
 
    # Compute node SSH (used by Trevor for polling PC state only)
    compute_host: str = Field(alias="COMPUTE_HOST")
    compute_port: int = Field(alias="COMPUTE_PORT")
    compute_username: str = Field(alias="COMPUTE_USERNAME")
    # Prefer a key (COMPUTE_SSH_KEY_PATH); the password is only a fallback while migrating.
    compute_password: Optional[str] = Field(default=None, alias="COMPUTE_PASSWORD")
    compute_ssh_key_path: Optional[str] = Field(default=None, alias="COMPUTE_SSH_KEY_PATH")
    # known_hosts file (from `ssh-keyscan -p PORT HOST`): unknown or changed host keys are then refused.
    compute_known_hosts_path: Optional[str] = Field(default=None, alias="COMPUTE_KNOWN_HOSTS_PATH")
 
    # Seconds of chat inactivity before the PC is automatically shut down
    compute_inactivity_timeout: int = Field(alias="COMPUTE_INACTIVITY_TIMEOUT")

    # API security
    trevor_api_key: str = Field(alias="TREVOR_API_KEY")

    travel_yml_path: Path = Field(alias="TRAVEL_YML_PATH")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
