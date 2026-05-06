#!/usr/bin/env python3
"""
SCT Realtime Sync — index.html updater (v2)

Reads JSON payload from $GITHUB_EVENT_PATH (repository_dispatch event),
updates ESB or SID index.html in place.

v2 (2026-05-06): full reflection of Master Sheet → ESB hero, 3 Moves timeline,
Risks list, KPI strip with bar-width clamp. SID still updates timestamps only.

Idempotent: running twice with the same payload produces no diff.
"""

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict


# ---------- helpers ----------

def load_payload():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        print("ERROR: GITHUB_EVENT_PATH not set or missing", file=sys.stderr)
        sys.exit(1)
    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)
    return event.get("client_payload", {})


def fmt_money(v, suffix=" ลบ."):
    if v is None or v == "" or v == "–":
        return "–"
    try:
        n = float(v)
        if n.is_integer():
            return f"{int(n)}{suffix}"
        return f"{n:.1f}{suffix}".replace(".0", "")
    except (TypeError, ValueError):
        return str(v)


def clamp_pct(v, default=0):
    if v is None or v == "":
        return default
    try:
        if isinstance(v, str):
            v = v.rstrip("%").strip()
            if v == "":
                return default
        n = float(v)
        if 0 < n <= 1.0:
            n = n * 100
        return int(max(0, min(100, round(n))))
    except (TypeError, ValueError):
        return default


def status_class(status):
    s = (str(status or "")).strip().lower()
    if s in ("completed", "done", "เสร็จ", "เสร็จแล้ว"):
        return "completed"
    if s in ("in progress", "inprogress", "in-progress", "กำลังดำเนินการ", "ดำเนินการ"):
        return "inprogress"
    if s in ("blocked", "ติด", "ติดขัด"):
        return "blocked"
    if s in ("on hold", "onhold", "on-hold", "พัก"):
        return "onhold"
    return "notstarted"


STATUS_LABELS = {
    "completed": "Completed",
    "inprogress": "In Progress",
    "blocked": "Blocked",
    "onhold": "On Hold",
    "notstarted": "Not Started",
}


def parse_move_num(task):
    raw = task.get("move", "")
    if raw not in (None, "", 0):
        try:
            n = int(re.sub(r"[^\d]", "", str(raw)) or "0")
            if 1 <= n <= 9:
                return n
        except ValueError:
            pass
    tid = str(task.get("id", ""))
    m = re.match(r"[Mm](\d)", tid)
    if m:
        return int(m.group(1))
    return None


def aggregate_tasks(tasks):
    if not tasks:
        return None
    by_move = defaultdict(list)
    for t in tasks:
        mv = parse_move_num(t)
        if mv:
            by_move[mv].append(t)

    total = len(tasks)
    completed = sum(1 for t in tasks if status_class(t.get("status")) == "completed")
    active = sum(1 for t in tasks if status_class(t.get("status")) in ("inprogress", "blocked"))

    pct_sum = sum(clamp_pct(t.get("pctDone")) for t in tasks)
    overall_pct = int(round(pct_sum / max(1, total)))

    move_summaries = {}
    for mv, ts in by_move.items():
        if not ts:
            continue
        ps = sum(clamp_pct(t.get("pctDone")) for t in ts)
        avg = int(round(ps / len(ts)))
        statuses = [status_class(t.get("status")) for t in ts]
        if all(s == "completed" for s in statuses):
            mv_status = "completed"
        elif "blocked" in statuses:
            mv_status = "blocked"
        elif "inprogress" in statuses:
            mv_status = "inprogress"
        elif all(s == "notstarted" for s in statuses):
            mv_status = "notstarted"
        else:
            mv_status = "inprogress"
        move_summaries[mv] = {
            "pct": avg,
            "status_class": mv_status,
            "status_label": STATUS_LABELS[mv_status],
            "task_count": len(ts),
        }

    return {
        "overall_pct": overall_pct,
        "total_count": total,
        "completed_count": completed,
        "active_count": active,
        "by_move": move_summaries,
    }


def risk_emoji(score):
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "🟢"
    if s >= 9: return "🔴"
    if s >= 6: return "🟠"
    if s >= 3: return "🟡"
    return "🟢"


# ---------- SID ----------

def update_sid(html, payload):
    ts = payload.get("timestamp", "")
    if ts:
        html = re.sub(r"Last Sync: [^<]+<br>", f"Last Sync: {ts}<br>", html, count=1)
        date_only = ts.split(" ")[0:3]
        if len(date_only) == 3:
            html = re.sub(r"As of [^·]+ ·", f"As of {' '.join(date_only)} ·", html, count=1)
    return html


# ---------- ESB ----------

def update_esb(html, payload):
    ts = payload.get("timestamp", "")
    if ts:
        html = re.sub(
            r'(<span id="last-update">)[^<]+(</span>)',
            rf'\g<1>{ts}\g<2>', html, count=1)

    tasks = payload.get("tasks") or []
    risks = payload.get("risks") or []
    kpis = payload.get("kpis") or {}

    agg = aggregate_tasks(tasks)

    if agg:
        overall = clamp_pct(agg["overall_pct"])
        html = re.sub(
            r"const\s+overallPct\s*=\s*\d+\s*;",
            f"const overallPct = {overall};",
            html, count=1)
        html = re.sub(
            r'(<div class="hero-circle-pct" id="overall-pct">)[^<]*(</div>)',
            rf"\g<1>{overall}%\g<2>", html, count=1)
        html = re.sub(
            r'(<div class="hero-status">[^<·]+·\s*)\d+\s*tasks active',
            rf'\g<1>{agg["active_count"]} tasks active', html, count=1)
        html = _replace_kd_value(
            html, "Tasks Completed",
            f'{agg["completed_count"]} / {agg["total_count"]}')

    active_risks = sum(
        1 for r in risks if str(r.get("status", "")).strip().lower() == "active")
    if active_risks > 0:
        html = _replace_kd_value(html, "Active Risks", str(active_risks))

    cash = _kpi_lookup(kpis, "Cash Unlocked")
    if cash:
        html = _replace_kpi_card(
            html, "Cash Unlocked",
            target=fmt_money(cash.get("target")),
            actual=fmt_money(cash.get("actual")),
            pct=clamp_pct(cash.get("achieved")))

    lg = _kpi_lookup(kpis, "LG Capacity")
    if lg:
        html = _replace_kpi_card(
            html, "LG Capacity",
            target=fmt_money(lg.get("target")),
            actual=fmt_money(lg.get("actual")),
            pct=clamp_pct(lg.get("achieved")))

    bl = _kpi_lookup(kpis, "Backlog Capacity")
    if bl:
        html = _replace_kpi_card(
            html, "Backlog Capacity",
            target=fmt_money(bl.get("target")),
            actual=fmt_money(bl.get("actual")),
            pct=clamp_pct(bl.get("achieved")))

    if agg:
        for mv_num in (1, 2, 3):
            mv = agg["by_move"].get(mv_num)
            if mv:
                html = _replace_move_row(
                    html, mv_num, mv["pct"], mv["status_class"], mv["status_label"])

    if risks:
        active_sorted = sorted(
            (r for r in risks if str(r.get("status", "")).strip().lower() == "active"),
            key=lambda r: float(r.get("score") or 0),
            reverse=True)[:3]
        if active_sorted:
            html = _rebuild_risks_list(html, active_sorted, total_active=active_risks)

    return html


# ---------- ESB helpers ----------

def _kpi_lookup(kpis, needle):
    for k, v in kpis.items():
        if needle in str(k):
            return v
    return None


def _replace_kpi_card(html, kpi_title, target=None, actual=None, pct=None):
    title_pattern = re.escape(kpi_title)
    block_re = re.compile(
        r'(<div class="kpi-card-title">' + title_pattern + r'</div>.*?</div>\s*</div>)',
        re.DOTALL)
    m = block_re.search(html)
    if not m:
        return html
    block = m.group(1)
    if target is not None:
        block = re.sub(
            r'(<span class="kpi-target">Target: )[^<]+(</span>)',
            rf'\g<1>{target}\g<2>', block, count=1)
    if actual is not None:
        block = re.sub(
            r'(<span class="kpi-actual">)[^<]+(</span>)',
            rf'\g<1>{actual}\g<2>', block, count=1)
    if pct is not None:
        pct = clamp_pct(pct)
        block = re.sub(
            r'(class="kpi-bar-fill[^"]*" style="width:)-?\d+%(")',
            rf'\g<1>{pct}%\g<2>', block, count=1)
    return html.replace(m.group(1), block, 1)


def _replace_kd_value(html, label, new_value):
    pattern = (
        r'(<div class="hero-kd-label">' + re.escape(label) + r'</div>\s*'
        r'<div class="hero-kd-value">)[^<]*(</div>)')
    return re.sub(pattern, rf"\g<1>{new_value}\g<2>", html, count=1)


def _replace_move_row(html, mv_num, pct, status_class_, status_label_):
    full_row_re = re.compile(
        r'(<div class="move-row">\s*<div class="move-light[^"]*"></div>\s*'
        r'<div>\s*<div class="move-name">Move ' + str(mv_num) + r'\b.*?'
        r'<div class="move-status [^"]+">[^<]+</div>\s*</div>)',
        re.DOTALL)
    m = full_row_re.search(html)
    if not m:
        return html
    block = m.group(1)
    pct = clamp_pct(pct)
    block = re.sub(
        r'(<div class="progress-fill" style="width:)-?\d+%(")',
        rf'\g<1>{pct}%\g<2>', block, count=1)
    block = re.sub(
        r'(<div class="progress-pct">)\d+%(</div>)',
        rf"\g<1>{pct}%\g<2>", block, count=1)
    block = re.sub(
        r'<div class="move-status [^"]+">[^<]+</div>',
        f'<div class="move-status {status_class_}">{status_label_}</div>',
        block, count=1)
    return html.replace(m.group(1), block, 1)


def _rebuild_risks_list(html, top_risks, total_active):
    items = []
    for i, r in enumerate(top_risks, start=1):
        risk_text = str(r.get("risk") or "").strip()
        # Auto-bold the prefix before the first colon (e.g., "Concentration: ..." → "<strong>Concentration:</strong> ...")
        risk_text_html = re.sub(r"^([^:<\n]{1,40}):", r"<strong>\1:</strong>", risk_text)
        sev = str(r.get("severity") or "").strip()
        prob = str(r.get("probability") or "").strip()
        score = r.get("score") or ""
        meta = []
        if sev: meta.append(f"Severity: {sev}")
        if prob: meta.append(f"Probability: {prob}")
        if score: meta.append(f"Score: {score}")
        emoji = risk_emoji(score)
        items.append(
            f'    <div class="risk-item">\n'
            f'      <div class="risk-num">{i}</div>\n'
            f'      <div>\n'
            f'        <div class="item-text">{risk_text_html}</div>\n'
            f'        <div class="item-meta">{" · ".join(meta)}</div>\n'
            f'      </div>\n'
            f'      <div style="font-size:18px">{emoji}</div>\n'
            f'    </div>')
    new_inner = "\n".join(items)
    list_re = re.compile(
        r'(<div class="section-title">🚨 Top Active Risks</div>\s*)'
        r'(?:<div class="risk-item">.*?</div>\s*)+'
        r'(<div style="font-size:11px;color:#5a6c80;margin-top:10px;text-align:right">[^<]*</div>)',
        re.DOTALL)
    footer_text = (
        f'<div style="font-size:11px;color:#5a6c80;margin-top:10px;text-align:right">'
        f'📋 รายละเอียดทั้งหมด {total_active} ความเสี่ยง → ดูใน Action Plan Master · Sheet 06_Risk_Register'
        f'</div>')
    new_block = "\n".join(items)  # items already include leading "    "
    # Use a function-replacer to control whitespace deterministically
    def _replacer(m):
        return m.group(1).rstrip() + "\n" + new_block + "\n    " + footer_text
    return list_re.sub(_replacer, html, count=1)


def main():
    payload = load_payload()
    if not payload:
        print("Empty payload, nothing to sync")
        return
    print(f"Trigger reason: {payload.get('trigger_reason', 'unknown')}")
    print(f"Fired at: {payload.get('fired_at', '?')}")
    print(f"Timestamp: {payload.get('timestamp', '?')}")
    print(f"KPIs: {len(payload.get('kpis', {}))}")
    print(f"Tasks: {len(payload.get('tasks', []))}")
    print(f"Risks: {len(payload.get('risks', []))}")
    repo_name = os.environ.get("GITHUB_REPOSITORY", "")
    is_sid = "Strategic_Intersection_Dashboard" in repo_name
    is_esb = "Executive_Status_Board" in repo_name
    index_path = Path("index.html")
    if not index_path.exists():
        print("ERROR: index.html not found", file=sys.stderr)
        sys.exit(1)
    html = index_path.read_text(encoding="utf-8")
    original = html
    if is_sid:
        html = update_sid(html, payload)
    elif is_esb:
        html = update_esb(html, payload)
    else:
        print(f"WARN: Unknown repo {repo_name}, no rules applied")
    if html != original:
        index_path.write_text(html, encoding="utf-8")
        print(f"index.html updated ({len(html) - len(original):+d} bytes)")
    else:
        print("No changes detected — idempotent run")


if __name__ == "__main__":
    main()
