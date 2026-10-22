#!/usr/bin/env python3
"""Direct-upload helpers; upload JWTs arrive through stdin, never CLI arguments.

The account-authorized manifest registration and Worker deployment remain with
the Cloudflare connector. This helper only sends assets using its short-lived,
asset-scoped upload JWT. Do not log the upload result: it contains the completion
JWT required by the connector's final deployment request.
"""

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request


ASSETS = Path(__file__).resolve().parent.parent / "cloudflare/.generated/assets"


def manifest():
    result = {}
    for path in sorted(ASSETS.rglob("*")):
        if not path.is_file() or path.name in {"_headers", "_redirects"}:
            continue
        data = path.read_bytes()
        # Same content+extension identity as Cloudflare's direct-upload example.
        identity = base64.b64encode(data) + path.suffix.lstrip(".").encode()
        result["/" + path.relative_to(ASSETS).as_posix()] = {
            "hash": hashlib.sha256(identity).hexdigest()[:32], "size": len(data)}
    return result


def upload(config):
    files = {item["hash"]: ASSETS / name.lstrip("/")
             for name, item in config["manifest"].items()}
    completion = config["jwt"] if not config["buckets"] else None
    for bucket in config["buckets"]:
        boundary = "NornAssets" + secrets.token_hex(12)
        pieces = []
        for file_hash in bucket:
            path = files[file_hash]
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            pieces.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_hash}\"; "
                           f"filename=\"{file_hash}\"\r\nContent-Type: {mime}\r\n\r\n").encode())
            pieces.extend((base64.b64encode(path.read_bytes()), b"\r\n"))
        pieces.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}/workers/assets/upload?base64=true",
            data=b"".join(pieces), method="POST", headers={
                "Authorization": "Bearer " + config["jwt"],
                "User-Agent": "NornAssetUploader/0.8",
                "Content-Type": f"multipart/form-data; boundary={boundary}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    value = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"Asset upload HTTP {exc.code}: {exc.read().decode()}") from None
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        if not value.get("success"):
            raise RuntimeError(f"Asset upload failed: {value.get('errors')}")
        completion = value.get("result", {}).get("jwt") or completion
    if not completion:
        raise RuntimeError("No completion JWT returned after all asset buckets")
    return {"jwt": completion, "files": len(config["manifest"]), "buckets": len(config["buckets"])}


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "manifest":
        print(json.dumps(manifest(), separators=(",", ":")))
    elif len(sys.argv) == 3 and sys.argv[1] == "upload":
        if sys.stdin.isatty():
            import termios
            settings = termios.tcgetattr(sys.stdin)
            settings[3] &= ~termios.ECHO
            termios.tcsetattr(sys.stdin, termios.TCSANOW, settings)
        print("Ready for upload configuration on stdin", flush=True)
        config = json.loads(Path(sys.argv[2]).read_text())
        config["manifest"] = manifest()
        config["jwt"] = sys.stdin.readline().strip()
        print(json.dumps(upload(config), separators=(",", ":")))
    else:
        raise SystemExit("Usage: cloudflare_assets.py manifest|upload <non-secret-session.json>")
