from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MONITORING_DIR = DATA_DIR / "monitoring"
RACKS_FILE = DATA_DIR / "racks.csv"
DEVICES_FILE = DATA_DIR / "devices.csv"
PLACEMENTS_FILE = DATA_DIR / "placements.csv"
DCS_FILE = DATA_DIR / "datacenters.csv"
LINKS_FILE = DATA_DIR / "dci_links.csv"
USERS_FILE = BASE_DIR / "users.csv"
SECRET_KEY = os.environ.get("DC_MONITOR_SECRET", "dc-monitor-pro-secret-change-me")

for d in (DATA_DIR, MONITORING_DIR, BASE_DIR / "assets" / "racks"):
    d.mkdir(parents=True, exist_ok=True)
