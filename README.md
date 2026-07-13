# Tender Board Activity Agent

סוכן שמתחבר ל-MongoDB, שולף לוגי פעילות של לוח המכרזים (Tender Board),
מסווג וסופר אותם לפי סוג פעולה, ומציג דו"ח קריא ב-CLI.

התהליך בנוי כגרף LangGraph: `fetch -> classify -> report`.

## התקנה

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell. ב-bash/Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

## הגדרת סביבה

יש קובץ `.env.example` בשורש הפרויקט שמתעד את כל המשתנים הנדרשים (עם
הסברים, בלי סודות אמיתיים). מעתיקים אותו ל-`.env` ומכניסים ערכים אמיתיים:

```bash
cp .env.example .env      # bash / Git Bash
copy .env.example .env    # Windows cmd
```

```env
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster-url>/?appName=Cluster0
MONGODB_DB_NAME=test
MONGODB_COLLECTION_NAME=applicationlogs
MONGODB_DATE_FIELD=timestamp
MONGODB_TENDER_KEYWORD=tender
```

⚠️ `.env` נמצא ב-`.gitignore` ולעולם לא נכנס ל-git — רק `.env.example`
(שאינו מכיל סודות) נשמר בריפו.

## הרצה

```bash
# 30 הימים האחרונים (ברירת מחדל)
python -m tender_agent.cli

# מספר ימים מותאם
python -m tender_agent.cli --days 60

# טווח תאריכים מפורש
python -m tender_agent.cli --start 2026-01-01 --end 2026-12-31
```

**פלט לדוגמה:**

```
Tender Board Activity Summary
Period: 2026-01-01 to 2026-12-31
----------------------------------------
CREATE      :      7
REGISTER    :     12
EDIT        :      4
DELETE      :      0
VIEW        :    421
----------------------------------------
OTHER       :      7   (log lines not tied to a tender action)
INVALID     :      0   (malformed/unreadable log records)
----------------------------------------
TOTAL       :    451
```

קוד יציאה `0` = הצלחה, `1` = שגיאת חיבור/שליפה (למשל URI שגוי) — הדו"ח
עדיין יודפס, עם הודעת `ERROR` במקום הספירות.

## הרצת בדיקות

```bash
python -m pytest tests/ -v
```

הבדיקות רצות כולן מול `mongomock` (מסד מדומה בזיכרון) — לא נוגעות במסד האמיתי.

## מבנה הפרויקט

```
tender_agent/
├── db.py           # SCRUM-37: חיבור ל-Mongo ושליפת לוגים לפי טווח תאריכים
├── classify.py      # SCRUM-38: סיווג כל רשומה + ספירה לפי קטגוריה
├── report.py         # SCRUM-39: פורמט הדו"ח הקריא
├── graph.py           # SCRUM-39: הגרף (LangGraph) שמחבר את כל השלבים
└── cli.py               # SCRUM-39: נקודת הכניסה בפועל - הרצת CLI
tests/                    # בדיקות יחידה לכל מודול, ללא חיבור אמיתי ל-DB
```

## מקור הנתונים

הלוגים נשלפים מ-`test.applicationlogs` - קולקשן לוגים **כללי** של כל
האפליקציה (winston), לא ייעודי ללוח המכרזים. הסינון לרשומות רלוונטיות
נעשה לפי הימצאות המילה `"tender"` בשדה `message` (case-insensitive).

## AI / LLM

אין כרגע שימוש בפועל ב-AI/LLM בקוד. LangChain/LangGraph משמשים כאן
כתשתית תזמון (orchestration) בלבד. שילוב LLM אמיתי לניתוח סמנטי של
תוכן מכרזים הוא סטורי עתידי (SCRUM-57), מחוץ לתחום הספרינט הנוכחי.
