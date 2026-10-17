"""Read the bundled, prerecorded Live voice cue without provider access."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import wave


MAX_CUE_FILE_BYTES = 160_000
MAX_CUE_PCM_BYTES = 3 * 24_000 * 2


@lru_cache(maxsize=8)
def _validated_pcm(path: Path, modified_ns: int, size: int) -> bytes | None:
    if size > MAX_CUE_FILE_BYTES:
        return None
    try:
        with wave.open(str(path), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                    audio.getframerate() != 24_000 or audio.getcomptype() != 'NONE' or
                    not 0 < frames <= 3 * 24_000):
                return None
            pcm = audio.readframes(frames + 1)
    except (OSError, EOFError, ValueError, wave.Error):
        return None
    return pcm if len(pcm) == frames * 2 and len(pcm) <= MAX_CUE_PCM_BYTES else None


def load_cue_pcm(path: Path) -> bytes | None:
    """Return validated mono PCM16, or omit an absent/invalid cue safely."""
    try:
        path = Path(path)
        stat = path.stat()
    except (OSError, ValueError, TypeError):
        return None
    return _validated_pcm(path, stat.st_mtime_ns, stat.st_size)
