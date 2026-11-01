"""Per-request speech attempt tracking, including tool tasks spawned by an agent."""
from contextvars import ContextVar

speech_attempt: ContextVar[dict | None] = ContextVar('sentient_speech_attempt', default=None)
ANNOUNCE_TIMEOUT = 12
BACKUP_TIMEOUT = 15
