"""CI smoke launch of exactly two temporary simulators; no physical-device access."""
import json
import subprocess
import time
from pathlib import Path

def run(*args, timeout=120):
    print("Running:", " ".join(args), flush=True)
    return subprocess.check_output(args, text=True, timeout=timeout)

listing = json.loads(run("xcrun", "simctl", "list", "-j"))
runtime = next(r["identifier"] for r in reversed(listing["runtimes"]) if r.get("isAvailable") and "iOS" in r["name"])
app = Path("apple_client/DerivedData/Build/Products/Debug-iphonesimulator/YogurtSimulator.app").resolve()
for family in ("iPhone", "iPad"):
    available = next(d for d in listing["devices"][runtime] if d.get("isAvailable") and family in d["name"])
    device = available["deviceTypeIdentifier"]
    identifier = run("xcrun", "simctl", "create", "Yogurt S3-A " + family, device, runtime).strip()
    try:
        run("xcrun", "simctl", "boot", identifier)
        run("xcrun", "simctl", "bootstatus", identifier, "-b", timeout=300)
        run("xcrun", "simctl", "install", identifier, str(app))
        run("xcrun", "simctl", "launch", identifier, "org.yogurt.native.simulator")
        time.sleep(3)
        run("xcrun", "simctl", "io", identifier, "screenshot", "apple_client/Generated/" + family + ".png")
    finally:
        subprocess.run(["xcrun", "simctl", "shutdown", identifier], check=False, timeout=60)
        subprocess.run(["xcrun", "simctl", "delete", identifier], check=False, timeout=60)
