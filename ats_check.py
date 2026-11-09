"""ATS- und Normprüfung eines Lebenslaufs gegen eine gespeicherte Stellenanzeige.

Prüft nur, was sich aus Dateien belegen lässt: lesbarer Text, Schlüsselbegriffe der
Anzeige, harte Anforderungen, deutsche CV-Normen und Faktenwächter aus dem Profil.
Fehlende Begriffe sind Hinweise, keine Aufforderung, Kenntnisse zu erfinden.
"""
from datetime import date, datetime
import json
from pathlib import Path
import re

from career import CONFIG, cards

# Begriffe, die in IT-Support-, Anwendungsbetreuungs- und Industrie-IT-Anzeigen häufig
# als ATS-Schlüsselwörter dienen. Schreibweisen werden als Regex geprüft.
KEYWORDS = {
    "1st Level": r"1st(/2nd)?[- ]?level|first[- ]level", "2nd Level": r"2nd[- ]?level|second[- ]level",
    "3rd Level": r"3rd[- ]?level", "Anwendungsbetreuung": r"anwendungsbetreu", "Anwendersupport": r"anwender(support|unterstützung|betreuung)|user[- ]support",
    "Helpdesk / Service Desk": r"helpdesk|service[- ]?desk", "Ticketsystem": r"ticket", "ITIL": r"\bitil\b",
    "Jira": r"\bjira\b", "Confluence": r"confluence", "Active Directory": r"active directory|\bad\b(?=[ ,/-])",
    "Microsoft 365": r"microsoft 365|\bm365\b|office 365|\bo365\b", "Exchange": r"\bexchange\b", "Intune": r"\bintune\b",
    "Windows Server": r"windows[- ]server", "Windows Client": r"windows ?1[01]|windows[- ]client",
    "Linux": r"\blinux\b", "Netzwerk": r"netzwerk|network|lan\b|wlan|tcp/ip", "VPN": r"\bvpn\b",
    "Virtualisierung": r"virtualisier|vmware|hyper-v|proxmox", "Backup / Datensicherung": r"backup|datensicherung",
    "Server": r"\bserver", "Datenbank": r"datenbank|database|\bsql\b", "SQL": r"\bsql\b",
    "Skripting": r"powershell|skript|script|python|bash", "Dokumentation": r"dokumentation|dokumentier",
    "Test / Qualitätssicherung": r"\btest|qualitätssicherung|abnahme", "Anforderungsmanagement": r"anforderung",
    "Schnittstellen": r"schnittstelle|interface|\bapi\b|rest\b", "Fehleranalyse": r"fehleranalyse|störung|troubleshoot|fehlerbehebung",
    "Hardware": r"hardware|drucker|peripherie", "Rollout": r"rollout|roll-out", "Installation": r"installation|install",
    "SCADA": r"scada", "WinCC": r"wincc", "HMI": r"\bhmi\b", "SPS": r"\bsps\b|\bplc\b|s7|tia",
    "Modbus": r"modbus", "Automatisierung": r"automatisierung", "Fachverfahren": r"fachverfahren",
    "Kundenkontakt": r"kunden|customer", "Vor-Ort-Einsatz": r"vor ort|onsite|on-site|außendienst|field service",
    "Git": r"\bgit\b|gitlab|github", "JavaScript": r"javascript", "Führerschein": r"führerschein",
}

# Harte Anforderungen, die vor einer Bewerbung bewusst geprüft werden müssen.
FLAGS = {
    "Deutsch C1/C2 oder verhandlungssicher": r"\bc1\b|\bc2\b|verhandlungssicher|muttersprachlich|perfekte deutsch",
    "Sehr gute Deutschkenntnisse (über B2 möglich)": r"sehr gute\w* deutsch|deutsch\w* (sehr gut|fließend)|fließende\w* deutsch",
    "Fließendes / sehr gutes Englisch": r"(fließend|sehr gute?)\w* englisch|englisch\w* (fließend|verhandlungssicher)|fluent english",
    "Informatikstudium oder Fachinformatiker gefordert": r"studium der informatik|informatikstudium|fachinformatiker|it-ausbildung",
    "Vergleichbare Qualifikation zulässig": r"vergleichbar|quereinst",
    "Schicht / Rufbereitschaft / Wochenende": r"schicht|rufbereitschaft|bereitschaftsdienst|wochenend|nachtarbeit",
    "Reisebereitschaft / Dienstreisen": r"reisebereitschaft|dienstreise|bundesweit",
    "Führerschein gefordert": r"führerschein",
    "Sicherheitsüberprüfung": r"sicherheitsüberprüfung|\bsü ?[123]\b",
    "Telefonischer Support / Hotline": r"telefonisch|hotline|telefon",
    "Homeoffice / mobiles Arbeiten möglich": r"homeoffice|home-office|mobile[sn]? arbeit|remote|hybrid",
}

# Personal fact guards live in sources.json → "cv_rules", so the tool stays generic:
#   "fact_guards":    [[regex, message]]  — wording that would be false for this candidate
#   "should_mention": [[regex, message]]  — strengths the CV ought to show
RULES = CONFIG.get("cv_rules", {})


def read_text(path):
    path = Path(path)
    if path.suffix.lower() in {".txt", ".md"}:
        return path.read_text(encoding="utf-8-sig"), {"pages": None, "images": None}
    if path.suffix.lower() != ".pdf":
        raise ValueError("Für die ATS-Prüfung PDF, TXT oder MD verwenden.")
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ValueError("Für PDF-Prüfung bitte einmal ausführen: python -m pip install pypdf") from error
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    images = 0
    for page in reader.pages:
        try:
            images += len(page.images)
        except Exception:
            pass
    return text, {"pages": len(reader.pages), "images": images}


def find(pattern, text):
    return re.search(pattern, text, re.IGNORECASE) is not None


def check(cv_path, posting_text, company="", cv_meta=None, display_name=None):
    cv_text, meta = read_text(cv_path)
    if cv_meta:
        meta.update(cv_meta)
    lower_cv = cv_text.casefold()
    report = dict(cv=str(cv_path), checked=datetime.now().isoformat(timespec="minutes"), company=company)

    wanted = [name for name, rx in KEYWORDS.items() if find(rx, posting_text)]
    present = [name for name in wanted if find(KEYWORDS[name], cv_text)]
    report["keywords"] = dict(in_posting=wanted, in_cv=present,
                              missing=[n for n in wanted if n not in present],
                              coverage=round(100 * len(present) / len(wanted)) if wanted else None)
    report["posting_flags"] = [name for name, rx in FLAGS.items() if find(rx, posting_text)]

    norms = []
    words = len(cv_text.split())
    if words < 150:
        norms.append("Kaum Text erkannt: PDF ist evtl. ein Bild/Scan. ATS braucht auswählbaren Text.")
    if meta.get("pages") and meta["pages"] > 2:
        norms.append(f"{meta['pages']} Seiten. Für diese Stellen sind 1–2 Seiten üblich.")
    if not re.search(r"\+?\d[\d /-]{7,}", cv_text):
        norms.append("Keine Telefonnummer gefunden.")
    if "@" not in cv_text:
        norms.append("Keine E-Mail-Adresse gefunden.")
    if not re.search(r"\b\d{5}\b", cv_text):
        norms.append("Keine Postleitzahl/Adresse gefunden.")
    if meta.get("images") == 0:
        norms.append("Kein Foto eingebettet. In Deutschland optional, aber verbreitet." +
                     (f" Aktuelles Foto: {RULES['photo']}" if RULES.get("photo") else ""))
    dates = re.findall(r"\b(\d{2})\.(\d{2})\.(20\d{2})\b", cv_text)
    if dates:
        d, m, y = dates[-1]
        try:
            age = (date.today() - date(int(y), int(m), int(d))).days
            if age > 30:
                norms.append(f"Datum unten im CV ist {d}.{m}.{y} ({age} Tage alt). Auf das Versanddatum setzen.")
        except ValueError:
            pass
    else:
        norms.append("Kein Ort/Datum (z. B. „Bonn, TT.MM.JJJJ“) gefunden.")
    for section, rx in (("Berufserfahrung", r"berufserfahrung|beruflicher werdegang"),
                        ("Ausbildung/Studium", r"studium|ausbildung|bildungsweg"),
                        ("Sprachen", r"sprachen|deutsch")):
        if not find(rx, cv_text):
            norms.append(f"Abschnitt „{section}“ nicht erkannt. ATS erwartet klare Standardüberschriften.")
    name = display_name or Path(cv_path).name
    if not re.search(r"lebenslauf|cv", name, re.IGNORECASE):
        norms.append(f"Dateiname „{name}“: besser Lebenslauf_Vorname_Nachname.pdf.")
    report["norms"] = norms

    others = sorted({data["company"].strip() for _, data, _ in cards()
                     if data["company"].strip() and len(data["company"].strip()) >= 4
                     and data["company"].strip().casefold() != company.strip().casefold()
                     and data["company"].strip().casefold() in lower_cv})
    report["foreign_company_mentions"] = others
    report["fact_guards"] = [msg for rx, msg in RULES.get("fact_guards", []) if find(rx, cv_text)]
    report["profile_suggestions"] = [msg for rx, msg in RULES.get("should_mention", []) if not find(rx, cv_text)]
    return report


def as_markdown(r):
    k = r["keywords"]
    out = [f"# ATS-Prüfung · {r['company'] or 'ohne Firma'}", "", f"CV: {r['cv']} · geprüft {r['checked']}", ""]
    if k["coverage"] is not None:
        out.append(f"**Abdeckung der Anzeigenbegriffe: {k['coverage']} %** ({len(k['in_cv'])} von {len(k['in_posting'])})")
    out += ["", "Im CV vorhanden: " + (", ".join(k["in_cv"]) or "—"),
            "Fehlt im CV: " + (", ".join(k["missing"]) or "—"),
            "_Fehlende Begriffe nur ergänzen, wenn die Erfahrung echt ist._", "",
            "## Harte Anforderungen der Anzeige", *[f"- {f}" for f in r["posting_flags"] or ["keine erkannt"]], "",
            "## Faktenwächter", *([f"- ⚠ {f}" for f in r["fact_guards"]] or ["- ok"]), "",
            "## Fremde Firmennamen im CV", *([f"- ⚠ {f}" for f in r["foreign_company_mentions"]] or ["- keine"]), "",
            "## Deutsche CV-Normen", *[f"- {f}" for f in r["norms"] or ["ok"]], "",
            "## Profil besser zeigen", *[f"- {f}" for f in r["profile_suggestions"] or ["ok"]]]
    return "\n".join(out)


def check_card(identifier, cv_path=None, posting_path=None):
    from documents_store import card_record, document_path
    path, data, _ = card_record(identifier)
    docs = data.get("documents", [])
    if cv_path is None:
        cvs = [d for d in docs if d["kind"] == "cv"]
        if not cvs:
            raise ValueError("In der Karte ist kein CV gespeichert. Pfad mit --cv angeben.")
        cv_path = document_path(path.stem, cvs[-1])
        display = cvs[-1]["filename"]
    else:
        display = None
    if posting_path is None:
        posts = [d for d in docs if d["kind"] == "posting"]
        if not posts:
            raise ValueError("In der Karte ist kein Anzeigentext gespeichert. Pfad mit --posting angeben.")
        posting_path = document_path(path.stem, posts[-1])
    posting, _ = read_text(posting_path)
    return check(cv_path, posting, data["company"], display_name=display)


if __name__ == "__main__":
    import sys
    print(json.dumps(check(sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
