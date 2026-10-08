#!/usr/bin/env python3
"""Export Ollama Cloud included-usage percentages for Home Assistant."""

import calendar
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from contextlib import closing
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from pathlib import Path


USAGE_URL = "https://ollama.com/api/usage"
ACCOUNT_URL = "https://ollama.com/api/me"
SETTINGS_URL = "https://ollama.com/settings"
SESSION_NAMES = ("__Secure-session", "wos-session", "ollama_session",
                 "__Host-ollama_session", "__Secure-next-auth.session-token",
                 "next-auth.session-token")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward a session cookie to the external sign-in service.
        return None


def session_cookie():
    cookie_file = (required_path("OLLAMA_USAGE_COOKIE_FILE")
                   if os.environ.get("OLLAMA_USAGE_COOKIE_FILE") else None)
    if cookie_file and cookie_file.exists():
        try:
            cookie = cookie_file.read_text().strip()
        except OSError:
            raise RuntimeError("Ollama session cookie file is unavailable") from None
        cookie = re.sub(r"^Cookie:\s*", "", cookie, flags=re.IGNORECASE)
    elif os.environ.get("OLLAMA_USAGE_BROWSER_COOKIES_FILE"):
        database = required_path("OLLAMA_USAGE_BROWSER_COOKIES_FILE")
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True,
                                         timeout=0.5)) as connection:
                rows = connection.execute(
                    "SELECT name,value FROM moz_cookies "
                    "WHERE host IN ('ollama.com','.ollama.com') AND path='/' "
                    "AND expiry>? AND (name IN (" + ",".join("?" for _ in SESSION_NAMES) +
                    ") OR name GLOB 'wos-session.[0-9]*') ORDER BY name",
                    (int(time.time()), *SESSION_NAMES)
                ).fetchall()
            cookie = "; ".join(f"{name}={value}" for name, value in rows)
        except sqlite3.Error:
            raise RuntimeError("Ollama browser session database is unavailable; "
                               "close Firefox once to import the saved session") from None
    elif cookie_file:
        raise RuntimeError("Ollama session cookie file is unavailable")
    else:
        raise RuntimeError("Ollama quota now requires a signed-in session; configure "
                           "OLLAMA_USAGE_BROWSER_COOKIES_FILE or OLLAMA_USAGE_COOKIE_FILE")
    if not cookie or "\n" in cookie or "\r" in cookie:
        raise RuntimeError("Ollama session is missing or invalid; sign in again")
    return cookie


def fetch_settings():
    cookie = session_cookie()
    request = urllib.request.Request(SETTINGS_URL, headers={
        "Cookie": cookie, "Accept": "text/html",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            html = response.read().decode("utf-8")
            # Cache only a session that returned a valid quota page. Firefox may
            # hold its database exclusively while running; never bypass its locks.
            parse_monthly_settings(html)
            if os.environ.get("OLLAMA_USAGE_COOKIE_FILE"):
                cookies = SimpleCookie(cookie)
                for header in response.headers.get_all("Set-Cookie", []):
                    updated = SimpleCookie(header)
                    for name, morsel in updated.items():
                        if name in SESSION_NAMES or re.fullmatch(r"wos-session\.[0-9]+", name):
                            if morsel.value:
                                cookies[name] = morsel.value
                            elif name in cookies:
                                del cookies[name]
                saved = "; ".join(f"{name}={morsel.coded_value}" for name, morsel in cookies.items())
                if not saved or "\n" in saved or "\r" in saved:
                    raise RuntimeError("Ollama returned an invalid session")
                write_private_file(required_path("OLLAMA_USAGE_COOKIE_FILE"), saved + "\n")
            return html
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308, 401, 403):
            raise RuntimeError("Ollama session expired; sign in again") from None
        raise RuntimeError(f"Ollama settings request returned HTTP {exc.code}") from None


class SettingsParser(HTMLParser):
    """Retain section boundaries so unrelated meters and resets cannot be mixed."""

    def __init__(self):
        super().__init__()
        self.root = {"tag": "root", "attrs": {}, "parts": [], "parent": None}
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parts": [], "parent": self.stack[-1]}
        self.stack[-1]["parts"].append(node)
        self.nodes.append(node)
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["parts"].append(data)


def node_text(node):
    if node["tag"] in ("script", "style"):
        return ""
    return " ".join(part if isinstance(part, str) else node_text(part)
                    for part in node["parts"])


def parse_monthly_settings(html):
    parser = SettingsParser()
    parser.feed(html)
    labels = [node for node in parser.nodes
              if " ".join(node_text(node).split()) in ("Monthly usage", "Free usage")]
    amount = r"((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)"
    pattern = re.compile(r"\$" + amount + r"\s+of\s+\$" + amount + r"\s+used", re.I)
    quota = None
    for label in labels:
        block = label["parent"]
        while block is not None:
            text = node_text(block)
            if re.search(r"(?:Weekly|Hourly|Session) usage", text, re.I):
                break
            match = pattern.search(text)
            percentage = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s+used", text, re.I)
            if match or percentage:
                if match:
                    spent, limit = (float(value.replace(",", "")) for value in match.groups())
                    if not math.isfinite(spent) or not math.isfinite(limit) or spent < 0 or limit <= 0:
                        raise ValueError("Ollama returned invalid monthly included credits")
                    used = spent / limit * 100
                else:
                    used = float(percentage.group(1))
                if not math.isfinite(used) or used < 0:
                    raise ValueError("Ollama returned invalid monthly utilization")
                used = min(100.0, used)
                quota = used, None
                # Only reset elements inside this monthly section are eligible.
                for node in parser.nodes:
                    ancestor = node
                    while ancestor is not None and ancestor is not block:
                        ancestor = ancestor["parent"]
                    if ancestor is block and node["attrs"].get("data-time") and re.search(
                            r"\bResets\b", node_text(node), re.I):
                        try:
                            reset = datetime.fromisoformat(node["attrs"]["data-time"].replace("Z", "+00:00"))
                            if reset.tzinfo is None:
                                raise ValueError()
                            return used, int(reset.timestamp())
                        except (ValueError, OverflowError):
                            raise ValueError("Ollama returned an invalid monthly reset time") from None
            block = block["parent"]
        if quota is not None:
            return quota
    raise ValueError("Ollama settings page has no valid monthly included-credit quota")


def required_path(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is missing")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def fetch(url, api_key, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Ollama request to {url} returned HTTP {exc.code}") from exc


def next_monthly_reset(signed_up_at, now):
    """Return the first monthly signup anniversary after now, clamping to short months."""
    for offset in range(3):
        index = now.month - 1 + offset
        year, month = now.year + index // 12, index % 12 + 1
        day = min(signed_up_at.day, calendar.monthrange(year, month)[1])
        candidate = signed_up_at.replace(year=year, month=month, day=day)
        if candidate > now:
            return candidate
    raise ValueError("Could not compute the next Ollama reset time")


def get_usage():
    api_key = os.environ.get("OLLAMA_CLOUD_API_KEY")
    if not api_key:
        raise ValueError("OLLAMA_CLOUD_API_KEY is missing")

    usage = fetch(USAGE_URL, api_key)
    if not isinstance(usage, dict):
        raise ValueError("Ollama usage response is not an object")

    # /api/me also returns the account email; export only the plan and reset time.
    account = fetch(ACCOUNT_URL, api_key, method="POST")
    if not isinstance(account, dict):
        raise ValueError("Ollama account response is not an object")
    plan = account.get("Plan")
    if "limits" in usage:
        monthly = usage["limits"]["monthly"]
        # Legacy API reports included usage as a fraction, rather than dollars.
        used = float(monthly["usage"]) * 100
        if not math.isfinite(used) or used < 0:
            raise ValueError("Ollama returned an invalid monthly usage value")
        used = min(used, 100.0)
        requests = sum(int(model["request_count"]) for model in monthly.get("models") or [])
        resets_at = None
    elif "totals" in usage and "buckets" in usage:
        used, resets_at = parse_monthly_settings(fetch_settings())
        # Activity totals cover 7d/30d, not the account's monthly billing cycle.
        requests = None
    else:
        raise ValueError("Unsupported Ollama usage response format")
    # Prefer the reset displayed by Ollama. If absent, the pricing page documents
    # Free's signup anniversary; paid-plan subscription anchors aren't in /api/me.
    if resets_at is None and plan == "free":
        signed_up_at = datetime.fromisoformat(account["CreatedAt"].replace("Z", "+00:00"))
        if signed_up_at.tzinfo is None:
            raise ValueError("Ollama returned a signup time without a timezone")
        resets_at = int(next_monthly_reset(signed_up_at, datetime.now(timezone.utc)).timestamp())

    return {
        "monthly_used": round(used, 1),
        "monthly_remaining": round(100 - used, 1),
        "monthly_requests": requests,
        "plan": plan if isinstance(plan, str) else None,
        "monthly_resets_at": resets_at,
        "sampled_at": int(time.time()),
    }


def write_private_file(output, contents):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    output = required_path("OLLAMA_USAGE_OUTPUT")
    usage = get_usage()
    write_private_file(output, json.dumps(usage, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Ollama usage export failed: {exc}", file=sys.stderr)
        sys.exit(1)
