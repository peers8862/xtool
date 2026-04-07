import sys
from pathlib import Path


def get_chrome_base() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif sys.platform.startswith("linux"):
        # check both google-chrome and chromium locations
        for candidate in [
            Path.home() / ".config" / "google-chrome",
            Path.home() / ".config" / "chromium",
        ]:
            if candidate.exists():
                return candidate
        return Path.home() / ".config" / "google-chrome"
    elif sys.platform == "win32":
        import os
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    else:
        raise OSError(f"Unsupported platform: {sys.platform}")


def get_screen_size() -> tuple[int, int]:
    if sys.platform == "darwin":
        from AppKit import NSScreen
        frame = NSScreen.mainScreen().frame()
        return int(frame.size.width), int(frame.size.height)
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            out = subprocess.check_output(["xrandr"]).decode()
            for line in out.splitlines():
                if " connected primary" in line or (" connected" in line and "primary" not in out):
                    import re
                    m = re.search(r"(\d+)x(\d+)", line)
                    if m:
                        return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return 1920, 1080  # sensible fallback
    elif sys.platform == "win32":
        import ctypes
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    else:
        return 1920, 1080
