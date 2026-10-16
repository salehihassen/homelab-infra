#!/usr/bin/env python3
"""Export the first five items of the latest CondenseIt digest for Home Assistant."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the admin bearer credential to another endpoint.
        return None


def make_sample(payload, reader_url, sampled_at):
    reader = urlsplit(reader_url)
    if reader.scheme != "https" or not reader.netloc or reader.username or reader.password or reader.query or reader.fragment:
        raise ValueError("Reader URL must be an HTTPS base URL without credentials")
    if payload is None:
        return {"digest_id": 0, "created_at": None, "item_count": 0,
                "items": [], "digest_url": reader_url.rstrip("/") + "/",
                "sampled_at": sampled_at}
    meta, items = payload["meta"], payload["items"]
    digest_id = meta["id"]
    if type(digest_id) is not int or digest_id < 1 or not isinstance(items, list):
        raise ValueError("Invalid digest ID or items")
    if not isinstance(meta["created_at"], str) or not meta["created_at"]:
        raise ValueError("Missing digest timestamp")
    compact = []
    for item in items[:5]:
        title = item["title"]
        source = item.get("source", "")
        if not isinstance(title, str) or not title.strip() or not isinstance(source, str):
            raise ValueError("Invalid item title or source")
        compact.append({"title": " ".join(title.split())[:300],
                        "source": " ".join(source.split())[:150]})
    return {"digest_id": digest_id, "created_at": meta["created_at"],
            "item_count": len(compact), "items": compact,
            "digest_url": reader_url.rstrip("/") + "/?" + urlencode({"id": digest_id}),
            "sampled_at": sampled_at}


def write_sample(output, sample):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(sample, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    api_url = os.environ["CONDENSEIT_DIGEST_API_URL"]
    api = urlsplit(api_url)
    if api.scheme != "http" or api.hostname not in {"127.0.0.1", "localhost", "::1"} or api.username or api.password or api.query or api.fragment:
        raise ValueError("API URL must use local loopback HTTP without credentials")
    password = Path(os.environ["CONDENSEIT_DIGEST_PASSWORD_FILE"]).read_text().strip()
    if not password:
        raise ValueError("Empty API password")
    request = Request(api_url, headers={"Authorization": "Bearer " + password, "Accept": "application/json"})
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        payload = json.load(response)
    sample = make_sample(payload, os.environ["CONDENSEIT_DIGEST_READER_URL"], int(time.time()))
    write_sample(Path(os.environ["CONDENSEIT_DIGEST_OUTPUT"]), sample)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # URL-bearing exception messages and response bodies may contain secrets.
        print(f"CondenseIt digest export failed ({type(exc).__name__}); previous sample retained", file=sys.stderr)
        sys.exit(1)
