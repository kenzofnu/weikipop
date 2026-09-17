# src/utils/audio_capture.py
"""
Rolling system-audio capture (Windows WASAPI loopback).

A background thread continuously records whatever is playing on the default
output device into an in-memory ring buffer holding the last N seconds. When a
card is mined, `grab_wav()` slices the most recent audio out of that buffer so
the line that was *just* spoken (drama / anime / game) can be attached to Anki.

Windows-only for now: it relies on `soundcard`'s WASAPI loopback support.
The whole module degrades gracefully — if `soundcard`/`numpy` are missing or no
output device is available, `available()` is False and the recorder is a no-op.
"""
import logging
import threading
import time
import wave
from collections import deque
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import soundcard as sc
    _IMPORT_ERROR: Optional[Exception] = None
except Exception as e:  # pragma: no cover - import guard
    np = None          # type: ignore
    sc = None          # type: ignore
    _IMPORT_ERROR = e


class SystemAudioRecorder:
    """Continuously records system output into a rolling buffer."""

    def __init__(self, samplerate: int = 48000, channels: int = 2,
                 buffer_seconds: float = 30.0, blocksize: int = 4800):
        self.samplerate     = samplerate
        self.channels       = channels
        self.buffer_seconds = buffer_seconds
        self.blocksize      = blocksize
        self._max_frames    = int(samplerate * buffer_seconds)
        self._chunks: deque = deque()
        self._buffered_frames = 0
        self._lock          = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running       = False

    @staticmethod
    def available() -> bool:
        return _IMPORT_ERROR is None

    def start(self) -> bool:
        """Idempotent. Returns True if the capture thread is (now) running."""
        if not self.available():
            logger.warning("System audio capture unavailable: %s", _IMPORT_ERROR)
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="SystemAudioRecorder", daemon=True
        )
        self._thread.start()
        logger.info("System audio capture started (%.0fs buffer)", self.buffer_seconds)
        return True

    def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def _run(self) -> None:
        # Reconnect loop — the default output device can change (headphones,
        # HDMI, etc.). On any error we back off and re-acquire the loopback mic.
        while self._running:
            try:
                speaker = sc.default_speaker()
                loopback = sc.get_microphone(
                    id=str(speaker.name), include_loopback=True
                )
                with loopback.recorder(
                    samplerate=self.samplerate,
                    channels=self.channels,
                    blocksize=self.blocksize,
                ) as rec:
                    while self._running:
                        data = rec.record(numframes=self.blocksize)
                        if data is not None and len(data):
                            self._append(data)
            except Exception as e:
                if self._running:
                    logger.error("Audio capture error (%s); retrying in 2s", e)
                    self._clear()
                    time.sleep(2)

    def _append(self, data) -> None:
        with self._lock:
            self._chunks.append(data)
            self._buffered_frames += data.shape[0]
            while (self._buffered_frames > self._max_frames
                   and len(self._chunks) > 1):
                removed = self._chunks.popleft()
                self._buffered_frames -= removed.shape[0]

    def _clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._buffered_frames = 0

    def grab_wav(self, duration: float = 5.0, offset: float = 0.0,
                 trim_silence: bool = True, silence_threshold: float = 0.006,
                 tail_pad: float = 0.2) -> Optional[bytes]:
        """
        Return 16-bit PCM WAV bytes of recent system audio.

        Without trimming the window is [now - offset - duration, now - offset].

        With `trim_silence` (the default), the window instead *ends* at the last
        real sound in the buffer (plus `tail_pad` seconds), skipping any trailing
        silence. This nails the "pause, then mine" workflow: it doesn't matter
        how long after pausing you press mine — the clip snaps to the dialogue
        that played just before the pause rather than capturing the silent gap.

        Returns None if no audio is buffered.
        """
        if not self.available():
            return None
        with self._lock:
            if not self._chunks:
                return None
            audio = np.concatenate(list(self._chunks), axis=0)

        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        total = audio.shape[0]
        ch    = audio.shape[1]

        end = total - int(offset * self.samplerate)
        if trim_silence:
            # Anchor the clip end to the last frame above the silence floor.
            amp = np.max(np.abs(audio), axis=1)
            voiced = np.nonzero(amp > silence_threshold)[0]
            if voiced.size:
                end = min(total, int(voiced[-1]) + int(tail_pad * self.samplerate))
        start = end - int(duration * self.samplerate)
        start = max(0, start)
        end   = max(start, min(end, total))
        clip  = audio[start:end]
        if clip.shape[0] == 0:
            return None

        pcm = np.clip(clip, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        bio = BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(self.samplerate)
            w.writeframes(pcm.tobytes())
        return bio.getvalue()


# Module-level singleton — started from main.py / settings when enabled.
recorder = SystemAudioRecorder()
