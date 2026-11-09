"""Scheduled search: run every configured query, keep only postings not seen before,
read their full text, mark stop requirements and write one Markdown note per run.

Designed to be started by the Windows Task Scheduler. Each run is independent:
network errors are retried, logged per query and never lose the list of seen postings.
"""
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from career import CONFIG, ROOT, VAULT, cards, company_history, source_notes, url_key

API = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/"
HEADERS = {"X-API-Key": "jobboerse-jobsuche", "User-Agent": "Mozilla/5.0 (CareerBrain watch)",
           "Accept": "application/json"}
FINDS = VAULT / "Находки"
STATE = VAULT / "watch_state.json"
LOG = ROOT / "watch.log"

DEFAULTS = {
    "queries": ["IT Support", "Anwendungsbetreuung", "Systemadministrator", "IT-Administrator", "Helpdesk"],
    "places": [["Bonn", 18]],
    "max_age_days": 21,
    "title_include": r"\bit\b|it-|edv|support|administr|anwendung|system|helpdesk|service ?desk|netzwerk|informatik|software|scada|leit|test",
    "title_exclude": r"senior|lead\b|leiter|leitung|head|architekt|consultant|berater|vertrieb|sales|werkstudent|ausbildung|praktik|azubi|duales|trainee|minijob|aushilfe",
    "agency_pattern": r"personal|zeitarbeit|randstad|adecco|hays|manpower|tempton|recruit|staffing",
    "stop_patterns": [[r"\bc1\b|\bc2\b|verhandlungssicher|muttersprach", "Deutsch C1+/verhandlungssicher"]],
    "note_patterns": [
        [r"vergleichbar|quereinst", "vergleichbare Qualifikation / Quereinstieg zulässig"],
        [r"schicht|rufbereitschaft|bereitschaftsdienst", "Schicht oder Rufbereitschaft"],
        [r"führerschein|fahrerlaubnis", "Führerschein gefordert"],
        [r"homeoffice|home-office|mobile[sn]? arbeit|telearbeit|hybrid", "Homeoffice möglich"],
        [r"sicherheitsüberprüfung", "Sicherheitsüberprüfung"],
        [r"dienstreise|reisebereitschaft", "Dienstreisen"],
    ],
}


def settings():
    merged = dict(DEFAULTS)
    merged.update(CONFIG.get("watch", {}))
    return merged


def fetch(url, tries=3, timeout=20):
    """GET JSON with retries; a 404 is a real answer and is not retried."""
    last = None
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                raise
            last = error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            last = error
        time.sleep(2 * (attempt + 1))
    raise last


def search(term, place, radius, days):
    query = urlencode({"was": term, "wo": place, "umkreis": radius, "size": 50, "angebotsart": 1,
                       "veroeffentlichtseit": days})
    return fetch(API + "v6/jobs?" + query).get("ergebnisliste") or []


def details(refnr):
    encoded = b64encode(refnr.encode()).decode()
    return fetch(API + "v4/jobdetails/" + encoded, tries=2)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seen": {}, "runs": []}


def save_state(state):
    VAULT.mkdir(exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(STATE)


def log(line):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")


def deadline(text):
    found = re.search(r"(?:bis (?:zum |spätestens )?|bewerbungsfrist:?\s*|bewerbungsschluss:?\s*)\W{0,4}"
                      r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", text, re.I)
    if not found:
        return None
    try:
        return date(int(found[3]), int(found[2]), int(found[1]))
    except ValueError:
        return None


def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"[*#\\]", "", text or "")).strip()


def requirements(text, limit=420):
    """The part of the posting that states what the employer asks for."""
    start = re.search(r"ihr profil|dein profil|anforderung|voraussetzung|das bringen sie|das bringst du|"
                      r"damit überzeugst|was sie mitbringen|qualifikation|sie haben:", text, re.I)
    return text[start.start():start.start() + limit] if start else text[:limit]


def run(dry_run=False, today=None):
    conf = settings()
    today = today or date.today()
    state = load_state()
    seen = state["seen"]
    include, exclude = re.compile(conf["title_include"], re.I), re.compile(conf["title_exclude"], re.I)
    agency = re.compile(conf["agency_pattern"], re.I)

    jobs, errors, total = {}, [], 0
    tasks = [(term, place, radius) for term in conf["queries"] for place, radius in conf["places"]]

    def one(task):
        try:
            return task, search(*task, conf["max_age_days"]), None
        except Exception as error:  # every query reports its own failure
            return task, [], f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(4) as pool:
        for (term, place, _), found, error in pool.map(one, tasks):
            if error:
                errors.append(f"{term} · {place}: {error}")
                continue
            total += len(found)
            for job in found:
                refnr, title = job.get("referenznummer"), job.get("stellenangebotsTitel") or ""
                if not refnr or refnr in jobs or not include.search(title) or exclude.search(title):
                    continue
                jobs[refnr] = dict(
                    refnr=refnr, title=title, company=job.get("firma") or "",
                    location=", ".join(i.get("adresse", {}).get("ort", "") for i in job.get("stellenlokationen", [])),
                    published=job.get("datumErsteVeroeffentlichung") or "",
                    salary=[job.get("gehaltsspanneVon"), job.get("gehaltsspanneBis")],
                    distance=job.get("entfernung"), query=f"{term} · {place}",
                    url="https://www.arbeitsagentur.de/jobsuche/jobdetail/" + refnr)

    if tasks and len(errors) == len(tasks):
        log(f"FAIL all {len(tasks)} queries failed: {errors[0]}")
        state["runs"] = (state.get("runs", []) + [dict(at=datetime.now().isoformat(timespec="minutes"),
                                                       ok=False, error=errors[0])])[-60:]
        if not dry_run:
            save_state(state)
        return dict(ok=False, errors=errors, new=0, note=None)

    saved = {url_key(data["url"]) for _, data, _ in cards() if data["url"]}
    try:
        notes = list(source_notes())
    except OSError:
        notes = []
        errors.append("История откликов недоступна: сверка с журналом пропущена.")

    fresh = [job for refnr, job in jobs.items() if refnr not in seen and url_key(job["url"]) not in saved]
    for job in fresh:
        job["agency"] = bool(agency.search(job["company"]))
        hits = company_history(job["company"], notes) if notes else []
        job["history"] = hits
        job["history_state"] = next((h["state"] for h in hits if h["state"] != "mentioned"), "mentioned" if hits else None)
        try:
            text = clean(details(job["refnr"]).get("stellenangebotsBeschreibung"))
        except Exception as error:
            text = ""
            job["detail_error"] = type(error).__name__
        job["stops"] = [label for rx, label in conf["stop_patterns"] if re.search(rx, text, re.I)]
        job["notes"] = [label for rx, label in conf["note_patterns"] if re.search(rx, text, re.I)]
        job["deadline"] = deadline(text)
        job["requirements"] = requirements(text) if text else ""

    far = date(2999, 1, 1)
    fresh.sort(key=lambda j: j["published"], reverse=True)
    fresh.sort(key=lambda j: j["deadline"] or far)
    good = [j for j in fresh if not j["stops"] and not j["agency"] and j["history_state"] in (None, "mentioned", "inquiry")
            and not (j["deadline"] and j["deadline"] < today)]
    known = [j for j in fresh if j["history_state"] in ("applied", "rejected", "excluded") and j not in good]
    rest = [j for j in fresh if j not in good and j not in known]

    note = None
    if not dry_run:
        for job in fresh:
            seen[job["refnr"]] = today.isoformat()
        limit = (today - timedelta(days=120)).isoformat()
        state["seen"] = {key: value for key, value in seen.items() if value >= limit}
        state["runs"] = (state.get("runs", []) + [dict(
            at=datetime.now().isoformat(timespec="minutes"), ok=True, queries=len(tasks), failed=len(errors),
            results=total, new=len(fresh), good=len(good))])[-60:]
        if fresh or errors:
            note = write_note(today, good, rest, known, errors, len(tasks), total)
            state["runs"][-1]["note"] = note.name
        save_state(state)
        log(f"OK queries={len(tasks)} failed={len(errors)} results={total} new={len(fresh)} good={len(good)}")
    return dict(ok=True, errors=errors, new=len(fresh), good=good, rest=rest, known=known, note=note)


def line(job):
    parts = [f"**{job['title']}** — {job['company']}, {job['location']}"]
    extra = []
    if job["deadline"]:
        extra.append(f"срок {job['deadline'].strftime('%d.%m.%Y')}")
    if job["published"]:
        extra.append(f"опубликовано {job['published']}")
    if any(job["salary"]):
        extra.append("зарплата " + "–".join(str(int(v)) for v in job["salary"] if v))
    if job["distance"] is not None:
        extra.append(f"{job['distance']} км")
    out = [f"- [ ] {parts[0]}", f"  - {' · '.join(extra)}" if extra else None, f"  - {job['url']}"]
    if job["stops"]:
        out.append("  - ⛔ " + "; ".join(job["stops"]))
    if job["agency"]:
        out.append("  - кадровое агентство")
    if job["notes"]:
        out.append("  - " + "; ".join(job["notes"]))
    for hit in job["history"][:2]:
        out.append(f"  - в журнале ({hit['state']}): {hit['file']}:{hit['line']} — {hit['text'][:110]}")
    if job.get("detail_error"):
        out.append("  - полный текст не открылся, требования проверь по ссылке")
    elif job["requirements"]:
        out.append(f"  - > {job['requirements']}")
    return "\n".join(item for item in out if item)


def write_note(today, good, rest, known, errors, queries, total):
    FINDS.mkdir(parents=True, exist_ok=True)
    path = FINDS / f"{today.isoformat()}.md"
    stamp = datetime.now().strftime("%H:%M")
    block = [f"## Запуск {stamp}", "",
             f"Запросов: {queries}, ответов: {total}, новых вакансий: {len(good) + len(rest) + len(known)}.", ""]
    if good:
        block += ["### Проверить в первую очередь", "", *[line(j) for j in good], ""]
    if rest:
        block += ["### Есть стоп-требование, агентство или истёк срок", "", *[line(j) for j in rest], ""]
    if known:
        block += ["### Компания уже есть в журнале (отклик, отказ или отсев)", "", *[line(j) for j in known], ""]
    if errors:
        block += ["### Сбои", "", *[f"- {e}" for e in errors], ""]
    block += ["Источник: Bundesagentur für Arbeit. Перед откликом открой объявление работодателя "
              "и сверь дату, место и требования.", ""]
    head = "" if path.exists() else f"# Новые вакансии {today.strftime('%d.%m.%Y')}\n\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(head + "\n".join(block) + "\n")
    return path


def status():
    """Short health line for the dashboard and for `career.py watch --status`."""
    runs = load_state().get("runs", [])
    if not runs:
        return dict(level="never", text="Автопоиск ещё ни разу не запускался.")
    last = runs[-1]
    ok_runs = [r for r in runs if r.get("ok")]
    when = datetime.fromisoformat(last["at"])
    age = datetime.now() - when
    if not last.get("ok"):
        return dict(level="error", text=f"Последний запуск {when:%d.%m %H:%M} не удался: {last.get('error', '')}",
                    last=last)
    if age > timedelta(hours=36):
        return dict(level="stale", last=last,
                    text=f"Автопоиск не запускался {age.days * 24 + age.seconds // 3600} ч. Последний раз {when:%d.%m %H:%M}.")
    week = [r for r in ok_runs if datetime.fromisoformat(r["at"]) > datetime.now() - timedelta(days=7)]
    return dict(level="ok", last=last, text=(
        f"Автопоиск работает: последний запуск {when:%d.%m %H:%M}, новых {last.get('new', 0)}, "
        f"из них к проверке {last.get('good', 0)}. За 7 дней запусков: {len(week)}, новых: "
        f"{sum(r.get('new', 0) for r in week)}."))
