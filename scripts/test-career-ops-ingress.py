#!/usr/bin/env python3
"""Exercise Career Ops' live HTTPS navigation/CSRF boundary without model calls.

Usage: python3 scripts/test-career-ops-ingress.py --url https://<private-host>
Checks status codes only; never prints CV, profile, API contents or credentials.
"""
import argparse
import urllib.error
import urllib.parse
import urllib.request


def check(base_url):
    base = urllib.parse.urlsplit(base_url)
    if base.scheme != "https" or not base.netloc or base.path not in ("", "/"):
        raise ValueError("--url must be an HTTPS origin")
    origin = f"https://{base.netloc}"
    navigation = {
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-User": "?1",
    }
    cases = [
        ("direct navigation", "/", "GET", {}, 200),
        ("same-site clicked link", "/", "GET", navigation, 200),
        ("same-site clicked pipeline link", "/pipeline", "GET", navigation, 200),
        ("same-site clicked HEAD link", "/", "HEAD", navigation, 200),
        ("cross-site clicked link", "/", "GET", {**navigation, "Sec-Fetch-Site": "cross-site"}, 200),
        ("background fetch", "/", "GET", {**navigation, "Sec-Fetch-Mode": "cors"}, 403),
        ("embedded page", "/", "GET", {**navigation, "Sec-Fetch-Dest": "iframe"}, 403),
        ("navigation without user activation", "/", "GET", {k: v for k, v in navigation.items() if k != "Sec-Fetch-User"}, 403),
        ("foreign Origin on clicked link", "/", "GET", {**navigation, "Origin": "https://untrusted.example"}, 403),
        ("cross-origin POST", "/api/cv", "POST", navigation, 403),
        ("foreign Origin POST", "/api/cv", "POST", {"Origin": "https://untrusted.example"}, 403),
        ("same-origin API fetch", "/api/clis", "GET", {"Sec-Fetch-Site": "same-origin", "Origin": origin}, 200),
    ]
    for path in ("/api", "/api/cv", "/api/clis", "/%61pi/cv", "/api%2Fcv"):
        cases.append((f"API document navigation {path}", path, "GET", navigation, 403))
    failures = []
    for label, path, method, headers, expected in cases:
        request = urllib.request.Request(
            origin + path, method=method, headers=headers,
            data=b"{}" if method == "POST" else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                actual = response.status
        except urllib.error.HTTPError as error:
            actual = error.code
            error.close()
        if actual != expected:
            failures.append(f"{label}: expected {expected}, received {actual}")
        print(f"{'PASS' if actual == expected else 'FAIL'} {label}: {actual}")
    if failures:
        raise AssertionError("\n".join(failures))
    print(f"Career Ops ingress: {len(cases)} navigation/security cases passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    check(parser.parse_args().url)
