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
MONGODB_TENDER_BOARD_MODULE=tenderBoard

# נדרש רק למצב chat (SCRUM-174) - הסוכן קורא ל-OpenAI (ChatOpenAI) לבחירת
# tools ולניסוח תשובות. מצב report לא צריך את זה בכלל.
OPENAI_API_KEY=sk-...
```

⚠️ `.env` נמצא ב-`.gitignore` ולעולם לא נכנס ל-git — רק `.env.example`
(שאינו מכיל סודות) נשמר בריפו.

## הרצה

```bash
# 30 הימים האחרונים (ברירת מחדל)
python -m agent.cli report

# מספר ימים מותאם
python -m agent.cli report --days 60

# טווח תאריכים מפורש
python -m agent.cli report --start 2026-01-01 --end 2026-12-31

# מצב צ'אט אינטראקטיבי - שאלות חופשיות על הלוגים (SCRUM-174)
python -m agent.cli chat
```

**דוגמת שימוש ב-chat:**

```
Tender Board chat - ask a question, or type 'exit'/'quit' to leave.
> כמה מכרזים נוצרו בין 2026-06-16 ל-2026-07-16, לפי יום?
The tender creation events between 2026-06-16 and 2026-07-16, grouped by day, are as follows:
- 2026-07-09: 3 events
- 2026-07-12: 4 events
...
> exit
```

הסוכן בוחר בעצמו איזה tool להריץ (`get_error_count`, `get_request_trace`,
`get_user_activity`, `find_duplicate_tenders_tool`, `get_latency_stats`,
`get_tender_creation_volume`) לפי השאלה שנשאלה, וממשיך בלולאה עד שיש לו
תשובה סופית. תוצאות tools (למשל טקסט חופשי מ-`get_request_trace`) עוברות
סינון prompt-injection (`agent/nodes/guardrail.py`) לפני שהן חוזרות למודל.

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

**סעיף "AI Analysis" (SCRUM-180):** אחרי `errors`, הדוח מריץ קריאת LLM
אחת (`ChatOpenAI`) שמקבלת את הסיכום המספרי + מדגם קטן ומסונן של תוכן
עסקי אמיתי (כותרות מכרזים, הודעות שגיאה — לאחר סינון
prompt-injection ע"י `guardrail_node`), ומחזירה הערכת הגיון עסקי,
דפוסי שגיאה, ואנומליות בשפה טבעית. `evaluator_node` בודק שהתשובה
עקבית מול הנתונים בפועל (לא "ממציאה" כפילות/שגיאות שלא קיימות) ולא
מאשר תשובה עם ביטחון (`confidence`) נמוך מ-0.5; במקרה כשל — עד 3
ניסיונות חוזרים ל-`analyze_node`, ואם גם אז לא אושר, הסעיף מציין
שהניתוח לא זמין באותה הרצה (הספירות/שגיאות/אנומליות עדיין מוצגות
במלואן — רק הניתוח החכם חסר).

## הרצת בדיקות

```bash
python -m pytest tests/ -v
```

הבדיקות רצות כולן מול `mongomock` (מסד מדומה בזיכרון) — לא נוגעות במסד האמיתי.

## מבנה הפרויקט

```
agent/
├── graph.py             # שני הגרפים: report graph (עם fan-out מקבילי) + chat graph
├── nodes/
│   ├── fetch.py          # (SCRUM-37/161) שליפת לוגים + מבנה מועשר (module/requestId/...)
│   ├── classify.py        # (SCRUM-38/161) סיווג כל רשומה + ספירה לפי קטגוריה
│   ├── report.py           # (SCRUM-39/166/180) פורמט הדו"ח + שגיאות/אנומליות/AI Analysis
│   ├── stats.py             # (SCRUM-166) latency + זיהוי כפילויות
│   ├── errors.py             # (SCRUM-166) קיבוץ שגיאות עם דה-דופ לפי requestId
│   ├── guardrail.py           # (SCRUM-174/180) סינון prompt-injection + איסוף מדגם תוכן
│   ├── analyze.py              # (SCRUM-180) קריאת LLM אחת לניתוח עומק
│   └── evaluator.py             # (SCRUM-180) בדיקת תקינות + לולאת ניסיון חוזר
├── tools.py                       # (SCRUM-174) 6 ה-tools למצב צ'אט
└── cli.py                          # נקודת הכניסה בפועל - subcommands report/chat
evals/                                # מדידת עלות/טוקנים/זמן/נכונות (SCRUM-184)
tests/                                  # בדיקות יחידה לכל מודול, ללא חיבור אמיתי ל-DB/LLM
```

מבנה זה עודכן ב-SCRUM-170 (במקום `tender_agent/` הקודם) כדי להתאים לפורמט הנדרש לפרויקט הסוכן.

## מקור הנתונים

הלוגים נשלפים מ-`test.applicationlogs` - קולקשן לוגים **כללי** של כל
האפליקציה (winston), לא ייעודי ללוח המכרזים. הסינון לרשומות רלוונטיות
נעשה לפי הימצאות המילה `"tender"` בשדה `message` (case-insensitive).

## AI / LLM

מצב `report` לא משתמש ב-LLM כלל (סיווג מבוסס-חוקים בלבד). מצב `chat`
(SCRUM-174) כן: `agent_node` קורא ל-`ChatOpenAI` (gpt-4o-mini) כדי לבחור
tools ולנסח תשובות. ה-provider הוא OpenAI, לא Anthropic, למרות ש-
`requirements.txt` עדיין כולל את `anthropic`/`langchain-anthropic`
מהתשתית המקורית - הוחלט לעבור ל-OpenAI כי זה המפתח הזמין בסביבה הזו.
ניתוח סמנטי עמוק יותר של תוכן מכרזים (`analyze_node`) הוא SCRUM-180.
