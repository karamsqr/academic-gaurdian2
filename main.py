import os, re, json, math, random, sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from rapidfuzz import process, fuzz

DB = os.environ.get("DB_PATH", "academic.db")
TOTAL = 60  # classes per course in the semester
app = FastAPI(title="Academic Guardian")

SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, role TEXT, student_id INTEGER);
CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, roll TEXT, prev_pct REAL);
CREATE TABLE courses(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE enrollments(student_id INTEGER, course_id INTEGER);
CREATE TABLE attendance(student_id INTEGER, course_id INTEGER, cls INTEGER, present INTEGER);
CREATE TABLE assessments(id INTEGER PRIMARY KEY, course_id INTEGER, name TEXT, max_marks REAL, kind TEXT);
CREATE TABLE marks(student_id INTEGER, assessment_id INTEGER, score REAL);
CREATE TABLE mark_history(student_id INTEGER, assessment_id INTEGER, old REAL, new REAL, at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE notifications(id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, read INTEGER DEFAULT 0, at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

# name, previous-year %, (attended of first 30, attended of last 10, marks %)
PERSONAS = [
    ("Priya Sharma", 78, (27, 4, 55)),   # 77.5% and falling: the attendance story
    ("Rahul Verma", 50, (24, 3, 38)),    # 67.5% + low marks: High risk
    ("Arjun Kumar", 85, (29, 10, 88)),   # healthy contrast
    ("Arjun Singh", 65, (25, 8, 60)),    # same first name: disambiguation demo
    ("Meena Iyer", 90, (28, 9, 92)),
    ("Karthik Raj", 72, (26, 8, 66)),
    ("Sneha Reddy", 80, (27, 9, 78)),
    ("Vikram Patel", 60, (22, 6, 50)),
    ("Ananya Das", 88, (29, 10, 90)),
    ("Imran Khan", 70, (25, 7, 64)),
]


def q(sql, args=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql, args).fetchall()
    con.close()
    return rows


def w(sql, args=()):
    con = sqlite3.connect(DB)
    cur = con.execute(sql, args)
    con.commit()
    i = cur.lastrowid
    con.close()
    return i


def seed():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    rnd = random.Random(7)
    cs = [con.execute("INSERT INTO courses(name) VALUES(?)", (n,)).lastrowid
          for n in ("Database Systems", "Operating Systems", "Engineering Maths")]
    ax = {}
    for c in cs:
        ax[c] = [(con.execute("INSERT INTO assessments(course_id,name,max_marks,kind) VALUES(?,?,?,?)",
                              (c, n, m, k)).lastrowid, m)
                 for n, m, k in (("Quiz 1", 25, "quiz"), ("Assignment 1", 10, "assignment"),
                                 ("Midterm", 50, "exam"), ("Quiz 2", 25, "quiz"))]
    con.execute("INSERT INTO users(username,name,role) VALUES('prof','Prof. Nair','prof')")
    con.execute("INSERT INTO users(username,name,role) VALUES('admin','Admin Office','admin')")
    for i, (name, prev, (e, r, mp)) in enumerate(PERSONAS):
        sid = con.execute("INSERT INTO students(name,roll,prev_pct) VALUES(?,?,?)",
                          (name, f"S{i+1:02d}", prev)).lastrowid
        con.execute("INSERT INTO users(username,name,role,student_id) VALUES(?,?,?,?)",
                    (f"s{i+1:02d}", name, "student", sid))
        for ci, c in enumerate(cs):
            con.execute("INSERT INTO enrollments VALUES(?,?)", (sid, c))
            ee = e if ci == 0 else max(0, min(30, e + rnd.randint(-2, 2)))
            early = [1] * ee + [0] * (30 - ee)
            rnd.shuffle(early)
            recent = [1] * r + [0] * (10 - r)
            rnd.shuffle(recent)
            for k, p in enumerate(early + recent):
                con.execute("INSERT INTO attendance VALUES(?,?,?,?)", (sid, c, k, p))
            for aid, m in ax[c][:3]:  # Quiz 2 left empty for the live demo
                sc = round(m * min(1, max(0, mp / 100 + rnd.uniform(-.08, .08))))
                con.execute("INSERT INTO marks VALUES(?,?,?)", (sid, aid, sc))
    con.commit()
    con.close()


# ---------------- engines ----------------
def att(sid, cid):
    rows = [r["present"] for r in q(
        "SELECT present FROM attendance WHERE student_id=? AND course_id=? ORDER BY cls", (sid, cid))]
    t, a = len(rows), sum(rows)
    R = max(TOTAL - t, 0)
    cur = 100 * a / t if t else 100.0
    rr = sum(rows[-10:]) / len(rows[-10:]) if rows else 1
    proj = 100 * (a + rr * R) / (t + R)
    status = "red" if cur < 75 else "amber" if (cur < 80 or proj < 75) else "green"
    need = max(0, math.ceil(3 * t - 4 * a))
    safe = max(0, math.floor((4 * a - 3 * t) / 3))
    if cur < 75:
        msg = "Below 75%. " + (f"Attend the next {need} classes in a row to recover."
                               if t + need <= TOTAL else "Recovery is no longer possible - meet your coordinator.")
    elif proj < 75:
        n = next(n for n in range(R + 1) if (a + n + rr * (R - n)) / (t + R) >= .75)
        msg = f"At your recent pace you will finish at {proj:.0f}%. Attend the next {n} classes to stay safe."
    else:
        msg = f"On track. You can still miss {safe} classes."
    return {"current": round(cur, 1), "projected": round(proj, 1), "status": status, "msg": msg, "safe_to_miss": safe}


W = {"Attendance": .30, "Assessment marks": .35, "Assignments": .15, "Previous performance": .20}
INSIGHT = {
    "Attendance": "attend the upcoming classes consistently.",
    "Assessment marks": "revise recent topics and meet the professor for help.",
    "Assignments": "submit pending assignments on time.",
    "Previous performance": "strengthen fundamentals with extra practice.",
}


def risk(s, cid, a):
    ms = q("SELECT m.score, x.max_marks mx, x.kind FROM marks m JOIN assessments x ON x.id=m.assessment_id "
           "WHERE m.student_id=? AND x.course_id=?", (s["id"], cid))

    def pct(rs):
        return 100 * sum(r["score"] for r in rs) / sum(r["mx"] for r in rs) if rs else None

    cl = lambda x: max(0, min(100, x))
    allp, asg = pct(ms), pct([r for r in ms if r["kind"] == "assignment"])
    gaps = {"Attendance": cl((85 - a["current"]) / 20 * 100),
            "Assessment marks": cl((75 - allp) / 40 * 100) if allp is not None else 0,
            "Assignments": cl((75 - asg) / 40 * 100) if asg is not None else 0,
            "Previous performance": cl((80 - s["prev_pct"]) / 40 * 100)}
    contrib = {k: W[k] * gaps[k] for k in W}
    score = sum(contrib.values())
    top = sorted(contrib, key=contrib.get, reverse=True)[:2]
    level = "High" if score > 65 else "Medium" if score >= 35 else "Low"
    insight = ("Focus on: " + "; ".join(INSIGHT[t] for t in top)) if level != "Low" else "Keep it up."
    return {"score": round(score), "level": level, "top": top, "insight": insight}


def student_view(sid):
    s = q("SELECT * FROM students WHERE id=?", (sid,))[0]
    out = []
    for c in q("SELECT c.id, c.name FROM courses c JOIN enrollments e ON e.course_id=c.id WHERE e.student_id=?", (sid,)):
        a = att(sid, c["id"])
        marks = [dict(r) for r in q(
            "SELECT x.id aid, x.name, x.max_marks, m.score FROM assessments x "
            "JOIN marks m ON m.assessment_id=x.id AND m.student_id=? WHERE x.course_id=?", (sid, c["id"]))]
        out.append({"course": c["name"], "att": a, "risk": risk(s, c["id"], a), "marks": marks})
    return {"student": {"id": sid, "name": s["name"]}, "courses": out}


def notify(uid, text):
    w("INSERT INTO notifications(user_id,text) VALUES(?,?)", (uid, text))


# ---------------- voice parsing ----------------
ONES = {x: i for i, x in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                   "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {x: 10 * (i + 2) for i, x in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
SKIP = {"and", "marks", "mark", "got", "scored", "has", "is", "for", "out", "of", "gets"}


def regex_parse(text):
    toks = re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower())
    out, buf, i = [], [], 0
    while i < len(toks):
        t = toks[i]
        if t[0].isdigit():
            out.append((" ".join(buf), float(t))); buf = []
        elif t in TENS or t in ONES:
            v = TENS.get(t, ONES.get(t))
            if t in TENS and i + 1 < len(toks) and toks[i + 1] in ONES and ONES[toks[i + 1]] < 10:
                v += ONES[toks[i + 1]]; i += 1
            out.append((" ".join(buf), float(v))); buf = []
        elif t == "absent":
            buf = []
        elif t not in SKIP:
            buf.append(t)
        i += 1
    return [(n, s) for n, s in out if n]


def llm_parse(text):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import requests
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=15,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": "claude-sonnet-5-5", "max_tokens": 500, "messages": [{"role": "user", "content":
                                'Extract student names and marks from this dictated text. Reply ONLY with JSON like '
                                '[{"name":"..","mark":18}]. Skip absent students. Text: ' + text}]})
        arr = json.loads(re.search(r"\[.*\]", r.json()["content"][0]["text"], re.S).group())
        return [(a["name"], float(a["mark"])) for a in arr]
    except Exception:
        return None


# ---------------- API ----------------
class Entry(BaseModel):
    student_id: int
    score: float


class SaveReq(BaseModel):
    assessment_id: int
    entries: list[Entry]


class VoiceReq(BaseModel):
    assessment_id: int
    transcript: str


class ReportReq(BaseModel):
    student_id: int
    assessment_id: int


def get_assessment(aid):
    r = q("SELECT x.*, c.name cname FROM assessments x JOIN courses c ON c.id=x.course_id WHERE x.id=?", (aid,))
    if not r:
        raise HTTPException(404, "Assessment not found")
    return r[0]


@app.get("/api/users")
def users():
    return [dict(r) for r in q("SELECT * FROM users ORDER BY role='student', id")]


@app.get("/api/student/{sid}")
def student(sid: int):
    return student_view(sid)


@app.get("/api/notifications/{uid}")
def notes(uid: int):
    return [dict(r) for r in q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))]


@app.post("/api/notifications/{uid}/read")
def read(uid: int):
    w("UPDATE notifications SET read=1 WHERE user_id=?", (uid,))
    return {"ok": True}


@app.get("/api/prof")
def prof():
    return [dict(r) for r in q("SELECT x.id, x.name, x.max_marks, c.name course FROM assessments x "
                               "JOIN courses c ON c.id=x.course_id ORDER BY c.id, x.id")]


@app.post("/api/voice/parse")
def voice_parse(r: VoiceReq):
    ax = get_assessment(r.assessment_id)
    roster = [dict(s) for s in q("SELECT s.id, s.name FROM students s JOIN enrollments e ON e.student_id=s.id "
                                 "WHERE e.course_id=? ORDER BY s.name", (ax["course_id"],))]
    names = {s["id"]: s["name"] for s in roster}
    pairs = llm_parse(r.transcript) or regex_parse(r.transcript)
    rows = []
    for spoken, score in pairs:
        cand = process.extract(spoken, names, scorer=fuzz.WRatio, limit=3)  # (name, score, id)
        conf, amb = cand[0][1], False
        if len(cand) > 1 and cand[1][1] >= cand[0][1] - 5:
            conf, amb = min(conf, 55), True
        rows.append({"spoken": spoken, "score": score, "student_id": cand[0][2], "confidence": round(conf),
                     "ambiguous": amb or score > ax["max_marks"]})
    return {"rows": rows, "roster": roster, "max": ax["max_marks"]}


@app.post("/api/marks")
def save_marks(r: SaveReq):
    ax = get_assessment(r.assessment_id)
    for e in r.entries:
        if not 0 <= e.score <= ax["max_marks"]:
            raise HTTPException(400, f"Mark {e.score:g} is outside 0-{ax['max_marks']:g}")
    for e in r.entries:
        old = q("SELECT score FROM marks WHERE student_id=? AND assessment_id=?", (e.student_id, ax["id"]))
        if old:
            w("UPDATE marks SET score=? WHERE student_id=? AND assessment_id=?", (e.score, e.student_id, ax["id"]))
        else:
            w("INSERT INTO marks VALUES(?,?,?)", (e.student_id, ax["id"], e.score))
        o = old[0]["score"] if old else None
        w("INSERT INTO mark_history(student_id,assessment_id,old,new) VALUES(?,?,?,?)", (e.student_id, ax["id"], o, e.score))
        u = q("SELECT id FROM users WHERE student_id=?", (e.student_id,))
        if u and o != e.score:
            txt = f"{ax['cname']} {ax['name']}: {e.score:g}/{ax['max_marks']:g}" + (f" (updated from {o:g})" if o is not None else "")
            notify(u[0]["id"], txt)
    return {"saved": len(r.entries)}


@app.post("/api/report")
def report(r: ReportReq):
    ax = get_assessment(r.assessment_id)
    nm = q("SELECT name FROM students WHERE id=?", (r.student_id,))[0]["name"]
    for p in q("SELECT id FROM users WHERE role='prof'"):
        notify(p["id"], f"{nm} reports a possible error in {ax['cname']} {ax['name']}")
    return {"ok": True}


@app.get("/api/admin")
def admin():
    courses = [r["name"] for r in q("SELECT name FROM courses ORDER BY id")]
    rows = []
    for s in q("SELECT id, name FROM students ORDER BY id"):
        v = student_view(s["id"])
        rows.append({"student": s["name"], "cells": [{"score": c["risk"]["score"], "level": c["risk"]["level"],
                                                       "att": c["att"]["current"]} for c in v["courses"]]})
    return {"courses": courses, "rows": rows}


@app.post("/api/reset")
def reset():
    seed()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


if not os.path.exists(DB):
    seed()
