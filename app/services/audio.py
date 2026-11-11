"""WhatsApp voice notes are Ogg/Opus; browsers record WebM/Opus (or MP4). Convert when a converter exists."""
import logging
import shutil
import subprocess

log = logging.getLogger("relay.audio")


def ffmpeg_path() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # the pip package ships a static ffmpeg
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def to_ogg_opus(data: bytes) -> bytes | None:
    """Mono 48 kHz Opus in an Ogg container, or None if no converter is available / it failed."""
    exe = ffmpeg_path()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn", "-c:a", "libopus", "-b:a", "32k",
             "-ac", "1", "-ar", "48000", "-f", "ogg", "pipe:1"],
            input=data, capture_output=True, timeout=60,
        )
    except Exception:
        log.exception("Audio conversion crashed")
        return None
    if proc.returncode != 0 or not proc.stdout:
        log.warning("Audio conversion failed: %s", proc.stderr[:200])
        return None
    return proc.stdout
