"""Import the JSON records written by the original job-tracker API."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from career import CARDS, cards, save, url_key

# "accepted" in the old tracker was not a confirmed job offer; keep it for review instead.
STATUS = {"sent": "applied", "interview": "interview", "rejected": "rejected", "accepted": "review"}


def legacy_id(job, path):
    old_id = str(job.get("id") or f"{path.name}:{job['company']}:{job['position']}")
    return old_id, "legacy-" + sha256(old_id.encode()).hexdigest()[:16]


def import_jobs(source, apply=False):
    if not source.exists():
        raise SystemExit(f"Папка или JSON не найдены: {source}")
    paths = sorted(source.glob("*/*.json")) if source.is_dir() else [source]
    existing = {path.stem for path, _, _ in cards()}
    urls = {url_key(data["url"]) for _, data, _ in cards() if data["url"]}
    imported = skipped = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        for job in payload if isinstance(payload, list) else [payload]:
            if not isinstance(job, dict) or not job.get("company") or not job.get("position"):
                skipped += 1
                continue
            old_id, identifier = legacy_id(job, path)
            url = job.get("link") or ""
            try:
                key = url_key(url) if url else ""
            except ValueError:
                key = url = ""
            if identifier in existing or (key and key in urls):
                skipped += 1
                continue
            status = str(job.get("status", "")).strip().casefold()
            data = dict(company=str(job["company"]).strip(), title=str(job["position"]).strip(), location=job.get("location") or "",
                        url=url, status=STATUS.get(status, "review"), updated=date.today().isoformat())
            if job.get("interviewDate"):
                data["interview"] = job["interviewDate"]
            body = (f"\nИсточник: старый job-tracker, запись {old_id}.\n"
                    f"Исходный статус: {status or 'не указан'}. Актуальность статуса требует сверки с журналом.\n")
            if job.get("createdAt"):
                body += f"Создано в старом трекере: {job['createdAt']}.\n"
            if job.get("notes"):
                body += f"\n{job['notes']}\n"
            if apply:
                save(CARDS / f"{identifier}.md", data, body)
            imported += 1
            existing.add(identifier)
            if key:
                urls.add(key)
    print(f"{'Импортировано' if apply else 'Готово к импорту'}: {imported}. Пропущено: {skipped}. JSON-файлов: {len(paths)}.")


def import_documents(source):
    from documents_store import MAX_FILE, attach

    if not source.is_dir():
        raise ValueError("Укажи папку data/jobs исходного трекера.")
    existing = {path.stem for path, _, _ in cards()}
    copied = 0
    for path in sorted(source.glob("*/*.json")):
        job = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(job, dict) or not job.get("company") or not job.get("position"):
            continue
        _, identifier = legacy_id(job, path)
        if identifier not in existing:
            continue
        for key, kind in (("resumePath", "cv"), ("coverLetterPath", "letter")):
            value = job.get(key)
            if not value:
                continue
            folder = path.parent.resolve()
            file = (folder / value).resolve()
            if file.parent != folder or not file.is_file() or file.stat().st_size > MAX_FILE:
                continue
            try:
                attach(identifier, kind, "ready", file.name, file.read_bytes())
                copied += 1
            except ValueError as error:
                print(f"{identifier}: {error}")
    print(f"Скопировано старых вложений: {copied}.")
