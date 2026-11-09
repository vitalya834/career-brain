import argparse
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import sys
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "Vault"
CARDS = VAULT / "Вакансии"
_CONFIG_FILE = ROOT / "sources.json"
if not _CONFIG_FILE.is_file():  # first run: work with the shipped example until the user copies it
    _CONFIG_FILE = ROOT / "sources.example.json"
CONFIG = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
STATUSES = {
    "lead": "На рассмотрении", "review": "Сверить", "prepared": "Черновик",
    "inquiry": "Запрос отправлен", "applied": "Отклик отправлен",
    "interview": "Собеседование", "accepted": "Принято",
    "rejected": "Отказ", "closed": "Закрыто",
}


def config_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def source_notes():
    folder = config_path(CONFIG["history"])
    for name in CONFIG.get("history_files", ["Applications Log.md", "Companies.md"]):
        path = folder / name
        if path.is_file():
            yield path, path.read_text(encoding="utf-8-sig")
    for filename in CONFIG.get("additional_history", []):
        path = config_path(filename)
        if path.is_file():
            yield path, path.read_text(encoding="utf-8-sig")


def history(query):
    if len(query.strip()) < 2:
        raise ValueError("Запрос по истории должен содержать хотя бы два символа.")
    for path, content in source_notes():
        section = ""
        for number, line in enumerate(content.splitlines(), 1):
            if line.startswith("## "):
                section = line[3:]
            if query.casefold() in line.casefold():
                yield path, number, section, line


def cards():
    for path in sorted(CARDS.glob("*.md")):
        _, header, body = path.read_text(encoding="utf-8-sig").split("---\n", 2)
        data = dict((key, json.loads(value)) for key, value in
                    (line.split(": ", 1) for line in header.splitlines()))
        yield path, data, body


def save(path, data, body):
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}"
                       for key, value in data.items())
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(f"---\n{header}\n---\n{body}", encoding="utf-8")
    temporary.replace(path)


def url_key(url):
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("Укажи полный URL вакансии.")
    # Preserve IDs in the query string, for example jobs.hug.de/de?id=...
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def links(term, place):
    query = urlencode({"was": term, "wo": place, "umkreis": 35, "angebotsart": 1})
    return {
        "Arbeitsagentur": "https://www.arbeitsagentur.de/jobsuche/suche?" + query,
        "Stepstone": "https://www.stepstone.de/jobs?" + urlencode({"ke": term, "ws": place}),
        "EURES": "https://eures.europa.eu/jobseekers_en",
        "Arbeitnow": "https://www.arbeitnow.com/english-speaking-jobs",
        "The Stack Jobs": "https://jobs.trythestack.co/",
    }


def collect_jobs(term, place):
    query = urlencode({"was": term, "wo": place, "umkreis": 35, "size": 20})
    endpoint = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?"
    request = Request(endpoint + query, headers={"X-API-Key": "jobboerse-jobsuche"})
    with urlopen(request, timeout=12) as response:
        results = json.load(response)["ergebnisliste"]
    for job in results:
        location = ", ".join(
            item.get("adresse", {}).get("ort", "") for item in job.get("stellenlokationen", [])
        )
        if job.get("referenznummer"):
            url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/" + job["referenznummer"]
            salary = [job.get("gehaltsspanneVon"), job.get("gehaltsspanneBis")]
            yield job.get("firma") or "", job.get("stellenangebotsTitel") or "", location, url, {
                "refnr": job["referenznummer"],
                "published": job.get("datumErsteVeroeffentlichung") or job.get("aktuelleVeroeffentlichungsdatum"),
                "start": (job.get("eintrittszeitraum") or {}).get("von"),
                "salary": salary if any(salary) else None,
                "salary_unit": job.get("verguetungsangabe"),
                "distance_km": job.get("entfernung"),
                "contract": job.get("vertragsdauer"),
                "lateral_entry": job.get("quereinstiegGeeignet"),
                "main_profession": job.get("hauptberuf"),
            }


LEGAL_FORMS = re.compile(r"\b(gmbh|ggmbh|mbh|ag|se|kg|kgaa|co|ohg|ug|e\.?\s?v|gbr|ltd|inc|holding|group|deutschland|germany)\b\.?", re.I)
HISTORY_STATES = (
    ("rejected", re.compile(r"отказ|absage|abgelehnt|rejected|место закрыто|закрыт", re.I)),
    ("inquiry", re.compile(r"nur anfrage|только запрос|только вопрос|не полная заявка|keine vollst", re.I)),
    ("applied", re.compile(r"gesendet|sent\b|отправлен|отклик|eingangsbestätigung|danke-mail|portal", re.I)),
    ("inquiry", re.compile(r"запрос|anfrage|вопрос", re.I)),
    ("excluded", re.compile(r"отсеян|исключ|не подавать|nicht passend", re.I)),
)


def company_core(company):
    core = LEGAL_FORMS.sub(" ", company.casefold().replace("&", " "))
    return " ".join(re.findall(r"\w+", core))


def company_history(company, notes=None, limit=3):
    """Lines from the logs that mention the company, newest first, with a rough state."""
    core = company_core(company)
    if len(core) < 3:
        return []
    flags = re.I
    if len(core) <= 4:
        # Short names match ordinary words ("Neu", "Alt"); accept only real acronyms, case-sensitively.
        acronym = next((word for word in re.findall(r"\w+", company) if word.casefold() == core and word.isupper()), None)
        if not acronym:
            return []
        core, flags = acronym, 0
    pattern = re.compile(r"(?<!\w)" + r"\W+".join(map(re.escape, core.split())) + r"(?!\w)", flags)
    hits, seen = [], set()
    for path, content in (notes if notes is not None else source_notes()):
        for number, line in enumerate(content.splitlines(), 1):
            if pattern.search(line) and line.strip() not in seen:
                seen.add(line.strip())
                state = next((name for name, rx in HISTORY_STATES if rx.search(line)), "mentioned")
                hits.append(dict(file=Path(path).name, line=number, state=state, text=line.strip()[:220]))
    rank = {name: index for index, (name, _) in enumerate(HISTORY_STATES)}
    hits.sort(key=lambda hit: rank.get(hit["state"], len(rank)))
    return hits[:limit]


def search(term, place, provider):
    from discovery import arbeitnow_jobs
    results = list(arbeitnow_jobs([term], place) if provider == "arbeitnow" else collect_jobs(term, place))
    archive = "\n".join(content.casefold() for _, content in source_notes())
    for company, title, location, url, *_ in results:
        known = company and company.casefold() in archive
        print(f'{title} | {company or "Работодатель не указан"} | {location}' +
              (" | есть совпадение в истории" if known else ""))
        print(url)
    if not results:
        print("Источник не вернул совпадений.")


def add_card(company, title, location, url, note):
    company, title = company.strip(), title.strip()
    if not company or not title:
        raise ValueError("Укажи компанию и должность.")
    key = url_key(url)
    for path, data, _ in cards():
        if data["url"] and url_key(data["url"]) == key:
            raise ValueError(f"Такая ссылка уже сохранена в {path.name}")
    previous = len(company) >= 2 and any(history(company))
    data = dict(company=company, title=title, location=location, url=url,
                status="review" if previous else "lead", updated=date.today().isoformat())
    identifier = uuid4().hex[:12]
    save(CARDS / f"{identifier}.md", data, f"\n{note}\n")
    return identifier, previous


def update_card(identifier, status, evidence):
    if status not in STATUSES or not evidence.strip():
        raise ValueError("Укажи статус и основание для изменения.")
    for path, data, body in cards():
        if path.stem == identifier:
            today = date.today().isoformat()
            event = f"- {today} · {STATUSES[data['status']]} → {STATUSES[status]} · {evidence}"
            data.update(status=status, updated=today)
            save(path, data, body.rstrip() + "\n\n" + event + "\n")
            return
    raise ValueError("Карточка с таким ID не найдена.")


def edit_card(identifier, company, title, location, url, note):
    company, title, location, url = (value.strip() for value in (company, title, location, url))
    if not company or not title:
        raise ValueError("Укажи компанию и должность.")
    key = url_key(url) if url else ""
    entries = list(cards())
    if key and any(path.stem != identifier and data["url"] and url_key(data["url"]) == key
                   for path, data, _ in entries):
        raise ValueError("Эта ссылка уже есть в другой карточке.")
    for path, data, body in entries:
        if path.stem == identifier:
            today = date.today().isoformat()
            data.update(company=company, title=title, location=location, url=url, updated=today)
            if note.strip():
                body = body.rstrip() + f"\n\n- {today} · {note.strip()}\n"
            save(path, data, body)
            return
    raise ValueError("Карточка с таким ID не найдена.")


def schedule_interview(identifier, value):
    interview = datetime.fromisoformat(value)
    if interview.tzinfo is None:
        raise ValueError("Укажи дату и часовой пояс, например 2026-11-20T14:00+01:00.")
    for path, data, body in cards():
        if path.stem == identifier:
            data["interview"] = interview.isoformat()
            save(path, data, body.rstrip() + f"\n\n- Собеседование · {interview.isoformat()}\n")
            return
    raise ValueError("Карточка с таким ID не найдена.")


def calendar_link(data):
    try:
        start = datetime.fromisoformat(data.get("interview") or "")
    except ValueError:
        return ""
    if start.tzinfo is None:
        return ""
    start = start.astimezone(timezone.utc)
    end = start + timedelta(hours=1)
    params = dict(action="TEMPLATE", text="Собеседование · " + data["company"],
                  dates=f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}")
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def watch_banner():
    """Health of the scheduled search, shown at the top of the dashboard."""
    try:
        import watch
        state = watch.status()
    except Exception as error:  # the dashboard must open even if the watch state is broken
        return f'<section id="watch"><p>Автопоиск: состояние не читается ({escape(type(error).__name__)}).</p></section>'
    marks = {"ok": "✅", "stale": "⚠", "error": "⛔", "never": "⚠"}
    note = (state.get("last") or {}).get("note")
    link = f' · <a href="Vault/Находки/{escape(note)}">последние находки</a>' if note else ""
    hint = "" if state["level"] == "ok" else " Запусти «Install Daily Search.cmd» или «Run Watch.cmd»."
    return (f'<section id="watch"><h2>Автопоиск</h2><p>{marks[state["level"]]} '
            f'{escape(state["text"])}{escape(hint)}{link}</p></section>')


def dashboard(live=False, extra=""):
    from documents_store import KINDS, VERSIONS
    rows = []
    for path, data, body in cards():
        e = escape
        source = ""
        if data["url"]:
            url_key(data["url"])
            source = f' · <a href="{e(data["url"])}">Вакансия</a>'
        options = "".join(f'<option value="{key}"{" selected" if key == data["status"] else ""}>{label}</option>'
                          for key, label in STATUSES.items())
        controls = (f'<form method="post" action="/status"><input type="hidden" name="id" value="{e(path.stem)}">'
                    f'<select name="status" aria-label="Статус">{options}</select>'
                    '<input name="evidence" placeholder="Основание" required>'
                    '<button>Сохранить</button></form>') if live else ""
        if live:
            fields = "".join(f'<input name="{key}" aria-label="{label}" placeholder="{label}"'
                             f' value="{e(data[key])}">' for key, label in
                             (("company", "Компания"), ("title", "Должность"), ("location", "Город"), ("url", "Ссылка")))
            controls += (f'<form method="post" action="/edit"><input type="hidden" name="id" value="{e(path.stem)}">'
                         f'{fields}<input name="note" placeholder="Добавить заметку"><button>Обновить карточку</button></form>')
            controls += (f'<form method="post" action="/interview"><input type="hidden" name="id" value="{e(path.stem)}">'
                         '<input name="interview" aria-label="Дата собеседования с часовым поясом" '
                         'placeholder="2026-11-20T14:00+01:00" required><button>Дата интервью</button></form>')
        calendar = calendar_link(data)
        if calendar:
            source += f' · <a href="{e(calendar)}">В календарь · {e(data["interview"])}</a>'
        documents = []
        for doc in data.get("documents", []):
            if live:
                href = f'/document?id={path.stem}&file={doc["id"]}'
            else:
                from documents_store import document_path
                href = document_path(path.stem, doc).relative_to(ROOT).as_posix()
            label = f"{KINDS[doc['kind']]} · {VERSIONS[doc['version']]} · {doc['filename']}"
            documents.append(f'<li><a href="{e(href)}">{e(label)}</a></li>')
        materials = '<h4>Документы отклика</h4><ul>' + "".join(documents) + '</ul>' if documents else ""
        if live:
            kinds = "".join(f'<option value="{key}">{value}</option>' for key, value in KINDS.items())
            versions = "".join(f'<option value="{key}">{value}</option>' for key, value in VERSIONS.items())
            hidden_id = f'<input type="hidden" name="id" value="{e(path.stem)}">'
            materials += (f'<p><a href="/brief?id={e(path.stem)}">Контекст для ИИ</a> · '
                          f'<a href="/ats?id={e(path.stem)}">ATS-проверка CV</a></p>'
                          '<form method="post" enctype="multipart/form-data" action="/document">'
                          f'{hidden_id}<select name="kind" aria-label="Тип файла">{kinds}</select>'
                          f'<select name="version" aria-label="Версия">{versions}</select>'
                          '<input type="file" name="file" aria-label="Файл" accept=".pdf,.doc,.docx,.txt,.md" required>'
                          '<button>Сохранить файл</button></form>'
                          f'<form method="post" action="/snapshot">{hidden_id}'
                          '<textarea name="posting" aria-label="Текст объявления" placeholder="Вставь текст вакансии" required></textarea>'
                          '<button>Сохранить объявление</button></form>')
        domain = (urlsplit(data["url"]).hostname or "").removeprefix("www.")
        rows.append(
            f'<article data-state="{e(data["status"])}" data-company="{e(data["company"])}"'
            f' data-title="{e(data["title"])}" data-updated="{e(data.get("updated", ""))}"'
            f' data-source="{e(domain)}" data-search="{e(" ".join((data["company"], data["title"], data["location"], body)))}">'
            f'<div class="meta"><span class="badge">{e(STATUSES[data["status"]])}</span>'
            f'<small>{e(data["location"] or "Город не указан")}</small></div>'
            f'<h3>{e(data["title"])}</h3><div class="company">{e(data["company"])}</div>'
            f'<details><summary>Карточка и история</summary><p>{e(body.strip())}</p>'
            f'<a href="{path.relative_to(ROOT).as_posix()}">Markdown</a>{source}{controls}{materials}</details></article>'
        )
    launchers = []
    for label, term, place in CONFIG["queries"]:
        source_links = " · ".join(f'<a href="{escape(url)}">{name}</a>'
                                  for name, url in links(term, place).items())
        launchers.append(f'<li>{escape(label)} · {source_links}</li>')
    options = "".join(f'<option value="{key}">{value}</option>' for key, value in STATUSES.items())
    stages = '<button type="button" data-stage="">Все заявки</button>' + "".join(
        f'<button type="button" data-stage="{key}">{label}</button>' for key, label in STATUSES.items()
    )
    source_links = " · ".join(f'<a href="{path.as_uri()}">{escape(path.stem)}</a>'
                              for path, _ in source_notes())
    template = (ROOT / "dashboard.template.html").read_text(encoding="utf-8")
    output = (template.replace("{{HISTORY}}", source_links).replace("{{SEARCH}}", "".join(launchers))
              .replace("{{LIVE}}", watch_banner() + extra)
              .replace("{{STAGES}}", stages)
              .replace("{{OPTIONS}}", options).replace("{{CARDS}}", "".join(rows)))
    if live:
        return output
    (ROOT / "Career Brain.html").write_text(output, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Career Brain")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dashboard")
    commands.add_parser("history").add_argument("query")
    finder = commands.add_parser("search")
    finder.add_argument("term")
    finder.add_argument("place", nargs="?", default="Bonn")
    finder.add_argument("--provider", choices=("arbeitsagentur", "arbeitnow"), default="arbeitsagentur")
    discovery = commands.add_parser("discover")
    discovery.add_argument("term")
    discovery.add_argument("place", nargs="?", default="Bonn")
    discovery.add_argument("--provider", choices=("all", "arbeitsagentur", "arbeitnow", "employers", "work-in-germany"), default="all")
    commands.add_parser("serve")
    importer = commands.add_parser("import-legacy")
    importer.add_argument("path", type=Path)
    importer.add_argument("--apply", action="store_true")
    old_docs = commands.add_parser("import-legacy-documents")
    old_docs.add_argument("path", type=Path)
    external = commands.add_parser("jobspy-csv")
    external.add_argument("path", type=Path)
    ats = commands.add_parser("ats", help="ATS- и нормопроверка CV против текста вакансии")
    ats.add_argument("id", nargs="?", help="ID карточки с сохранёнными CV и объявлением")
    ats.add_argument("--cv", type=Path)
    ats.add_argument("--posting", type=Path)
    ats.add_argument("--json", action="store_true")
    watcher = commands.add_parser("watch", help="Автопоиск: только новые вакансии, заметка в Vault/Находки")
    watcher.add_argument("--dry-run", action="store_true", help="Показать результат, ничего не записывая")
    watcher.add_argument("--status", action="store_true", help="Когда был последний запуск и что он нашёл")
    brief = commands.add_parser("brief")
    brief.add_argument("id")
    add = commands.add_parser("add")
    for field in ("company", "title", "location", "url", "note"):
        add.add_argument(field)
    state = commands.add_parser("status")
    state.add_argument("id")
    state.add_argument("status", choices=STATUSES)
    state.add_argument("evidence")
    args = parser.parse_args()

    if args.command == "jobspy-csv":
        from discovery import read_jobspy_csv
        try:
            print(json.dumps(read_jobspy_csv(args.path), ensure_ascii=False, indent=2))
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return

    if args.command == "discover":
        from discovery import discover
        try:
            result = discover(args.term, args.place, args.provider)
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "ats":
        from ats_check import as_markdown, check, check_card, read_text
        try:
            if args.id:
                report = check_card(args.id, args.cv, args.posting)
            elif args.cv and args.posting:
                report = check(args.cv, read_text(args.posting)[0])
            else:
                parser.error("Укажи ID карточки или --cv и --posting.")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else as_markdown(report))
        return

    if args.command == "watch":
        import watch
        if args.status:
            print(watch.status()["text"])
            return
        result = watch.run(dry_run=args.dry_run)
        if not result["ok"]:
            print("Автопоиск не удался: " + result["errors"][0])
            sys.exit(1)
        print(f"Новых вакансий: {result['new']}, к проверке: {len(result['good'])}, сбоев: {len(result['errors'])}.")
        for job in result["good"]:
            print(f"- {job['title']} — {job['company']} — {job['url']}")
        if result["note"]:
            print(f"Заметка: {result['note']}")
        if not args.dry_run:
            dashboard()
        return

    if args.command == "history":
        for path, number, section, line in history(args.query):
            print(f"{path.name}:{number} [{section}] {line}")
        return
    if args.command == "search":
        for name, url in links(args.term, args.place).items():
            print(f"{name} — {url}")
        try:
            search(args.term, args.place, args.provider)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"Запрос к {args.provider} не прошёл ({error}). Используй ссылки выше.")
            raise SystemExit(1) from error
        return
    if args.command == "serve":
        from local_app import serve
        serve()
        return
    if args.command == "brief":
        from documents_store import brief_documents
        for path, data, body in cards():
            if path.stem == args.id:
                profile = config_path(CONFIG["candidate_profile"])
                if not profile.is_file():
                    parser.error("Профиль кандидата (candidate_profile в sources.json) не найден.")
                template = (VAULT / "Подготовка отклика.md").read_text(encoding="utf-8")
                print(f"{template}\n\n## Карточка\n\n{data['company']} · {data['title']} · {data['location']}"
                      f"\n\n{data['url']}\n{body}\n{brief_documents(path, data)}"
                      f"\n\n## Профиль кандидата\n\n{profile.read_text(encoding='utf-8-sig')}")
                return
        parser.error("Карточка с таким ID не найдена.")
    if args.command == "import-legacy":
        from legacy import import_jobs
        import_jobs(args.path, args.apply)
        if not args.apply:
            return
    try:
        if args.command == "import-legacy-documents":
            from legacy import import_documents
            import_documents(args.path)
        if args.command == "add":
            identifier, previous = add_card(args.company, args.title, args.location, args.url, args.note)
            print(identifier, "— найдено совпадение в истории" if previous else "")
        if args.command == "status":
            update_card(args.id, args.status, args.evidence)
    except ValueError as error:
        parser.error(str(error))
    dashboard()


if __name__ == "__main__":
    main()
