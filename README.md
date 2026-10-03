# Academic Guardian

Intelligent Student Academic Monitoring and Support System (synthetic demo data).

## Run locally
```
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://localhost:8000. Data is seeded automatically; "Reset demo data" on the login screen reseeds it.

Optional: `export ANTHROPIC_API_KEY=...` makes voice parsing use an LLM. Without it, the built-in parser is used.

## Deploy (Render, free)
1. Push this folder to GitHub (`academic-gaurdian2`).
2. Render > New Web Service > connect the repo.
3. Build: `pip install -r requirements.txt` Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. (Optional) add env var `ANTHROPIC_API_KEY`. Open the URL on a laptop (Chrome) and a phone.

## Demo script (3 min)
1. Login as **Priya Sharma**: 77.5% now, projected 65%, "attend the next 10 classes". Medium risk with reasons.
2. Open a **phone** logged in as Priya (Notifications visible).
3. Laptop: login as **Prof. Nair**, pick *Database Systems - Quiz 2*, click the mic and say
   "Arjun eighteen, Priya twenty two, Rahul nine". Show the ambiguous "Arjun" (Kumar vs Singh) in the confirm table, fix it, confirm.
4. Phone buzzes with Priya's mark. Press "Report possible error" to show the error loop back to the professor.
5. Login as **Admin**: risk heatmap (Rahul = High).

## What is covered
Attendance prediction and 75% alerts, mark notifications (with old to new value), voice mark entry with fuzzy matching and confirmation, per-subject risk scoring with top factors and insights, student / professor / admin views.

## Next steps (say in the pitch)
Plug into the real portal DB, SMS/WhatsApp alerts, professor attendance entry, CSV import, ML models trained on real history.
