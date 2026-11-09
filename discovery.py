"""Multi-source job discovery shared by the browser and CLI."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
from functools import lru_cache
from itertools import islice
import json
import re
from time import monotonic
from urllib.parse import quote
from urllib.request import urlopen

from career import CONFIG, cards, collect_jobs, company_history, links, source_notes, url_key

PROVIDERS = {
    "all": "Все подключённые источники",
    "arbeitsagentur": "Arbeitsagentur",
    "arbeitnow": "Arbeitnow",
    "employers": "Сайты работодателей",
    "work-in-germany": "Work in Germany · GitHub",
}


def normalized(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def read_jobspy_csv(path):
    saved = {url_key(data["url"]): card.stem for card, data, _ in cards() if data["url"]}
    archive = "\n".join(content.casefold() for _, content in source_notes())
    jobs = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"title", "company", "job_url"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV JobSpy должен содержать title, company и job_url.")
        for row in reader:
            title, company = (row.get(key) or "" for key in ("title", "company"))
            url = (row.get("job_url_direct") or row.get("job_url") or "").strip()
            try:
                key = url_key(url)
            except ValueError:
                url = (row.get("job_url") or "").strip()
                try:
                    key = url_key(url)
                except ValueError:
                    continue
            if not title or key in jobs:
                continue
            jobs[key] = dict(company=company, title=title, location=row.get("location") or "",
                             url=url, sources=[row.get("site") or "JobSpy"],
                             saved_card=saved.get(key), known_company=len(company) >= 2
                             and company.casefold() in archive)
    return dict(source="JobSpy CSV", jobs=list(jobs.values()))


GENERIC = {"m", "w", "d", "mwd", "und", "der", "die", "das", "für", "mit", "im", "in", "the", "and"}


def published_key(row):
    hints = row.get("source_hints") or {}
    found = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(hints.get("published") or hints.get("listed_date") or ""))
    return int("".join(found.groups())) if found else 0


def relevant(title, terms):
    """Arbeitsagentur matches loosely on related professions; keep titles sharing a real word."""
    words = set(normalized(title).split())
    for term in terms:
        wanted = [w for w in normalized(term).split() if w not in GENERIC and (len(w) >= 3 or w == "it")]
        if any(w in words or any(t.startswith(w) for t in words) for w in wanted):
            return True
    return False


def matches(title, location, terms, place):
    title = normalized(title)
    locations = [normalized(item) for item in place.split("|") if normalized(item)]
    return (any(all(word in title for word in normalized(term).split()) for term in terms)
            and (not locations or any(f" {city} " in f" {normalized(location)} " for city in locations)))


def employer_jobs(token, company, terms, place):
    endpoint = "https://boards-api.greenhouse.io/v1/boards/" + quote(token, safe="") + "/jobs"
    with urlopen(endpoint, timeout=9) as response:
        jobs = json.load(response)["jobs"]
    for job in jobs:
        title = job["title"]
        location = (job.get("location") or {}).get("name") or ""
        if matches(title, location, terms, place):
            yield company, title, location, job["absolute_url"]


def arbeitnow_jobs(terms, place):
    with urlopen("https://www.arbeitnow.com/api/job-board-api", timeout=9) as response:
        jobs = json.load(response)["data"]
    for job in jobs:
        title, location = job["title"], job.get("location") or ""
        if matches(title, location, terms, place):
            yield job["company_name"], title, location, job["url"]


@lru_cache(maxsize=1)
def germany_catalog(cache_window):
    url = "https://raw.githubusercontent.com/ashishtiwari03/work-in-germany/main/data/jobs.json"
    limit = 45 * 1024 * 1024
    with urlopen(url, timeout=15) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Каталог превышает 45 МБ.")
    return json.loads(payload)["jobs"]


def germany_jobs(terms, place):
    rows = germany_catalog(int(monotonic() // 900))
    for row in rows:
        title, location = row.get("title") or "", row.get("location") or ""
        if row.get("url") and matches(title, location, terms, place):
            yield row.get("company") or "", title, location, row["url"], {
                "listed_date": row.get("posted_at"),
                "upstream": row.get("source"),
                "language_hint": row.get("language"),
                "visa_hint": row.get("visa"),
            }


def discover(term, place, provider="all"):
    terms = [item.strip() for item in term.split("|") if normalized(item)]
    if not 1 <= len(terms) <= 3 or len(term) > 180 or len(place) > 120 or provider not in PROVIDERS:
        raise ValueError("Укажи от одного до трёх названий должности через | и выбери источник.")

    queries = []
    cities = [item.strip() for item in place.split("|") if item.strip()] or [""]
    city = cities[0]
    if provider in ("all", "arbeitsagentur"):
        for item in terms:
            for german_city in cities[:3]:
                queries.append((f"Arbeitsagentur · {item} · {german_city}", collect_jobs, (item, german_city)))
    if provider in ("all", "arbeitnow"):
        queries.append(("Arbeitnow", arbeitnow_jobs, (terms, place)))
    if provider in ("all", "employers"):
        for token, company in CONFIG["employer_boards"].items():
            queries.append((company + " · Greenhouse", employer_jobs, (token, company, terms, place)))
    if provider == "work-in-germany":
        queries.append(("Work in Germany · GitHub", germany_jobs, (terms, place)))

    entries = list(cards())
    saved = {url_key(data["url"]): path.stem for path, data, _ in entries if data["url"]}
    saved_roles = {(normalized(data["company"]), normalized(data["title"])) for _, data, _ in entries}
    history_error = ""
    try:
        notes = list(source_notes())
    except OSError:
        notes = []
        history_error = "Часть локальной истории недоступна. Сверка по заметкам пропущена."
    history_cache = {}
    results, sources = {}, []
    with ThreadPoolExecutor(max_workers=10) as pool:
        pending = [(name, pool.submit(lambda fn, args: list(islice(fn(*args), 60)), fn, args))
                   for name, fn, args in queries]
        for name, future in pending:
            try:
                found = future.result()
                sources.append(dict(source=name, matches=len(found)))
            except (OSError, ValueError, KeyError, TypeError) as error:
                sources.append(dict(source=name, error=type(error).__name__))
                continue
            dropped = 0
            for company, title, location, url, *metadata in found:
                if not title:
                    continue
                if name.startswith("Arbeitsagentur") and not relevant(title, terms):
                    dropped += 1
                    continue
                try:
                    key = url_key(url)
                except ValueError:
                    continue
                if company not in history_cache:
                    history_cache[company] = company_history(company, notes) if notes else []
                hits = history_cache[company]
                entry = results.setdefault(key, dict(company=company, title=title, location=location,
                    url=url, sources=[], saved_card=saved.get(key), known_company=bool(hits),
                    history=hits, history_state=next((h["state"] for h in hits if h["state"] != "mentioned"),
                                                     "mentioned" if hits else None),
                    similar_saved_role=(normalized(company), normalized(title)) in saved_roles))
                if name not in entry["sources"]:
                    entry["sources"].append(name)
                if metadata:
                    entry["source_hints"] = metadata[0]
            if dropped:
                sources[-1]["filtered_out"] = dropped

    order = {None: 0, "mentioned": 1, "inquiry": 2, "excluded": 3, "applied": 4, "rejected": 5}
    rows = sorted(results.values(), key=lambda row: (bool(row["saved_card"]), row["similar_saved_role"],
                  order.get(row["history_state"], 1),
                  -published_key(row)))
    return dict(query=term, location=place, checked_at=datetime.now(timezone.utc).isoformat(),
                sources=sources, history_error=history_error, jobs=rows,
                manual_links=links(terms[0], city),
                instructions="Сверь ссылку работодателя, требования к языку, квалификации и поездкам. Совпадение в истории требует сверки. Запись в ленте не подтверждает, что вакансия всё ещё открыта.")
