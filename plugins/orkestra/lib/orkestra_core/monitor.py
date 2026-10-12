"""Read-only live view of every orkestra run in the workspace: `orkestra watch` (terminal) and
`orkestra dashboard` (local web page on 127.0.0.1).

Sources (all files, nothing is executed in the projects):
  .orkestra/runs/<run>/state.json      phase, status, tasks, evidence      (CLI-written)
  .orkestra/runs/<run>/activity.jsonl  tool calls / subagents / questions   (hook-written)
  .orkestra/runs/<run>/gates-*.json    gate results
  .orkestra/features/<run>/ledger.md   human-readable event log
  ~/.orkestra/active-runs.json, ~/.orkestra/sessions/*.json
  ~/.claude/projects/*/<session>.jsonl  token usage of the bound sessions (read-only, numbers only — usage.py)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import activity as A
from . import paths
from . import tempo as T
from . import engines as EN
from . import usage as U
from .state import OPEN_STATUSES, PHASES, active_runs
from .util import read_json, read_text

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "target", "Pods", "__pycache__"}
PHASE_TR = {"intake": "giriş", "discover": "keşif", "clarify": "netleştirme", "plan": "plan", "approve": "onay",
            "implement": "uygulama", "verify": "doğrulama", "review": "inceleme", "deliver": "teslim", "done": "bitti"}
STATUS_TR = {"active": "aktif", "awaiting_user": "seni bekliyor", "blocked": "bloke", "paused": "duraklatıldı",
             "done": "bitti", "aborted": "iptal"}


# ---------------------------------------------------------------- discovery

def _project_dirs(workspace: Path) -> Iterable[Path]:
    seen = set()
    for key in active_runs():
        p = Path(key)
        if p.is_dir() and p not in seen:
            seen.add(p)
            yield p
    if not workspace.is_dir():
        return
    for child in sorted(workspace.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        if (child / ".orkestra" / "runs").is_dir():
            if child not in seen:
                seen.add(child)
                yield child
            continue
        try:                                    # one level deeper (e.g. ~/Projects/clients/acme)
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and not sub.name.startswith(".") and (sub / ".orkestra" / "runs").is_dir() \
                        and sub not in seen:
                    seen.add(sub)
                    yield sub
        except OSError:
            continue


def discover(project: Optional[Path] = None, include_all: bool = False, recent_hours: float = 12.0,
             workspace: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Open runs (+ runs finished in the last `recent_hours`, or every run with include_all)."""
    projects = [project] if project else list(_project_dirs(workspace or paths.workspace_root()))
    now = time.time()
    found = []
    for proj in projects:
        rdir = paths.runs_dir(proj)
        if not rdir.is_dir():
            continue
        for d in rdir.iterdir():
            sp = d / "state.json"
            st = read_json(sp)
            if not isinstance(st, dict) or "run_id" not in st:
                continue
            try:
                mtime = max(sp.stat().st_mtime, (d / "activity.jsonl").stat().st_mtime
                            if (d / "activity.jsonl").exists() else 0)
            except OSError:
                mtime = 0
            if st.get("status") in OPEN_STATUSES or include_all or (now - mtime) < recent_hours * 3600:
                found.append((proj, st, mtime))
    # stable order (open runs first, then project name): cards/blocks must not jump around as activity arrives
    found.sort(key=lambda x: (x[1].get("status") not in OPEN_STATUSES, x[0].name.lower(), str(x[1].get("run_id"))))
    return [collect(proj, st, mtime) for proj, st, mtime in found]


def _sessions_for(project: Path, run_id: str) -> List[Dict[str, Any]]:
    out = []
    d = paths.sessions_dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        data = read_json(f)
        if isinstance(data, dict) and data.get("run_id") == run_id and Path(data.get("project", "")) == Path(project):
            out.append({"id": f.stem, "session": EN.short_sid(f.stem), "bound_at": data.get("bound_at", "")})
    return out


def _gate_summary(rdir: Path) -> Dict[str, str]:
    out = {}
    for label in ("baseline", "verify"):
        data = read_json(rdir / f"gates-{label}.json")
        if not isinstance(data, dict):
            out[label] = "—"
            continue
        res = data.get("results") or []
        ok = sum(1 for r in res if r.get("ok"))
        txt = f"{ok}/{len(res)} geçti" if res else "kapı yok"
        cmp_ = data.get("comparison") or {}
        if label == "verify" and cmp_:
            txt += " ✓" if cmp_.get("ok") else f" ✗ gerileme: {', '.join(cmp_.get('regressions') or []) or '?'}"
        out[label] = txt
    return out


def _parse_iso(s: str) -> Optional[float]:
    try:
        return _dt.datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def _activity_feed(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    entries = []
    latest_denial = {}
    for row in A.visible(rows, len(rows)):
        ts = float(row.get("ts") or 0)
        agent = str(row.get("agent") or "main")
        item = {"time": time.strftime("%H:%M:%S", time.localtime(ts)),
                "agent": A.short_agent(agent), "kind": row.get("kind"), "tool": row.get("tool") or "",
                "what": row.get("what") or "", "_sort_ts": ts}
        if item["kind"] == "deny" and item["tool"] == "Bash":
            key = (agent, item["what"])
            previous = latest_denial.get(key)
            if previous and 0 <= ts - previous["_last_ts"] <= 600:
                previous["repeat_count"] += 1
                previous["time"] = item["time"]
                previous["_last_ts"] = ts
                previous["_sort_ts"] = ts
                continue
            item["repeat_count"] = 1
            item["_last_ts"] = ts
            latest_denial[key] = item
        entries.append(item)
    entries.sort(key=lambda item: item["_sort_ts"])
    feed = entries[-limit:]
    for item in feed:
        item.pop("_last_ts", None)
        item.pop("_sort_ts", None)
    return feed


def collect(project: Path, st: Dict[str, Any], mtime: float = 0.0, n_activity: int = 14) -> Dict[str, Any]:
    rid = st["run_id"]
    rdir = paths.run_dir(project, rid)
    rows = A.read_rows(project, rid)
    tasks = st.get("tasks") or {}
    tlist = []
    for tid in sorted(tasks):
        t = tasks[tid]
        tlist.append({"id": tid, "status": t.get("status"), "title": t.get("title") or "", "tdd": t.get("tdd"),
                      "agent": t.get("agent") or "",
                      "evidence": ("R" if (t.get("red") or {}).get("ok") else "-")
                                  + ("G" if (t.get("green") or {}).get("ok") else "-")})
    done = sum(1 for t in tlist if t["status"] in ("done", "deferred"))
    hist = st.get("history") or []
    phase_since = _parse_iso(hist[-1].get("at", "")) if hist else None
    ledger = [ln[2:] for ln in read_text(paths.feature_dir(project, rid) / "ledger.md").splitlines()
              if ln.startswith("- ")][-4:]
    feed = _activity_feed(rows, n_activity)
    phase = st.get("phase", "intake")
    state_updated = _parse_iso(st.get("updated")) or mtime
    sessions = _sessions_for(project, rid)
    created = _parse_iso(st.get("created", ""))
    usage = U.for_sessions([s["id"] for s in sessions], since=created - 120 if created else None)
    if not usage and isinstance(st.get("usage"), dict):
        usage = dict(st["usage"], final=True)       # closed run: the numbers saved when it finished
    wd = T.wd_cfg()
    pending = A.agents_pending(project, rid, max_age=float(wd["agent_max_minutes"]) * 60) \
        if st.get("status") in OPEN_STATUSES else []
    stall = T.classify(st, rows, pending, wd=wd, state_mtime=state_updated)
    tm = T.timings(st)
    try:
        flags = T.flags(project, st, rows, wd) if st.get("status") in OPEN_STATUSES else []
    except Exception:
        flags = []
    return {
        "project": str(project), "project_name": project.name, "run_id": rid, "title": st.get("title", ""),
        "size": st.get("size", ""), "mode": st.get("mode", ""), "engine": st.get("engine") or "claude", "phase": phase,
        "phase_tr": PHASE_TR.get(phase, phase), "phase_index": PHASES.index(phase) if phase in PHASES else 0,
        "phase_since": phase_since, "status": st.get("status"), "status_tr": STATUS_TR.get(st.get("status"), ""),
        "approved": bool(st.get("approved")), "live": A.liveness(st, rows, state_mtime=state_updated, pending=pending),
        "agents": [A.short_agent(str(a.get("type") or "")) for a in pending] or
                  [A.short_agent(a) for a in A.running_agents(rows)],
        "stall": stall, "flags": flags,
        "timing": {"run": int(tm["run"]), "phases": {k: int(v) for k, v in tm["phases"].items()},
                   "task_avg": int(tm["task_avg"]) if tm.get("task_avg") else None, "reopens": tm["reopens"],
                   "tasks_reopened": tm["tasks_reopened"]},
        "tasks": tlist, "tasks_done": done, "tasks_total": len(tlist),
        "current_tasks": [t for t in tlist if t["status"] == "in_progress"],
        "gates": _gate_summary(rdir), "activity": feed, "ledger": ledger,
        "sessions": [{"session": s["session"], "bound_at": s["bound_at"]} for s in sessions], "usage": usage,
        "updated": st.get("updated", ""),
        "artifacts": str(paths.feature_dir(project, rid)),
    }


# ---------------------------------------------------------------- terminal

class _C:
    def __init__(self, on: bool):
        self.on = on

    def __call__(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text


LEVEL_COLOR = {"run": "32", "wait": "1;33", "quiet": "36", "stale": "31", "blocked": "1;31", "paused": "35",
               "done": "2"}
LEVEL_DOT = {"run": "●", "wait": "◆", "quiet": "○", "stale": "✗", "blocked": "■", "paused": "Ⅱ", "done": "✓"}
KIND_MARK = {"tool": " ", "deny": "!", "result": "←", "agent_done": "✓", "answer": "✎", "user": "»", "idle": "⏸",
             "nudge": "↻", "bind": "⚓", "handback": "⇠", "wait_agents": "⏳"}
STALL_MARK = {2: "ÖLÜ BEKLEME", 3: "ASILI KOMUT", 4: "SESSİZ OTURUM"}


def _dur(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    s = int(max(0, sec))
    return f"{s // 60} dk" if s < 3600 else f"{s // 3600} sa {s % 3600 // 60} dk"


def _tok(n: Any) -> str:
    n = int(n or 0)
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}K" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def render_text(views: List[Dict[str, Any]], color: bool = False, width: Optional[int] = None) -> str:
    c = _C(color)
    width = width or shutil.get_terminal_size((110, 40)).columns
    width = max(60, min(width, 160))

    def cut(s: str, n: int) -> str:
        return s if len(s) <= n else s[: max(0, n - 1)] + "…"

    if not views:
        return ("Açık orkestra çalışması yok. (Son 12 saatte bitenleri de görmek için `orkestra watch --all`.)\n"
                f"Çalışma alanı: {paths.workspace_root()}")
    out: List[str] = []
    now = time.time()
    for v in views:
        head = f"━━ {v['project_name']} · {v['run_id']} · \"{v['title']}\" ({v['size']}{', headless' if v['mode'] == 'headless' else ''}{', cursor' if v.get('engine') == 'cursor' else ''}) "
        out.append(c("1", cut(head, width) + "━" * max(0, width - len(head))))
        bar = []
        for i, ph in enumerate(PHASES):
            if i < v["phase_index"]:
                bar.append(c("2", ph))
            elif i == v["phase_index"]:
                bar.append(c("1;7", f" {ph} "))
            else:
                bar.append(ph)
        since = f" · bu fazda {_dur(now - v['phase_since'])}" if v.get("phase_since") else ""
        out.append(f" faz    {' › '.join(bar)}" + c("2", since))
        lv = v["live"]
        out.append(f" durum  " + c(LEVEL_COLOR.get(lv["level"], "0"), f"{LEVEL_DOT.get(lv['level'], '•')} {cut(lv['text'], width - 10)}"))
        stl = v.get("stall") or {}
        if stl.get("cls") in STALL_MARK:
            out.append(c("1;31", cut(f" DİKKAT {STALL_MARK[stl['cls']]} — {stl.get('text', '')}", width)))
        for fl in (stl.get("loops") or [])[:2]:
            out.append(c("31", cut(f" döngü  {fl}", width)))
        tmg = v.get("timing") or {}
        if tmg.get("run"):
            bits = [f"toplam {_dur(tmg['run'])}"]
            if tmg.get("task_avg"):
                bits.append(f"görev başına {_dur(tmg['task_avg'])}")
            if tmg.get("reopens"):
                bits.append(f"inceleme reddi {tmg['reopens']} ({tmg['tasks_reopened']} görev)")
            out.append(c("2", cut(" tempo  " + " · ".join(bits), width)))
        if v["tasks_total"]:
            cur = v["current_tasks"]
            cur_txt = " · şu an: " + "; ".join(f"{t['id']} {t['evidence']} {t['title']}" for t in cur) if cur else ""
            out.append(cut(f" görev  {v['tasks_done']}/{v['tasks_total']} bitti{cur_txt}", width))
        g = v["gates"]
        if g.get("baseline") != "—" or g.get("verify") != "—":
            out.append(f" kapı   başlangıç {g['baseline']} · doğrulama {g['verify']}")
        sess = v["sessions"]
        out.append(c("2", f" oturum {len(sess)} bağlı" + (f" ({', '.join(s['session'] for s in sess)})" if sess else
                                                         " — bu çalışmayı süren açık Claude oturumu yok")))
        u = v.get("usage")
        if u:
            out.append(cut(f" token  girdi {_tok(u['input'])} · çıktı {_tok(u['output'])} · cache yazma "
                           f"{_tok(u['cache_write'])} · cache okuma {_tok(u['cache_read'])}"
                           + (f" · bağlam {_tok(u['context'])}" if u.get("context") else "")
                           + (" (kapanış değeri)" if u.get("final") else ""), width))
        if v["activity"]:
            out.append(" son hareketler:")
            for r in v["activity"]:
                mark = KIND_MARK.get(r["kind"], " ")
                tool, what = r["tool"], r["what"]
                if r["kind"] == "deny":
                    count = int(r.get("repeat_count") or 1)
                    category = str(what).partition("]")[0].lstrip("[") if str(what).startswith("[") else "genel"
                    tool = f"Koruma ×{count}" if count > 1 else "Koruma"
                    what = f"{category} koruması"
                line = f"   {r['time']} {cut(r['agent'], 11):<11} {mark} {cut(tool, 15):<15} {what}"
                colored = cut(line, width)
                if r["kind"] == "deny":
                    colored = c("33", colored)
                elif r["kind"] in ("idle", "user", "answer"):
                    colored = c("33", colored)
                elif r["kind"] in ("result", "agent_done", "handback", "wait_agents"):
                    colored = c("36", colored)
                out.append(colored)
        else:
            out.append(c("2", "   (aktivite kaydı yok — kayıt, eklentinin bu sürümü yüklendikten sonraki araç çağrılarıyla başlar)"))
        if v["ledger"]:
            out.append(c("2", cut(f" günlük {v['ledger'][-1]}", width)))
        out.append("")
    return "\n".join(out)


def watch(project: Optional[Path], interval: float, once: bool, include_all: bool, as_json: bool) -> int:
    if as_json:
        sys.stdout.write(json.dumps(discover(project, include_all), ensure_ascii=False, indent=2) + "\n")
        return 0
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if once or not sys.stdout.isatty():          # agents / pipes get one snapshot, never an endless loop
        sys.stdout.write(render_text(discover(project, include_all), color=color) + "\n")
        return 0
    try:
        while True:
            body = render_text(discover(project, include_all), color=color)
            stamp = time.strftime("%H:%M:%S")
            sys.stdout.write("\033[H\033[2J" + _C(color)("2", f"orkestra watch · {stamp} · her {interval:g} sn · "
                                                            f"çıkış: Ctrl+C") + "\n\n" + body + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


# ---------------------------------------------------------------- web dashboard (read-only, 127.0.0.1)

PAGE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orkestra — canlı</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mut:#6b7280;--line:#e3e6eb;--run:#16a34a;--wait:#d97706;
--quiet:#0891b2;--stale:#dc2626;--blocked:#b91c1c;--paused:#7c3aed;--done:#9ca3af;--deny:#dc2626;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--fg:#e6e9ef;--mut:#9aa3b2;--line:#262c37;
--chip:#222834}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;gap:12px;align-items:baseline;padding:16px 20px 4px}h1{font-size:18px;margin:0}
#meta{color:var(--mut);font-size:12px}main{padding:8px 20px 40px;display:grid;gap:16px;
grid-template-columns:repeat(auto-fill,minmax(520px,1fr))}@media(max-width:600px){main{grid-template-columns:1fr;padding:8px 12px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}.proj{font-weight:700;font-size:16px}
.rid{color:var(--mut);font-size:12px;overflow-wrap:anywhere}.title{margin:2px 0 10px}
.live{border-radius:8px;padding:8px 10px;font-weight:600;margin:8px 0;border-left:4px solid}
.lv-run{border-color:var(--run)}.lv-wait{border-color:var(--wait);background:color-mix(in srgb,var(--wait) 12%,transparent)}
.lv-quiet{border-color:var(--quiet)}.lv-stale{border-color:var(--stale)}.lv-blocked{border-color:var(--blocked);
background:color-mix(in srgb,var(--blocked) 12%,transparent)}.lv-paused{border-color:var(--paused)}.lv-done{border-color:var(--done)}
.phases{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}.ph{font-size:11px;padding:2px 7px;border-radius:999px;
background:var(--chip);color:var(--mut)}.ph.past{opacity:.55}.ph.cur{background:var(--fg);color:var(--bg);font-weight:700}
.row{color:var(--mut);font-size:12px;margin:3px 0}.row b{color:var(--fg);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;table-layout:fixed}td{padding:3px 4px;
border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}td.t{width:62px;color:var(--mut);
font-variant-numeric:tabular-nums}td.a{width:86px}td.k{width:110px;color:var(--mut)}
tr.deny td{color:var(--wait)}tr.idle td,tr.user td,tr.answer td{color:var(--wait)}tr.result td,tr.agent_done td,tr.handback td,tr.wait_agents td{color:var(--quiet)}
.tasks{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}.task{font-size:11px;padding:2px 6px;border-radius:6px;
background:var(--chip)}.task.done{opacity:.55}.task.in_progress{outline:2px solid var(--run)}.task.blocked{outline:2px solid var(--blocked)}
.empty{color:var(--mut);padding:40px 20px}label{font-size:12px;color:var(--mut)}
</style></head><body>
<header><h1>Orkestra — canlı</h1><span id="meta">yükleniyor…</span>
<label><input type="checkbox" id="all"> bitenleri de göster</label></header>
<main id="runs"></main>
<script>
const PH=["intake","discover","clarify","plan","approve","implement","verify","review","deliver","done"];
function el(t,cls,txt){const e=document.createElement(t);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e;}
function tk(n){n=n||0;return n<1000?String(n):n<1e6?(n/1e3).toFixed(1)+"K":(n/1e6).toFixed(2)+"M";}
function dur(s){s=Math.max(0,s|0);return s<3600?Math.floor(s/60)+" dk":Math.floor(s/3600)+" sa "+Math.floor(s%3600/60)+" dk";}
const STALL={2:"ÖLÜ BEKLEME",3:"ASILI KOMUT",4:"SESSİZ OTURUM"};
function card(v){
 const c=el("div","card");const top=el("div","top");top.append(el("span","proj",v.project_name),
 el("span","rid",v.run_id+" · "+v.size+(v.mode==="headless"?" · headless":"")+(v.engine==="cursor"?" · cursor":"")));c.append(top,el("div","title",v.title));
 const phs=el("div","phases");PH.forEach((p,i)=>{phs.append(el("span","ph"+(i<v.phase_index?" past":i===v.phase_index?" cur":""),p));});
 c.append(phs);c.append(el("div","live lv-"+v.live.level,v.live.text));
 if(v.stall&&STALL[v.stall.cls]){c.append(el("div","live lv-stale",STALL[v.stall.cls]+" — "+v.stall.text));}
 ((v.stall&&v.stall.loops)||[]).concat(v.flags||[]).slice(0,3).forEach(f=>c.append(el("div","row","⚑ "+f)));
 if(v.timing&&v.timing.run){c.append(el("div","row","tempo: toplam "+dur(v.timing.run)+(v.timing.task_avg?" · görev başına "+dur(v.timing.task_avg):"")
  +(v.timing.reopens?" · inceleme reddi "+v.timing.reopens+" ("+v.timing.tasks_reopened+" görev)":"")));}
 if(v.tasks_total){const r=el("div","row");r.append("görevler: ",el("b","",v.tasks_done+"/"+v.tasks_total+" bitti"));c.append(r);
  const ts=el("div","tasks");v.tasks.forEach(t=>{const x=el("span","task "+t.status,t.id+" "+t.evidence);x.title=t.title+" ["+t.status+"]";ts.append(x);});c.append(ts);}
 const g=el("div","row");g.textContent="kapılar: başlangıç "+v.gates.baseline+" · doğrulama "+v.gates.verify;c.append(g);
 const s=el("div","row");s.textContent=v.sessions.length?("bağlı oturum: "+v.sessions.map(x=>x.session).join(", ")):"bağlı Claude oturumu yok";c.append(s);
 const u=el("div","row");if(v.usage){u.append("token: girdi ",el("b","",tk(v.usage.input))," · çıktı ",el("b","",tk(v.usage.output)),
  " · cache yazma ",el("b","",tk(v.usage.cache_write))," · cache okuma ",el("b","",tk(v.usage.cache_read)));
  if(v.usage.context){u.append(" · bağlam ",el("b","",tk(v.usage.context)));}
  if(v.usage.final){u.append(" (kapanış değeri)");}
  u.title="Çalışmanın başından beri: bağlı oturum(lar) + alt-ajanlar · "+v.usage.messages+" model yanıtı · bağlam = ana oturumun son isteği";}
 else{u.textContent="token: — (bağlı oturumun kaydı bulunamadı)";}c.append(u);
 if(v.ledger.length){c.append(el("div","row","günlük: "+v.ledger[v.ledger.length-1]));}
 const tb=el("table");v.activity.slice().reverse().forEach(r=>{const tr=el("tr",r.kind);const isGuard=r.kind==="deny";
  const guardCount=Number(r.repeat_count||1);const guardLabel=guardCount>1?"Koruma ×"+guardCount:"Koruma";
    const guardText=String(r.what||"");const guardKind=guardText.startsWith("[")?guardText.split("]",1)[0].slice(1):"genel";
  const detail=el("td","",isGuard?(guardKind+" koruması"+(guardCount>1?" · "+guardCount+" tekrar":"")):r.what);
  if(isGuard)detail.title=r.what;
  tr.append(el("td","t",r.time),el("td","a",r.agent),el("td","k",isGuard?guardLabel:(r.kind==="tool"?"":r.kind+" ")+r.tool),detail);tb.append(tr);});
 if(!v.activity.length){const tr=el("tr");const td=el("td","","aktivite kaydı yok (yeni araç çağrılarıyla başlar)");td.colSpan=4;tr.append(td);tb.append(tr);}
 c.append(tb);const p=el("div","row");p.textContent=v.artifacts;c.append(p);return c;}
async function tick(){
 try{const all=document.getElementById("all").checked;const r=await fetch("/api/runs"+(all?"?all=1":""),{cache:"no-store"});
  const data=await r.json();const m=document.getElementById("runs");m.replaceChildren();
  if(!data.length){m.append(el("div","empty","Açık orkestra çalışması yok."));}
  data.forEach(v=>m.append(card(v)));
  document.getElementById("meta").textContent="güncellendi "+new Date().toLocaleTimeString("tr-TR")+" · her 2 sn";
 }catch(e){document.getElementById("meta").textContent="bağlantı yok — `orkestra dashboard` çalışıyor mu?";}}
tick();setInterval(tick,2000);document.getElementById("all").addEventListener("change",tick);
</script></body></html>
"""


def make_handler(project: Optional[Path], port: int):
    from http.server import BaseHTTPRequestHandler

    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        server_version = "orkestra-dashboard"

        def log_message(self, *args: Any) -> None:     # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Host", "") not in allowed_hosts:    # DNS-rebinding protection
                self._send(403, b"forbidden", "text/plain")
                return
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/runs":
                data = discover(project, include_all="all=1" in query)
                self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

    return H


def dashboard(project: Optional[Path], port: int, open_browser: bool) -> int:
    from http.server import ThreadingHTTPServer

    if os.environ.get("CLAUDECODE") and not sys.stdout.isatty():
        sys.stderr.write("orkestra: dashboard is a long-running server — the user starts it in their own terminal "
                         "(`orkestra dashboard --open`). Use `orkestra watch --once` here.\n")
        return 2

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(project, port))
    except OSError as exc:
        sys.stderr.write(f"orkestra: 127.0.0.1:{port} açılamadı ({exc}). Zaten açık olabilir: http://127.0.0.1:{port}/ "
                         f"— ya da başka port: `orkestra dashboard --port <n>`\n")
        return 2
    url = f"http://127.0.0.1:{port}/"
    sys.stdout.write(f"orkestra dashboard → {url}  (yalnızca bu bilgisayar, salt-okunur · kapatmak: Ctrl+C)\n")
    sys.stdout.flush()
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
