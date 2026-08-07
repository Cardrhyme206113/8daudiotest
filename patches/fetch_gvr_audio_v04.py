from pathlib import Path
import base64
import urllib.request

# JCenter is no longer part of modern Android repository defaults, but Google's
# preserved GVR source mirror still hosts the exact 1.180.0 AAR binaries. Vendor
# them into app/libs at build time so Orbit8D does not depend on dead JCenter.
base = 'https://chromium.googlesource.com/external/github.com/googlevr/gvr-android-sdk/+/25a0c20415bd3854b76f3e0e55f73d36cdc076fd/libraries/'
libs = Path('Orbit8D-Android/app/libs')
libs.mkdir(parents=True, exist_ok=True)

for name in ('sdk-common-1.180.0.aar', 'sdk-base-1.180.0.aar', 'sdk-audio-1.180.0.aar'):
    url = base + name + '?format=TEXT'
    print('Fetching preserved Google VR library:', name)
    with urllib.request.urlopen(url, timeout=60) as r:
        encoded = r.read()
    data = base64.b64decode(encoded)
    if len(data) < 10000:
        raise SystemExit(f'Unexpectedly small GVR AAR: {name} ({len(data)} bytes)')
    (libs / name).write_bytes(data)

print('Google VR / Resonance Audio AARs staged locally')
