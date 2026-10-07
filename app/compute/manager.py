"""
Compute manager — controls the GPU PC (Ollama inference node).

Responsibilities:
  - Wake the PC via TravelNet's /compute/wake endpoint (same Docker network)
  - Poll SSH to track whether the PC is actually online
  - Shut down the PC via TravelNet's /compute/shutdown endpoint
  - Track last chat timestamp and shut down after inactivity

Architecture note:
  Wake/shutdown calls go to TravelNet's ingest service (http://ingest:8000)
  rather than directly to the WoL/SSH layer, since TravelNet already owns
  that logic. Trevor just calls the API. SSH polling is duplicated here
  because Trevor needs to know PC state independently (e.g. before an
  Ollama call) without depending on TravelNet being the source of truth
  at query time.

TODO (frontend): Option B (wake-and-reject) is implemented here.
  When a frontend is built, replace the 503 response in ollama.py with
  an SSE stream that emits status events while polling for PC readiness,
  then streams the LLM response once the PC is online.
  Relevant files: app/llm/ollama.py, app/api/chat.py
"""

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import paramiko

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

pc_active: bool = False
last_chat_at: datetime | None = None
_poll_thread: threading.Thread | None = None
_inactivity_thread: threading.Thread | None = None

_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_ssh_client() -> paramiko.SSHClient:
    """Connect to the compute host.

    The private key at COMPUTE_SSH_KEY_PATH is offered first and the password only as a fallback
    (paramiko's order). With COMPUTE_KNOWN_HOSTS_PATH set, only the pinned host key is accepted
    (unknown or changed keys raise before any credentials are sent); otherwise the previous
    trust-on-first-use behaviour is kept.
    """
    client = paramiko.SSHClient()

    known_hosts = settings.compute_known_hosts_path
    if known_hosts and Path(known_hosts).is_file():
        client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs = dict(
        port=settings.compute_port,
        username=settings.compute_username,
        timeout=5,
        allow_agent=False,
        look_for_keys=False,
    )
    key_path = settings.compute_ssh_key_path
    if key_path and Path(key_path).is_file():
        kwargs["key_filename"] = key_path
    if settings.compute_password:
        kwargs["password"] = settings.compute_password
    if "key_filename" not in kwargs and "password" not in kwargs:
        raise RuntimeError("No SSH credentials for the compute host (set COMPUTE_SSH_KEY_PATH or COMPUTE_PASSWORD)")

    client.connect(settings.compute_host, **kwargs)
    return client


def _poll_ssh(interval: int = 10) -> None:
    """
    Background thread: polls SSH every `interval` seconds and updates pc_active.
    Mirrors the polling logic in TravelNet's compute/util.py.
    Notifications are omitted here — TravelNet handles those.
    """
    global pc_active
    previous_state = None

    while True:
        try:
            client = _get_ssh_client()
            client.close()
            current_state = True
        except Exception:
            current_state = False

        if current_state != previous_state:
            logger.info(f"PC state changed: {'online' if current_state else 'offline'}")

        with _state_lock:
            pc_active = current_state

        previous_state = current_state
        time.sleep(interval)


def _inactivity_watcher() -> None:
    """
    Background thread: shuts the PC down after COMPUTE_INACTIVITY_TIMEOUT
    seconds of no chat activity.

    Only triggers if:
      - The PC is currently active
      - At least one chat has been received (last_chat_at is set)
      - No chat has been received within the timeout window
    """
    while True:
        time.sleep(60)  # check every minute

        with _state_lock:
            active = pc_active
            last = last_chat_at

        if not active or last is None:
            continue

        idle_seconds = (datetime.now(timezone.utc) - last).total_seconds()
        if idle_seconds >= settings.compute_inactivity_timeout:
            logger.info(
                f"Compute idle for {idle_seconds:.0f}s "
                f"(threshold: {settings.compute_inactivity_timeout}s) — shutting down"
            )
            shutdown_pc()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_chat() -> None:
    """Call this on every successful /chat request to reset the inactivity timer."""
    global last_chat_at
    with _state_lock:
        last_chat_at = datetime.now(timezone.utc)


def is_pc_active() -> bool:
    with _state_lock:
        return pc_active


def wake_pc() -> None:
    """
    Send a wake request to TravelNet and start the SSH polling thread.
    Trevor calls TravelNet's endpoint rather than sending the magic packet
    directly — TravelNet owns the WoL/SSH layer.
    """
    global _poll_thread

    try:
        with httpx.Client(timeout=5) as client:
            client.post(
                f"{settings.travelnet_url}/compute/wake",
                headers={"Authorization": f"Bearer {settings.travelnet_api_key}"},
            )
        logger.info("Wake request sent to TravelNet")
    except Exception as e:
        logger.warning(f"Wake request failed: {e}")

    # Start polling thread if not already running
    if _poll_thread is None or not _poll_thread.is_alive():
        _poll_thread = threading.Thread(target=_poll_ssh, daemon=True)
        _poll_thread.start()
        logger.info("SSH polling thread started")


def shutdown_pc() -> None:
    """
    Send a shutdown request to TravelNet and mark the PC as inactive locally.
    """
    global pc_active

    try:
        with httpx.Client(timeout=5) as client:
            client.post(
                f"{settings.travelnet_url}/compute/shutdown",
                headers={"Authorization": f"Bearer {settings.travelnet_api_key}"},
            )
        logger.info("Shutdown request sent to TravelNet")
    except Exception as e:
        logger.warning(f"Shutdown request failed: {e}")

    with _state_lock:
        pc_active = False


def start_background_tasks() -> None:
    """
    Called once at application startup (from main.py lifespan).
    Starts the inactivity watcher. Polling starts only after a wake call.
    """
    global _inactivity_thread

    _inactivity_thread = threading.Thread(target=_inactivity_watcher, daemon=True)
    _inactivity_thread.start()
    logger.info(
        f"Inactivity watcher started "
        f"(timeout: {settings.compute_inactivity_timeout}s)"
    )