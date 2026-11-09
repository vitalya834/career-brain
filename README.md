# Career Brain

Локальный помощник для поиска работы в Германии: поиск вакансий в нескольких источниках, сверка с собственной историей откликов, карточки в Markdown (Obsidian), хранение CV, писем и копий объявлений, контекст для ИИ и ATS-проверка резюме по немецким нормам.

*A local job-search assistant for Germany: multi-source discovery, duplicate checks against your own application log, Markdown/Obsidian cards, document storage, AI context export and an ATS/German-CV-norm check. Python standard library plus `pypdf`; everything stays on your computer.*

## Зачем

- Не отправлять повторно туда, где уже был отклик или отказ.
- Видеть в одном месте вакансии из Bundesagentur für Arbeit (Jobsuche API), Arbeitnow, Greenhouse-досок работодателей и каталога Work in Germany.
- Хранить к каждой вакансии текст объявления, отправленное CV и письмо.
- Перед отправкой проверить CV: читается ли текст в ATS, какие ключевые слова вакансии в нём есть, какие жёсткие требования стоят в объявлении и не попало ли в CV что-то неверное.

## Быстрый старт

Нужен Python 3.11+.

```bash
git clone https://github.com/vitalya834/career-brain.git
cd career-brain
python -m pip install -r requirements.txt     # только для проверки PDF
cp sources.example.json sources.json          # Windows: copy sources.example.json sources.json
python -X utf8 career.py serve                # открыть http://127.0.0.1:8765
```

На Windows можно просто запустить `Start Career Brain.cmd`.

Без `sources.json` программа работает на примерах из `examples/`, так что всё можно попробовать сразу.

## Настройка `sources.json`

| Ключ | Что это |
|---|---|
| `history` | Папка с твоими заметками об откликах (например, хранилище Obsidian). Относительный путь считается от папки проекта. |
| `history_files` | Файлы в этой папке, по которым идёт сверка. По умолчанию `Applications Log.md`, `Companies.md`. |
| `additional_history` | Ещё файлы с историей. |
| `candidate_profile` | JSON с проверенными фактами о себе. Шаблон: `examples/candidate_profile.example.json`. |
| `employer_boards` | Greenhouse-доски работодателей: `{"token": "Название"}`. |
| `search_presets`, `queries` | Готовые поиски для панели. |
| `cv_rules` | Личные правила для ATS-проверки: `fact_guards` — формулировки, которые для тебя неверны; `should_mention` — что CV обязательно должно показать; `photo` — путь к актуальному фото. |

`sources.json`, карточки и документы перечислены в `.gitignore` и в репозиторий не попадают.

## Команды

```bash
python -X utf8 career.py discover "IT Support | Anwendungsbetreuung" "Köln | Bonn"
python -X utf8 career.py history "Firma"            # строки журнала по компании
python -X utf8 career.py add "Firma GmbH" "IT Support" "Köln" "https://…" "заметка"
python -X utf8 career.py status ID applied "отправлено 09.11, письмо-подтверждение"
python -X utf8 career.py brief ID                   # контекст для ИИ: карточка + документы + профиль
python -X utf8 career.py ats ID                     # ATS-проверка последнего CV против объявления в карточке
python -X utf8 career.py ats --cv Lebenslauf.pdf --posting vacancy.txt --json
python -X utf8 career.py jobspy-csv jobs.csv        # сверить выгрузку JobSpy с историей
python -X utf8 career.py import-legacy PATH [--apply]   # импорт из job-tracker (data/jobs)
python -X utf8 career.py watch                      # автопоиск: только новые вакансии
python -X utf8 career.py dashboard                  # статичный Career Brain.html
```

### Что показывает `discover`

Для каждой вакансии: источник с запросом и городом, сохранена ли уже карточка, есть ли компания в истории и в каком состоянии (`applied`, `rejected`, `inquiry`, `excluded`, `mentioned`) со ссылкой на строку журнала. Для Arbeitsagentur добавляются номер, дата публикации, начало работы, зарплата, расстояние и пометка Quereinstieg. Нерелевантные совпадения Arbeitsagentur отсеиваются (`filtered_out`).

### Что проверяет `ats`

- **Читаемость:** есть ли в PDF выделяемый текст, а не картинка.
- **Ключевые слова:** доля терминов вакансии, найденных в CV (немецкий IT-словарь: 1st/2nd Level, Active Directory, M365, ITIL, Ticketsystem, SCADA, SPS и др.).
- **Жёсткие требования объявления:** C1, Fachinformatiker или «vergleichbar», смены и Rufbereitschaft, поездки, Sicherheitsüberprüfung, телефонная поддержка, homeoffice.
- **Немецкие нормы CV:** 1–2 страницы, телефон, e-mail, адрес, фото (необязательно), «Ort, Datum» не старше месяца, стандартные разделы, имя файла.
- **Чужие компании:** не осталось ли в CV названия другой фирмы из твоих карточек.
- **Проверка фактов** по твоим `cv_rules`.

Недостающие ключевые слова — только подсказка. Добавлять их в CV можно только при реальном опыте.

## Автопоиск по расписанию

- **Включить (Windows):** запусти `Install Daily Search.cmd`. Он создаёт в Планировщике задачу «Career Brain Watch» на 08:30 и 14:30 и сразу делает первый запуск. Если компьютер был выключен, запуск произойдёт при включении.
- **Разовый запуск:** `Run Watch.cmd` или `python -X utf8 career.py watch`. Проба без записи: `career.py watch --dry-run`. Linux/macOS: добавь `career.py watch` в cron.
- **Выключить:** `Remove Daily Search.cmd`.
- **Что делает запуск:** идёт по списку `sources.json → watch.queries × watch.places` в Arbeitsagentur, оставляет только вакансии, которых ещё не было, открывает полный текст каждой и пишет заметку `Vault/Находки/ГГГГ-ММ-ДД.md`.
- **Как разложены находки:** «Проверить в первую очередь»; «Есть стоп-требование, агентство или истёк срок»; «Компания уже есть в журнале» со ссылкой на строку журнала.
- **Контроль:** блок «Автопоиск» в панели и `career.py watch --status` показывают последний запуск и предупреждают, если его не было больше 36 часов. Журнал — `watch.log`.
- **Сбои сети:** каждый запрос повторяется до трёх раз; если не ответил ни один, запуск считается неудачным, а список виденных вакансий не меняется.

## Данные и приватность

- Всё работает локально, сервер слушает только `127.0.0.1`, формы защищены токеном.
- Наружу уходят только поисковые запросы к перечисленным порталам.
- Карточки — обычный Markdown в `Vault/Вакансии`, документы — в `Vault/Документы/<id>/`.
- Программа ничего не отправляет работодателям. Отклики ты отправляешь сам, а статус меняешь по факту.

## Для ИИ-агентов

Правила работы агента — в `AGENTS.md`. Коротко: сначала история, потом объявление работодателя. Совпадение компании — повод сверить, а не отказ. Факты кандидата брать только из профиля. Ничего не отправлять без явной просьбы.

## Планы

- Генератор CV и Anschreiben в PDF из профиля, с несколькими шаблонами.
- Тесты (pytest) и CI.
- Больше источников: EURES, Interamt, Personio/Softgarden-ленты работодателей.
- Перевод интерфейса на немецкий и английский.

Pull requests и issues приветствуются.

## Лицензия

MIT
