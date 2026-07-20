# Tender Board Activity Agent

סוכן שמתחבר ל-MongoDB, שולף לוגי פעילות של לוח המכרזים (Tender Board),
מסווג וסופר אותם לפי סוג פעולה, ומציג דו"ח קריא ב-CLI.

התהליך בנוי כשני גרפי LangGraph: report graph
(`fetch -> classify/stats/errors (מקבילי) -> aggregate -> guardrail ->
analyze -> evaluator -> report`) ו-chat graph אינטראקטיבי (`topic_guardrail/
security_guardrail (מקבילי) -> guardrail_gate -> agent -> tool -> agent ->
...`). שניהם מפורטים למטה.

## התקנה

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell. ב-bash/Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
```

לפיתוח/הרצת בדיקות/אריזה כ-exe (כולל `pyinstaller`, ראו "אריזה" למטה):

```bash
pip install -r requirements-dev.txt
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

# נדרש גם במצב report (analyze_node, SCRUM-180 - ניתוח עומק פעם אחת
# בכל הרצה) וגם במצב chat (agent_node, SCRUM-174 - בחירת tools וניסוח
# תשובות). שני המצבים משתמשים ב-ChatOpenAI (gpt-4o-mini).
OPENAI_API_KEY=sk-...

# רק אם מתקבלת שגיאת SSL: CERTIFICATE_VERIFY_FAILED בקריאות ל-OpenAI
# (רשת עם TLS interception - אותה בעיה בדיוק ש-NODE_TLS_REJECT_UNAUTHORIZED
# פותר לבקאנד ה-Node). ברירת מחדל: לא מוגדר/false - אימות SSL תקין ומלא.
OPENAI_DISABLE_SSL_VERIFY=false
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

**לפני שהודעת המשתמש בכלל מגיעה לסוכן**, היא עוברת דרך שני guardrails
נפרדים מבוססי-LLM שרצים **במקביל** (`topic_guardrail`, `security_guardrail`
- ראו "מבנה הפרויקט" למטה): הראשון בודק שהשאלה אכן קשורה למכרזים
(חוסם small-talk/שאלות כלליות לא-קשורות), השני בודק ניסיונות
prompt-injection ("התעלם מההוראות הקודמות" וכו') או בקשות שחשודות
כניסיון לשליפה מסוכנת/לא-מורשית מהדאטהבייס. **שני ה-guardrails חייבים
לאשר** - אם אחד מהם חוסם, הסוכן (`agent_node`) לא נקרא כלל, והמשתמש
מקבל הודעת סירוב במקום. ברירת המחדל של שניהם היא **fail-closed**: תקלת
רשת/LLM ממושכת מול OpenAI תחסום גם היא את הפנייה (לאחר ניסיון חוזר יחיד
על כשל רשת חולף) - ראו `agent/nodes/llm_classifier.py`.

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

## Evals ל-analyze_node (SCRUM-184)

```bash
python -m evals.eval          # ברירת מחדל: תשובת LLM מוקלטת מראש (evals/fixtures/*.recorded_response.json), חינמי ודטרמיניסטי
python -m evals.eval --live    # קריאה אמיתית ל-ChatOpenAI לכל fixture - עלות אמיתית, לדגימה תקופתית בלבד
```

מריץ כל תרחיש קבוע תחת `evals/fixtures/` (`normal_activity`,
`duplicate_tender_burst`, `error_spike`, `prompt_injection_attempt`)
דרך גרף הדוח המלא, ובודק את התוצאה מול הציפיות המתועדות
(`*.expected.json`): ספירות, מספר כפילויות, האם ה-guardrail הופעל,
מילות מפתח שצריכות להופיע בניתוח, והאם ה-evaluator אישר את התשובה.
פלט: שורה אחת לכל fixture תחת `evals/results/eval_<timestamp>.csv`
(עמודות: `fixture_name, passed, attempts, tokens_in, tokens_out,
cost_usd, latency_ms, notes`). קבצי ה-CSV עצמם לא נכנסים ל-git
(`evals/results/*.csv` ב-`.gitignore`) — רק ה-fixtures הם קוד מקור.

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
│   ├── guardrail.py           # (SCRUM-174/180) סינון prompt-injection + איסוף מדגם תוכן (על תוצאות tools/דוח)
│   ├── llm_classifier.py       # helper משותף ל-topic/security guardrail: קריאת LLM -> JSON -> fallback בטוח
│   ├── topic_guardrail.py       # guardrail על קלט המשתמש בצ'אט: חוסם שאלות לא-קשורות למכרזים
│   ├── security_guardrail.py     # guardrail על קלט המשתמש בצ'אט: חוסם prompt-injection/שליפה מסוכנת
│   ├── analyze.py              # (SCRUM-180) קריאת LLM אחת לניתוח עומק
│   └── evaluator.py             # (SCRUM-180) בדיקת תקינות + לולאת ניסיון חוזר
├── tools.py                       # (SCRUM-174) 6 ה-tools למצב צ'אט
└── cli.py                          # נקודת הכניסה בפועל - subcommands report/chat
evals/
├── eval.py                              # (SCRUM-184) מריץ fixtures דרך הגרף, מודד עלות/טוקנים/זמן/נכונות
├── fixtures/                             # תרחישים קבועים: records + expected + recorded_response
└── results/                               # פלט CSV (מתעלם ב-git, חוץ מ-.gitkeep)
tests/                                  # בדיקות יחידה לכל מודול, ללא חיבור אמיתי ל-DB/LLM
```

מבנה זה עודכן ב-SCRUM-170 (במקום `tender_agent/` הקודם) כדי להתאים לפורמט הנדרש לפרויקט הסוכן.

## מקור הנתונים

הלוגים נשלפים מ-`test.applicationlogs` - קולקשן לוגים **כללי** של כל
האפליקציה (winston), לא ייעודי ללוח המכרזים. הסינון לרשומות רלוונטיות
נעשה לפי הימצאות המילה `"tender"` בשדה `message` (case-insensitive).

## AI / LLM

מצב `report` בסיווג (`classify_node`) לא משתמש ב-LLM כלל (מבוסס-חוקים
בלבד) - אבל כן מריץ קריאת LLM אחת ל-`analyze_node` (SCRUM-180). מצב
`chat` (SCRUM-174) משתמש ביותר מקריאת LLM אחת לכל תור: `topic_guardrail`
ו-`security_guardrail` רצים במקביל ובודקים את הודעת המשתמש לפני שהיא
מגיעה לסוכן, ורק אם שניהם אישרו `agent_node` קורא ל-`ChatOpenAI`
(gpt-4o-mini) כדי לבחור tools ולנסח תשובות - כלומר תור עם קריאת tool
אחת הוא בפועל 4 קריאות LLM (שני ה-guardrails במקביל + 2 קריאות סוכן),
לא אחת. ה-provider הוא OpenAI, לא Anthropic,
למרות ש-`requirements.txt` עדיין כולל את `anthropic`/`langchain-anthropic`
מהתשתית המקורית - הוחלט לעבור ל-OpenAI כי זה המפתח הזמין בסביבה הזו
(ראו גם `manifest.json`: `technical_specifications.llm_provider`).

## אריזה כקובץ הרצה עצמאי (SCRUM-188)

```bash
pip install -r requirements-dev.txt
pyinstaller --name tender-agent --onefile run_cli.py
```

מייצר `dist/tender-agent.exe` - קובץ יחיד, ללא תלות ב-Python מותקן.
`run_cli.py` הוא נקודת כניסה דקה בשורש הפרויקט (לא `agent/cli.py`
ישירות) - כדי ש-PyInstaller יריץ ניתוח מה-root של הפרויקט וייבוא
`agent.*` יעבוד; הרצת PyInstaller ישירות על קובץ שבתוך החבילה עצמה
הייתה שוברת את פענוח ה-imports היחסיים.

**הרצה בפועל:**

```bash
dist/tender-agent.exe report --days 30
dist/tender-agent.exe chat
```

יש להעתיק `.env` אמיתי (עם `MONGODB_URI`/`OPENAI_API_KEY`) לצד ה-exe
(או להריץ מהתיקייה שמכילה אותו) - **לעולם לא** `.env` עם סודות אמיתיים
נכנס ל-git, רק `.env.example`. נבדק ידנית מקצה לקצה: ה-exe מתחבר
בהצלחה ל-Mongo האמיתי ומריץ את כל הגרף (fetch/classify/stats/errors/
guardrail) ללא בעיה.
