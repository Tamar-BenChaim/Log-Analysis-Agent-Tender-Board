מצב נוכחי
מה יש: בשלושת מקומות קריאת ה-LLM נכתב קוד שרושם זוג start/end בפורמט JSON קבוע (run_id, node, node_type, invocation_id, phase, status, duration_ms וכו'):

agent/nodes/llm_classifier.py - logger = logging.getLogger(__name__) (שם הלוגר: agent.nodes.llm_classifier)
agent/nodes/analyze.py - (agent.nodes.analyze)
agent/graph.py - (agent.graph)
הבעיה: אף אחד מהלוגרים האלה לא מחובר לשום handler בפועל. ב-Python, אם לא קוראים ל-logging.basicConfig(...) (או מגדירים handler ידנית), הרמה האפקטיבית של ה-root logger היא WARNING, ואין שום handler שמדפיס/שומר כלום. המשמעות בפועל:

רמת הלוג	מה קורה היום ב-python -m agent.cli chat/report
logger.info(...) (רשומות start + end של הצלחה)	נעלם לגמרי - לא מודפס, לא נשמר בשום מקום
logger.error(...) (רשומות end של כישלון)	מודפס ל-stderr, אבל רק בגלל מנגנון ברירת המחדל של Python (logging.lastResort) - לא בפורמט מבוקר, ולא משהו שתכננו במפורש
בבדיקות (pytest) זה כן עובד ונראה תקין, כי caplog.set_level(...) בטסטים עוקף את הבעיה הזו ומאזין ישירות ללוגר - זו הסיבה שהטסטים עוברים למרות שבפועל, בהרצה אמיתית, כמעט כלום לא מוצג.

מה חסר כדי שזה "יעבוד" בפועל
יש כאן שתי שכבות נפרדות, בסדר עדיפות:

1. הכרחי ומיידי - הגדרת handler (בלי זה, שום דבר לא נראה)
צריך קריאה אחת ל-logging.basicConfig(level=logging.INFO) (או הגדרת handler מפורש) שתופעל פעם אחת בתחילת ריצה. המקום הטבעי: agent/cli.py, בפונקציית main() - לפני קריאה ל-run_report/run_chat. זה ה"חלק הראשון" של אותו agent_logger.py העתידי (המקביל ל-configure_logging()/ה-Console transport ב-agentsLogger.ts), אבל אפשר לממש אותו כרגע כשורה אחת פשוטה ב-cli.py בלי לבנות עדיין את כל המודול - זה ייתן לך תוצאה מוחשית (רואים JSON בקונסולה) בלי לקבל החלטות ארכיטקטוניות גדולות.

2. עדיין לא הוחלט/לא ממומש - שמירה אמיתית ל-DB
זה החלק שדיברנו עליו קודם ונשאר פתוח במכוון: כתיבה בפועל ל-MongoDB (או קריאה ל-endpoint של השרת ה-Node) דורשת:

קובץ agent_logger.py (או שם אחר) שמרכז את הלוגיקה הזו
החלטה: pymongo ישיר (connection string + schema של AgentLog) או HTTP ל-Node
ברגע שתחליטי - שלושת בלוקי הקוד המשוכפלים היום יוחלפו בקריאה למודול הזה
3. מגבלה ידועה שנשארה פתוחה
run_id כרגע נוצר בנפרד בכל קריאת AI (לא משותף בין guardrails ו-agent באותו תור) - מסומן ב-# TODO בקוד, ממתין לאותה החלטה עתידית.

בקיצור: אם רק רוצה לראות את הלוגים עכשיו (בלי DB) - צריך שורה אחת ב-agent/cli.py. אם רוצה שמירה אמיתית - זה agent_logger.py + החלטת ארכיטקטורה שעדיין לא נסגרה.