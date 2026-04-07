import json
from pathlib import Path

from core.platform import get_chrome_base

CONFIG_FILE = Path(__file__).parent.parent / "config.py"


def _detect_profiles():
    """Return list of (folder_name, display_name, full_path) for all Chrome profiles."""
    try:
        CHROME_BASE = get_chrome_base()
    except OSError as e:
        print(f"ERROR: {e}")
        return []

    profiles = []
    local_state = CHROME_BASE / "Local State"
    display_names = {}

    if local_state.exists():
        try:
            state = json.loads(local_state.read_text(encoding="utf-8"))
            info  = state.get("profile", {}).get("info_cache", {})
            for folder, data in info.items():
                display_names[folder] = data.get("name", folder)
        except Exception:
            pass

    for folder in sorted(CHROME_BASE.iterdir()):
        if not folder.is_dir():
            continue
        if not (folder.name == "Default" or folder.name.startswith("Profile")):
            continue
        if not (folder / "Cookies").exists():
            continue
        name = display_names.get(folder.name, folder.name)
        profiles.append((folder.name, name, folder))

    return profiles


def _write_config(username, profile_path):
    config_text = f"""from pathlib import Path

# -------------------------------
# USER CONFIG — edit these
# -------------------------------
CHROME_PROFILE = Path("{profile_path}")
X_USERNAME     = "{username}"

# -------------------------------
# PATHS
# -------------------------------
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
EXPORTS_DIR = BASE_DIR / "exports"
DEBUG_DIR   = BASE_DIR / "debug_output"

LIKES_FILE     = DATA_DIR / "x_likes.json"
BOOKMARKS_FILE = DATA_DIR / "x_bookmarks.json"

# -------------------------------
# SCRAPE TUNING
# -------------------------------
SCROLL_STEP   = 900
SCROLL_PAUSE  = 1.5
STALL_SLEEP   = 3.0
DELAY_BETWEEN = 3.0  # seconds between individual tweet page visits (enrich/scrape-missing)
"""
    CONFIG_FILE.write_text(config_text, encoding="utf-8")


def run():
    print("\n=== xtool setup ===\n")

    # X username
    username = input("Enter your X username (without @): ").strip().lstrip("@")
    if not username:
        print("Username cannot be empty.")
        return

    # Chrome profile detection
    print("\nScanning for Chrome profiles...")
    profiles = _detect_profiles()

    if not profiles:
        print("No Chrome profiles found. Make sure Google Chrome is installed and you have logged in at least once.")
        return

    print(f"\nFound {len(profiles)} Chrome profile(s):\n")
    for i, (folder, name, path) in enumerate(profiles):
        print(f"  [{i + 1}] {name} ({folder})")
        print(f"       {path}")

    print()
    if len(profiles) == 1:
        choice = 1
        print(f"Only one profile found — using: {profiles[0][1]}")
    else:
        raw = input(f"Select profile [1-{len(profiles)}]: ").strip()
        try:
            choice = int(raw)
            if not 1 <= choice <= len(profiles):
                raise ValueError
        except ValueError:
            print("Invalid selection.")
            return

    selected = profiles[choice - 1]
    profile_path = selected[2]

    # Confirm
    print(f"\nSummary:")
    print(f"  X username    : @{username}")
    print(f"  Chrome profile: {selected[1]} ({selected[0]})")
    print(f"  Profile path  : {profile_path}")
    print()
    confirm = input("Write config? [y/n]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    _write_config(username, profile_path)
    print(f"\nConfig written to {CONFIG_FILE}")
    print("\nSetup complete. You can now run:")
    print("  python xtool.py likes")
    print("  python xtool.py bookmarks")
