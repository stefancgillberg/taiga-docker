#!/usr/bin/env python3
"""Delete epics and their linked stories by epic ID.

Pass the epic IDs to remove as positional arguments:

    TAIGA_USER=<user-name> TAIGA_PASS=<your-password> TAIGA_SLUG=<project-slug> \\
        python3 cleanup_wrong_project.py <epic-id> <epic-id> <epic-id>

Optional overrides:
    TAIGA_HOST   (default http://100.118.163.55:9000)
    TAIGA_SLUG   (default <your-project-slug>)
"""

import json, os, sys, urllib.error, urllib.request

HOST = os.environ.get("TAIGA_HOST", "http://100.118.163.55:9000").rstrip("/")
SLUG = os.environ.get("TAIGA_SLUG", "<your-project-slug>")
USER = os.environ.get("TAIGA_USER")
PASS = os.environ.get("TAIGA_PASS")


def api(method, path, token=None, payload=None):
    url = HOST + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code == 404:
            return None
        sys.exit(f"API-fel {e.code} på {method} {path}: {detail}")


epic_ids = []
for arg in sys.argv[1:]:
    try:
        epic_ids.append(int(arg))
    except ValueError:
        sys.exit(f"Ogiltigt epic-id: {arg!r} — ange heltal.")

if not epic_ids:
    sys.exit("Ange minst ett epic-id som argument, t.ex.: python3 cleanup_wrong_project.py 16 17 18")

if not USER or not PASS:
    sys.exit("Sätt TAIGA_USER och TAIGA_PASS.")

print(f"Loggar in på {HOST} ...")
auth = api("POST", "/api/v1/auth", payload={"type": "normal", "username": USER, "password": PASS})
token = auth["auth_token"]
print(f"Rensar epic-id:n: {epic_ids}\n")

deleted_stories = deleted_epics = 0
for epic_id in epic_ids:
    related = api("GET", f"/api/v1/epics/{epic_id}/related_userstories", token=token)
    for rel in related or []:
        us_id = rel["user_story"]
        us = api("GET", f"/api/v1/userstories/{us_id}", token=token)
        subject = us["subject"] if us else f"id={us_id}"
        print(f"  Raderar story id={us_id}: {subject}")
        api("DELETE", f"/api/v1/userstories/{us_id}", token=token)
        deleted_stories += 1
    epic = api("GET", f"/api/v1/epics/{epic_id}", token=token)
    subject = epic["subject"] if epic else f"id={epic_id}"
    print(f"  Raderar epic id={epic_id}: {subject}")
    api("DELETE", f"/api/v1/epics/{epic_id}", token=token)
    deleted_epics += 1

print(f"\nKlart. {deleted_stories} stories och {deleted_epics} epics raderade.")
