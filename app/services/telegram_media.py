import asyncio
import logging
import uuid
from pathlib import Path

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TELEGRAM_API_BASE = "https://api.telegram.org"
_UPLOADS_DIR = Path("app/uploads")

# Audio formats Telegram can send that ffmpeg must convert to wav.
# Whisper accepts wav/mp3/m4a/ogg natively, but we normalise everything
# to wav so the speech_service never needs to know the source format.
_AUDIO_FORMATS_NEEDING_CONVERSION = {".ogg", ".oga", ".m4a", ".mp4", ".webm", ".flac", ".aac"}


def _ensure_uploads_dir() -> None:
    """Create uploads directory if it does not exist."""
    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_file_path(file_id: str) -> str:
    """
    Call Telegram getFile to resolve a file_id into a downloadable CDN path.
    Returns the file_path string reported by Telegram (e.g. 'music/file_1.m4a').
    """
    url = f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/getFile"

    logger.info("Resolving Telegram file_id=%s", file_id)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params={"file_id": file_id})

    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram getFile failed: status={response.status_code} body={response.text}"
        )

    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram getFile returned ok=false: {body}")

    file_path: str = body["result"]["file_path"]
    logger.info("Resolved file_id=%s -> file_path=%s", file_id, file_path)
    return file_path


async def _download_raw(file_path: str, dest: Path) -> None:
    """
    Stream-download a file from the Telegram CDN into dest.
    Streams in 8 KB chunks — safe for large audio/video files.
    """
    download_url = (
        f"{_TELEGRAM_API_BASE}/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
    )

    logger.info("Downloading from Telegram CDN -> %s", dest)

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("GET", download_url) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"Telegram CDN download failed: status={response.status_code}"
                )

            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)

    logger.info("Download complete -> %s (%d bytes)", dest, dest.stat().st_size)


async def _convert_to_wav(src: Path) -> Path:
    """
    Convert any audio file to a 16 kHz mono WAV using ffmpeg.

    Works for: .ogg, .oga, .m4a, .mp4, .webm, .flac, .aac, .mp3
    The source file is preserved — caller decides whether to remove it.

    Raises RuntimeError if ffmpeg is not installed or conversion fails.
    """
    wav_path = src.with_suffix(".wav")

    cmd = [
        "ffmpeg",
        "-y",                    # overwrite output without asking
        "-i", str(src),          # input file (actual format detected by ffmpeg, not extension)
        "-ar", "16000",          # 16 kHz — optimal for Whisper
        "-ac", "1",              # mono
        "-sample_fmt", "s16",    # 16-bit PCM
        str(wav_path),
    ]

    logger.info("Converting audio: %s -> %s", src, wav_path)
    logger.info("ffmpeg command: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg conversion failed (exit {proc.returncode}): {stderr.decode()}"
        )

    logger.info("Conversion complete -> %s (%d bytes)", wav_path, wav_path.stat().st_size)
    return wav_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def download_file(file_id: str) -> Path:
    """
    Resolve a Telegram file_id and download it to uploads/.
    Preserves the original file extension reported by Telegram.
    Returns the local Path of the downloaded file.
    """
    _ensure_uploads_dir()

    file_path = await _get_file_path(file_id)
    suffix = Path(file_path).suffix or ".bin"
    local_path = _UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"

    await _download_raw(file_path, local_path)
    return local_path


async def download_photo(file_id: str) -> Path:
    """
    Download the largest Telegram photo to uploads/.
    The caller must pass the file_id of the largest PhotoSize (last in the array).
    Returns the local image path.
    """
    _ensure_uploads_dir()

    logger.info("Downloading photo file_id=%s", file_id)

    file_path = await _get_file_path(file_id)
    suffix = Path(file_path).suffix or ".jpg"
    local_path = _UPLOADS_DIR / f"photo_{uuid.uuid4().hex}{suffix}"

    await _download_raw(file_path, local_path)
    logger.info("Photo saved -> %s", local_path)
    return local_path


async def download_and_convert_audio(file_id: str) -> Path:
    """
    Download any Telegram audio (voice, audio, video_note) and return a WAV path.

    Handles every format Telegram can send:
      • voice     → always .ogg (Opus)
      • audio     → .m4a, .mp3, .flac, .ogg, etc.
      • video_note → .mp4

    Steps:
      1. Resolve file_id → CDN path (preserving real extension).
      2. Download raw file to uploads/ with its real extension.
      3. If already .wav, return as-is.
      4. Otherwise convert to 16 kHz mono WAV via ffmpeg.
      5. Remove the intermediate source file to save disk space.

    Returns the .wav path ready for speech_service.transcribe().
    """
    _ensure_uploads_dir()

    logger.info("Downloading audio file_id=%s", file_id)

    # Step 1 — resolve to CDN path, preserving the REAL extension
    cdn_path = await _get_file_path(file_id)
    real_suffix = Path(cdn_path).suffix.lower() or ".ogg"

    uid = uuid.uuid4().hex

    # Step 2 — download with correct extension so ffmpeg can detect format
    raw_path = _UPLOADS_DIR / f"audio_{uid}{real_suffix}"
    await _download_raw(cdn_path, raw_path)
    logger.info("Audio downloaded -> %s (format=%s)", raw_path, real_suffix)

    # Step 3 — if already wav, return immediately
    if real_suffix == ".wav":
        return raw_path

    # Step 4 — convert to wav
    try:
        wav_path = await _convert_to_wav(raw_path)
    except Exception as exc:
        raw_path.unlink(missing_ok=True)
        logger.error("Audio conversion failed for %s: %s", raw_path, exc)
        raise

    # Step 5 — clean up intermediate file
    raw_path.unlink(missing_ok=True)
    return wav_path