"""Payload budgets beneath the runtime's 8 MiB JSON file/channel ceiling."""

WORLD_BYTES = 2 * 1024 * 1024
STEP_BYTES = 4 * 1024 * 1024
ARTIFACT_BYTES = 1024 * 1024
ACTIVE_CHECKPOINT_BYTES = 4 * 1024 * 1024
PUBLICATION_CHECKPOINT_BYTES = 7 * 1024 * 1024
# Terminal publication contains artifacts in both result and outbox. Reserve
# their two embedded copies before accepting authoritative state changes.
TERMINAL_BASE_BYTES = PUBLICATION_CHECKPOINT_BYTES - 2 * ARTIFACT_BYTES
ERROR_CHARACTERS = 4096
