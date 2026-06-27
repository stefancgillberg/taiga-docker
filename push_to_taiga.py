#!/usr/bin/env python3
"""
Skapar epics + user stories i Taiga via REST-API från JSON-filer och länkar
varje story till sin epic (Taigas separata epic-entitet).

Generiskt: pekar du på en epic-fil (se epic-a.json som mall) skapas dess epic
och stories i projektet. Logiken är skild från story-datan.

Använd så här (inga creds passerar någon annan än dig):

    export TAIGA_USER='ditt-användarnamn'
    export TAIGA_PASS='ditt-lösenord'
    python3 push_to_taiga.py epic-b.json

Flera filer i samma körning går bra:
    python3 push_to_taiga.py epic-b.json epic-c.json

Lista föräldralösa stories (finns i Taiga men inte i JSON — t.ex. efter
omdöpta titlar), utan att pusha:
    python3 push_to_taiga.py --list-orphans
    python3 push_to_taiga.py --list-orphans epic-b.json epic-d.json

Utan filargument läser --list-orphans alla taiga/epic-*.json i samma katalog.

Ta bort dubbletter (epics och stories med identisk titel — behåller lägst id):
    python3 push_to_taiga.py --clean-duplicates

Lista projektets giltiga user-story-statusar (kanban-tillstånd), utan att pusha:
    python3 push_to_taiga.py --list-statuses

Sätt status per story genom att lägga till ett valfritt "status"-fält i JSON
(statusnamnet måste finnas i projektet — se --list-statuses; skiftlägesokänsligt):
    { "subject": "US-B1 — ...", "status": "In progress", "tags": [...], "description": "..." }
Stories utan "status" lämnas orörda i sitt nuvarande tillstånd.

Kända statusar i <project-slug> (gäller även de flesta Taiga-projekt):
    new | ready | in progress | ready for test | done | archived

Valfria override:
    TAIGA_HOST   (default http://100.118.163.55:9000)
    TAIGA_SLUG   (default <project-slug>)
    DRY_RUN=1    skriver bara ut vad som skulle göras, anropar inte API:t

Story-filens format (JSON):
    {
      "epic": "Epic B — Transkriberingsagent",   # blir Taiga-epicens titel
      "stories": [
        {
          "subject": "US-B1 — ...",
          "tags": ["epic-b", "..."],
          "description": "markdown ..."
        }
      ]
    }

Idempotent: epics och stories som redan finns (på titel) återanvänds i stället
för att dupliceras, redan skapade stories länkas till sin epic om länken
saknas, och description/tags PATCH:as om de skiljer sig från JSON-källan.
Säkert att köra om.
"""

import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HOST = os.environ.get("TAIGA_HOST", "http://100.118.163.55:9000").rstrip("/")
SLUG = os.environ.get("TAIGA_SLUG", "<project-slug>")
USER = os.environ.get("TAIGA_USER")
PASS = os.environ.get("TAIGA_PASS")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
PATCH_RETRIES = 2


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ORPHAN_SUBJECT_PREFIX = "US-"


def parse_argv(argv):
    """Returnerar (orphans_only, statuses_only, clean_duplicates, epic_paths)."""
    orphans_only = False
    statuses_only = False
    clean_duplicates = False
    paths = []
    for arg in argv:
        if arg == "--list-orphans":
            orphans_only = True
        elif arg == "--list-statuses":
            statuses_only = True
        elif arg == "--clean-duplicates":
            clean_duplicates = True
        else:
            paths.append(arg)
    return orphans_only, statuses_only, clean_duplicates, paths


def default_epic_paths():
    return sorted(glob.glob(os.path.join(SCRIPT_DIR, "epic-*.json")))


def expected_subjects(epics):
    return {s["subject"] for _, stories in epics for s in stories}


def load_epics(paths):
    """Läser epic-filer och returnerar en lista (epic_subject, [stories])."""
    epics = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            sys.exit(f"Hittar inte story-filen: {path}")
        except json.JSONDecodeError as e:
            sys.exit(f"Ogiltig JSON i {path}: {e}")
        subject = data.get("epic")
        if not subject:
            sys.exit(f"Saknar 'epic'-titel i {path}")
        items = data.get("stories", [])
        for s in items:
            if "subject" not in s:
                sys.exit(f"Story utan 'subject' i {path}: {s}")
            s.setdefault("tags", [])
            s.setdefault("description", "")
            s.setdefault("status", None)  # optional kanban-state name (e.g. "In progress")
        if not items:
            print(f"Varning: inga stories i {path}", file=sys.stderr)
        epics.append((subject, items))
    return epics


def api(method, path, token=None, payload=None, query=None):
    url = HOST + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
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
        sys.exit(f"API-fel {e.code} på {method} {path}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Kunde inte nå Taiga på {HOST}: {e.reason}")


def api_all(path, token, project_id):
    """Hämtar alla sidor från ett liständpunkt (hanterar Taiga-paginering)."""
    results = []
    page = 1
    while True:
        query = {"project": project_id, "page": page}
        url = HOST + path + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode()
                page_results = json.loads(body) if body else []
                if not isinstance(page_results, list):
                    break
                results.extend(page_results)
                if not resp.headers.get("x-pagination-next"):
                    break
                page += 1
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            sys.exit(f"API-fel {e.code} på GET {path} (sida {page}): {detail}")
        except urllib.error.URLError as e:
            sys.exit(f"Kunde inte nå Taiga på {HOST}: {e.reason}")
    return results


def api_allow_http_error(method, path, token=None, payload=None, query=None):
    """Som api() men returnerar (body, status_code) i stället för att avsluta."""
    url = HOST + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            return (json.loads(body) if body else None), resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            body = json.loads(detail) if detail else None
        except json.JSONDecodeError:
            body = {"_raw": detail}
        return body, e.code
    except urllib.error.URLError as e:
        sys.exit(f"Kunde inte nå Taiga på {HOST}: {e.reason}")


def tag_names(tags):
    """Normaliserar Taigas taggformat till en sorterad lista med namn."""
    names = []
    for tag in tags or []:
        if isinstance(tag, str):
            names.append(tag)
        elif isinstance(tag, (list, tuple)) and tag:
            names.append(tag[0])
    return names


def tags_for_taiga(desired_names, existing_tags=None):
    """Bygger Taigas tagglista och behåller befintliga färger där det går."""
    color_by_name = {}
    for tag in existing_tags or []:
        if isinstance(tag, (list, tuple)) and tag:
            color_by_name[tag[0]] = tag[1] if len(tag) > 1 else None
    return [[name, color_by_name.get(name)] for name in desired_names]


def get_status_map(token, project_id):
    """name(lower) -> status_id för projektets user-story-statusar."""
    statuses = api("GET", "/api/v1/userstory-statuses", token=token,
                   query={"project": project_id}) or []
    return {s["name"].lower(): s["id"] for s in statuses}


def resolve_status(name, status_map):
    """Översätter ett statusnamn (skiftlägesokänsligt) till dess id, eller avslutar."""
    if name is None:
        return None
    status_id = status_map.get(str(name).lower())
    if status_id is None:
        giltiga = ", ".join(sorted(status_map)) or "(inga)"
        sys.exit(f"Okänd status {name!r}. Giltiga i projektet: {giltiga}.")
    return status_id


def story_differs(remote, desired, desired_status_id=None):
    """True om subject, description, taggnamn eller status skiljer sig från JSON-källan."""
    if (remote.get("subject") or "") != (desired.get("subject") or ""):
        return True
    if (remote.get("description") or "") != (desired.get("description") or ""):
        return True
    if tag_names(remote.get("tags")) != list(desired.get("tags") or []):
        return True
    if desired_status_id is not None and remote.get("status") != desired_status_id:
        return True
    return False


def get_user_story(token, us_id):
    return api("GET", f"/api/v1/userstories/{us_id}", token=token)


def patch_user_story(token, us_id, desired, desired_status_id=None):
    """PATCH:ar description/tags/status om de ändrats. Returnerar updated|unchanged."""
    for attempt in range(PATCH_RETRIES):
        remote = get_user_story(token, us_id)
        if not story_differs(remote, desired, desired_status_id):
            return "unchanged"

        payload = {
            "version": remote["version"],
            "subject": desired["subject"],
            "description": desired["description"],
            "tags": tags_for_taiga(desired["tags"], remote.get("tags")),
        }
        if desired_status_id is not None:
            payload["status"] = desired_status_id
        body, status = api_allow_http_error(
            "PATCH", f"/api/v1/userstories/{us_id}", token=token, payload=payload
        )
        if status in (200, 204):
            return "updated"

        detail = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
        if status == 400 and "version" in detail.lower() and attempt + 1 < PATCH_RETRIES:
            continue

        sys.exit(f"API-fel {status} på PATCH /api/v1/userstories/{us_id}: {detail}")

    sys.exit(f"PATCH misslyckades efter {PATCH_RETRIES} försök för user story {us_id}")


def find_or_create_epic(token, project_id, subject, existing_epics):
    """Returnerar (epic_id, skapad_bool). Återanvänder befintlig epic på titel."""
    if subject in existing_epics:
        return existing_epics[subject], False
    epic = api("POST", "/api/v1/epics", token=token, payload={
        "project": project_id,
        "subject": subject,
    })
    existing_epics[subject] = epic["id"]
    return epic["id"], True


def linked_story_ids(token, epic_id):
    """Set av user-story-id:n som redan är länkade till epicen."""
    related = api("GET", f"/api/v1/epics/{epic_id}/related_userstories", token=token)
    if not isinstance(related, list):
        # Paginated or unexpected response shape — fall back to empty set;
        # link_story() handles the resulting 400 gracefully.
        return set()
    return {r["user_story"] for r in related}


def story_epic_labels(token, epic_id_by_subject, epic_subjects):
    """story_id -> sorterad lista med epictitlar från JSON-källorna."""
    labels = {}
    for epic_subject in epic_subjects:
        epic_id = epic_id_by_subject.get(epic_subject)
        if epic_id is None:
            continue
        for us_id in linked_story_ids(token, epic_id):
            labels.setdefault(us_id, set()).add(epic_subject)
    return {us_id: sorted(names) for us_id, names in labels.items()}


def login():
    if not USER or not PASS:
        sys.exit("Sätt TAIGA_USER och TAIGA_PASS.")
    print(f"Loggar in på {HOST} ...")
    auth = api("POST", "/api/v1/auth",
               payload={"type": "normal", "username": USER, "password": PASS})
    token = auth["auth_token"]
    project = api("GET", "/api/v1/projects/by_slug", token=token, query={"slug": SLUG})
    return token, project["id"]


def list_orphans(epics, token, project_id):
    """Skriver ut US-*-stories i Taiga som saknas i JSON-källorna."""
    known = expected_subjects(epics)
    managed_epics = [subject for subject, _ in epics]

    epic_id_by_subject = {
        e["subject"]: e["id"]
        for e in api_all("/api/v1/epics", token, project_id)
    }
    epic_labels = story_epic_labels(token, epic_id_by_subject, managed_epics)

    remote_stories = api_all("/api/v1/userstories", token, project_id)

    orphans = []
    for us in remote_stories:
        subject = us.get("subject", "")
        if subject in known or not subject.startswith(ORPHAN_SUBJECT_PREFIX):
            continue
        orphans.append({
            "id": us["id"],
            "ref": us.get("ref"),
            "subject": subject,
            "epics": epic_labels.get(us["id"], []),
            "is_closed": us.get("is_closed", False),
        })

    orphans.sort(key=lambda row: row["subject"])

    print(f"Projekt '{SLUG}' — {len(known)} stories i JSON, "
          f"{len(remote_stories)} totalt i Taiga.\n")

    if not orphans:
        print("Inga föräldralösa US-*-stories.")
        return 0

    print(f"Föräldralösa user stories ({len(orphans)} st) — finns i Taiga, saknas i JSON:\n")
    for row in orphans:
        state = "stängd" if row["is_closed"] else "öppen"
        epic_text = ", ".join(row["epics"]) if row["epics"] else "(ej länkad till epic)"
        print(f"  #{row['ref']:<4} id={row['id']:<5}  [{state}]  {row['subject']}")
        print(f"           epic: {epic_text}")

    print("\nTa bort eller stäng dessa manuellt i Taiga om de ersatts av nya titlar.")
    return len(orphans)


def clean_duplicates(token, project_id):
    """Tar bort epics och user stories med identisk titel — behåller högst id (senaste push)."""
    removed_epics = removed_stories = 0

    all_epics = api_all("/api/v1/epics", token, project_id)
    by_subject = {}
    for e in all_epics:
        by_subject.setdefault(e["subject"], []).append(e)
    for subject, dupes in sorted(by_subject.items()):
        if len(dupes) < 2:
            continue
        dupes.sort(key=lambda e: e["id"], reverse=True)  # högst id = senaste push
        keep = dupes[0]
        for extra in dupes[1:]:
            if DRY_RUN:
                print(f"  [DRY] epic  behåll id={keep['id']}  radera id={extra['id']}  {subject!r}")
            else:
                api("DELETE", f"/api/v1/epics/{extra['id']}", token=token)
                print(f"  ✖ epic raderad id={extra['id']}  (behöll id={keep['id']})  {subject!r}")
            removed_epics += 1

    all_stories = api_all("/api/v1/userstories", token, project_id)
    by_subject = {}
    for us in all_stories:
        by_subject.setdefault(us["subject"], []).append(us)
    for subject, dupes in sorted(by_subject.items()):
        if len(dupes) < 2:
            continue
        dupes.sort(key=lambda us: us["id"], reverse=True)  # högst id = senaste push
        keep = dupes[0]
        for extra in dupes[1:]:
            if DRY_RUN:
                print(f"  [DRY] story behåll id={keep['id']}  radera id={extra['id']}  {subject!r}")
            else:
                api("DELETE", f"/api/v1/userstories/{extra['id']}", token=token)
                print(f"  ✖ story raderad id={extra['id']}  (behöll id={keep['id']})  {subject!r}")
            removed_stories += 1

    verb = "Skulle radera" if DRY_RUN else "Raderade"
    print(f"\n{verb}: {removed_epics} epics, {removed_stories} stories.")


def main(argv):
    orphans_only, statuses_only, clean_dups, paths = parse_argv(argv)

    if clean_dups:
        if not USER or not PASS:
            sys.exit("Sätt TAIGA_USER och TAIGA_PASS (eller kör med DRY_RUN=1 för förhandsgranskning).")
        token, project_id = login()
        action = "Förhandsgranskar" if DRY_RUN else "Letar efter och raderar"
        print(f"{action} dubbletter i projekt '{SLUG}' ...")
        clean_duplicates(token, project_id)
        return

    if statuses_only:
        if DRY_RUN:
            print(f"DRY_RUN — skulle lista user-story-statusar i {SLUG} på {HOST}.")
            return
        token, project_id = login()
        status_map = get_status_map(token, project_id)
        print(f"User-story-statusar i projektet '{SLUG}':")
        for name in sorted(status_map, key=lambda n: status_map[n]):
            print(f"  • {name}")
        return

    if not paths:
        if orphans_only:
            paths = default_epic_paths()
            if not paths:
                sys.exit("Hittar inga epic-*.json i taiga/-katalogen.")
        else:
            sys.exit("Ange minst en story-fil, t.ex.: python3 push_to_taiga.py epic-a.json")

    epics = load_epics(paths)

    if orphans_only:
        if DRY_RUN:
            known = expected_subjects(epics)
            print(f"DRY_RUN — skulle lista föräldralösa US-*-stories i {SLUG} "
                  f"(jämför mot {len(known)} ämnen i {len(epics)} epic-filer).")
            return
        token, project_id = login()
        list_orphans(epics, token, project_id)
        return

    if DRY_RUN:
        total = sum(len(s) for _, s in epics)
        print(f"DRY_RUN — skulle skapa/länka/patcha {total} stories under "
              f"{len(epics)} epics i {SLUG} på {HOST}:\n")
        for subject, stories in epics:
            print(f"  ▸ EPIC: {subject}")
            for s in stories:
                status_text = f"  status={s['status']}" if s.get("status") else ""
                print(f"      • {s['subject']}  {s['tags']}{status_text}  (skapa eller patcha om ändrad)")
        return

    if not USER or not PASS:
        sys.exit("Sätt TAIGA_USER och TAIGA_PASS (eller kör med DRY_RUN=1).")

    token, project_id = login()
    print(f"Projekt '{SLUG}' hittat (id={project_id}).")

    needs_status = any(s.get("status") for _, stories in epics for s in stories)
    status_map = get_status_map(token, project_id) if needs_status else {}

    existing_epics = {e["subject"]: e["id"]
                      for e in api_all("/api/v1/epics", token, project_id)}
    story_id_by_subject = {us["subject"]: us["id"]
                           for us in api_all("/api/v1/userstories", token, project_id)}
    story_id_by_code = {}
    for _subj, _sid in story_id_by_subject.items():
        _code = _subj.split(" — ", 1)[0].strip()
        if _code.startswith("US-"):
            story_id_by_code.setdefault(_code, _sid)

    created_us, existing_us, updated_us, unchanged_us = 0, 0, 0, 0
    linked, already_linked = 0, 0

    for subject, stories in epics:
        epic_id, epic_created = find_or_create_epic(token, project_id, subject, existing_epics)
        print(f"\n▸ EPIC: {subject}  ({'skapad' if epic_created else 'fanns redan'}, id={epic_id})")
        already = linked_story_ids(token, epic_id)

        for s in stories:
            desired_status_id = resolve_status(s.get("status"), status_map)
            us_id = story_id_by_subject.get(s["subject"])
            if us_id is None:
                _code = s["subject"].split(" — ", 1)[0].strip()
                us_id = story_id_by_code.get(_code)
            if us_id is None:
                create_payload = {
                    "project": project_id,
                    "subject": s["subject"],
                    "description": s["description"],
                    "tags": s["tags"],
                }
                if desired_status_id is not None:
                    create_payload["status"] = desired_status_id
                us = api("POST", "/api/v1/userstories", token=token, payload=create_payload)
                us_id = us["id"]
                story_id_by_subject[s["subject"]] = us_id
                created_us += 1
                print(f"  ✔ skapad: {s['subject']}")
            else:
                existing_us += 1
                result = patch_user_story(token, us_id, s, desired_status_id)
                if result == "updated":
                    updated_us += 1
                    print(f"  ↻ uppdaterad: {s['subject']}")
                else:
                    unchanged_us += 1
                    print(f"  ↷ oförändrad: {s['subject']}")

            if us_id in already:
                already_linked += 1
            else:
                body, status = api_allow_http_error(
                    "POST", f"/api/v1/epics/{epic_id}/related_userstories",
                    token=token, payload={"epic": epic_id, "user_story": us_id},
                )
                already.add(us_id)
                if status in (200, 201):
                    linked += 1
                    print("      ↳ länkad till epic")
                elif status == 400 and "already exists" in json.dumps(body).lower():
                    already_linked += 1
                else:
                    detail = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
                    sys.exit(f"API-fel {status} på POST /api/v1/epics/{epic_id}/related_userstories: {detail}")

    print(
        f"\nKlart. Stories: {created_us} skapade, {existing_us} befintliga "
        f"({updated_us} uppdaterade, {unchanged_us} oförändrade). "
        f"Länkar: {linked} nya, {already_linked} fanns redan."
    )


if __name__ == "__main__":
    main(sys.argv[1:])
