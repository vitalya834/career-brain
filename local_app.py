"""Browser workflow served only on the local machine."""
from email import policy
from email.parser import BytesParser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from uuid import uuid4

from career import CONFIG, VAULT, config_path, add_card, dashboard, edit_card, history, update_card
from discovery import PROVIDERS, discover
from documents_store import MAX_FILE, attach, brief_documents, card_record, get_document

TOKEN = uuid4().hex


def field(name, placeholder, value="", required=False):
    return (f'<input name="{name}" aria-label="{placeholder}" placeholder="{placeholder}" '
            f'value="{escape(value)}" {"required" if required else ""}>')


def add_form(company="", title="", location="", url=""):
    return ('<form method="post" action="/add">' +
            field("company", "Компания", company, True) + field("title", "Должность", title, True) +
            field("location", "Город", location) + field("url", "Ссылка", url, True) +
            field("note", "Заметка") + "<button>В трекер</button></form>")


def tools_panel(params, message=""):
    def get(name, default=""):
        return params.get(name, [default])[0].strip()

    result = f'<p role="status">{escape(message)}</p>' if message else ""
    term, place, provider = get("term"), get("place", "Bonn"), get("provider", "all")
    if term:
        try:
            report = discover(term, place, provider)
            rows = []
            for job in report["jobs"]:
                company, title, location, url = (job[key] for key in ("company", "title", "location", "url"))
                info = (" · Уже в трекере" if job["saved_card"] else
                        " · Схожая карточка" if job["similar_saved_role"] else
                        " · Компания есть в истории" if job["known_company"] else "")
                form = "" if job["saved_card"] else add_form(company, title, location, url)
                rows.append(f'<li><a href="{escape(url)}">{escape(title)}</a> · '
                            f'{escape(company)} · {escape(location)} · {escape(", ".join(job["sources"]))}{info}{form}</li>')
            checks = " · ".join(f'{escape(source["source"])} ({source.get("matches", "недоступен")})'
                                for source in report["sources"])
            manual = " · ".join(f'<a href="{escape(url)}">{escape(name)}</a>'
                                for name, url in report["manual_links"].items())
            export = quote(json.dumps(report, ensure_ascii=False), safe="")
            result += (f'<p><b>{len(rows)} результатов</b> · {checks}</p>'
                       f'<p>{escape(report["history_error"])}</p><p>{manual}</p>'
                       f'<p><a download="career-search.json" href="data:application/json;charset=utf-8,{export}">'
                       'Скачать результаты для ИИ (JSON)</a></p>')
            result += "<ol>" + "".join(rows) + "</ol>" if rows else "<p>Совпадений нет.</p>"
        except (OSError, ValueError, KeyError, TypeError) as error:
            result += f"<p>Источник временно недоступен. {escape(str(error))}</p>"
    query = get("archive")
    if len(query) >= 2:
        hits = list(history(query))[:30]
        result += "<ul>" + "".join(f"<li>{escape(path.name)}:{n} · {escape(line)}</li>"
                                   for path, n, _, line in hits) + "</ul>" if hits else "<p>История пуста по этому запросу.</p>"
    options = "".join(f'<option value="{key}"{" selected" if key == provider else ""}>{name}</option>'
                      for key, name in PROVIDERS.items())
    presets = " · ".join(f'<a href="/?{escape(urlencode(dict(term=job, place=where, provider=source)))}#discovery">{escape(label)}</a>'
                         for label, job, where, source in CONFIG["search_presets"])
    return (f'<section id="discovery"><h2>Поиск вакансий</h2><p>{presets}</p><form method="get" action="/#discovery">' +
            field("term", "Должности через |", term, True) + field("place", "Города через |", place) +
            f'<select name="provider" aria-label="Источник">{options}</select><button>Найти</button></form>'
            '<form method="get" action="/#discovery">' + field("archive", "Компания в истории", query, True) +
            '<button>Сверить историю</button></form>' + result + '</section>'
            '<section id="add-job"><h2>Добавить вакансию</h2>' + add_form() + '</section>')


class Handler(BaseHTTPRequestHandler):
    def local_host(self):
        if self.headers.get("Host") == "127.0.0.1:8765":
            return True
        self.respond("Открой http://127.0.0.1:8765.", 403, "text/plain")
        return False

    def respond(self, body, status=200, content_type="text/html"):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def page(self, params=None, message="", status=200):
        output = dashboard(live=True, extra=tools_panel(params or {}, message))
        for action in ("add", "status", "interview", "edit", "document", "snapshot"):
            marker = f'action="/{action}">'
            output = output.replace(marker, marker + f'<input type="hidden" name="token" value="{TOKEN}">')
        self.respond(output, status)

    def do_GET(self):
        if not self.local_host():
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            self.page(parse_qs(parts.query))
            return
        if parts.path == "/ats":
            params = parse_qs(parts.query)
            try:
                from ats_check import as_markdown, check_card
                self.respond(as_markdown(check_card(params.get("id", [""])[0])), content_type="text/plain")
            except (ValueError, OSError) as error:
                self.respond(f"ATS-проверка невозможна: {error}", 400, "text/plain")
            return
        if parts.path in ("/document", "/brief"):
            params = parse_qs(parts.query)
            identifier = params.get("id", [""])[0]
            try:
                if parts.path == "/brief":
                    path, data, body = card_record(identifier)
                    checklist = (VAULT / "Подготовка отклика.md").read_text(encoding="utf-8")
                    profile = (VAULT / "Профиль.md").read_text(encoding="utf-8")
                    facts = config_path(CONFIG["candidate_profile"])
                    facts = facts.read_text(encoding="utf-8-sig") if facts.is_file() else "Профиль JSON не найден."
                    self.respond(f"{checklist}\n\n{profile}\n\n## Вакансия\n"
                                 f"{data['company']} · {data['title']} · {data['location']}\n{data['url']}\n"
                                 f"{body}\n{brief_documents(path, data)}"
                                 f"\n\n## Проверенные факты кандидата (JSON)\n\n{facts}", content_type="text/plain")
                else:
                    path, doc = get_document(identifier, params.get("file", [""])[0])
                    if path.stat().st_size > MAX_FILE:
                        raise ValueError("Документ превышает лимит.")
                    payload = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(doc["filename"], safe=""))
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(payload)
            except (ValueError, OSError):
                self.respond("Материал не найден или недоступен.", 404, "text/plain")
            return
        if parts.path.startswith("/Vault/"):
            path = (VAULT / unquote(parts.path.removeprefix("/Vault/"))).resolve()
            if path.is_relative_to(VAULT.resolve()) and path.suffix == ".md" and path.is_file():
                self.respond(path.read_text(encoding="utf-8-sig"), content_type="text/plain")
                return
        self.respond("Не найдено.", 404, "text/plain")

    def do_POST(self):
        if not self.local_host():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            limit = MAX_FILE + 65536 if self.path == "/document" else 262144
            if not 0 < length <= limit:
                raise ValueError("Некорректный размер формы.")
            payload = self.rfile.read(length)
            if self.path == "/document":
                content_type = self.headers.get("Content-Type", "")
                if not content_type.startswith("multipart/form-data;") or "\n" in content_type or "\r" in content_type:
                    raise ValueError("Ожидалась форма загрузки файла.")
                form = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {content_type}\r\n\r\n".encode("ascii") + payload)
                fields = {part.get_param("name", header="content-disposition"): part for part in form.iter_parts()}
                params = {key: [part.get_payload(decode=True).decode("utf-8")] for key, part in fields.items()
                          if key in {"token", "id", "kind", "version"}}
            else:
                params = parse_qs(payload.decode("utf-8"), keep_blank_values=True)
            def get(name):
                return params.get(name, [""])[0]
            if get("token") != TOKEN:
                self.respond("Открой локальную панель заново.", 403, "text/plain")
                return
            if self.path == "/document":
                file = fields.get("file")
                if file is None or not file.get_filename():
                    raise ValueError("Выбери файл.")
                attach(get("id"), get("kind"), get("version"), file.get_filename(), file.get_payload(decode=True))
                message = "Версия документа сохранена."
            elif self.path == "/snapshot":
                posting = get("posting").strip()
                if not posting:
                    raise ValueError("Вставь текст объявления.")
                attach(get("id"), "posting", "ready", "Объявление.txt", posting.encode("utf-8"))
                message = "Копия объявления сохранена."
            elif self.path == "/add":
                _, known = add_card(*(get(key) for key in ("company", "title", "location", "url", "note")))
                message = "Карточка сохранена. Есть совпадение в истории." if known else "Карточка сохранена."
            elif self.path == "/status":
                update_card(get("id"), get("status"), get("evidence"))
                message = "Статус сохранён."
            elif self.path == "/edit":
                edit_card(*(get(key) for key in ("id", "company", "title", "location", "url", "note")))
                message = "Карточка обновлена."
            elif self.path == "/interview":
                from career import schedule_interview
                schedule_interview(get("id"), get("interview"))
                message = "Дата собеседования сохранена."
            else:
                self.respond("Не найдено.", 404, "text/plain")
                return
            dashboard()
            self.page(message=message)
        except ValueError as error:
            self.page(message=str(error), status=400)

    def log_message(self, *args):
        pass


def serve():
    with HTTPServer(("127.0.0.1", 8765), Handler) as server:
        print("Career Brain: http://127.0.0.1:8765", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
