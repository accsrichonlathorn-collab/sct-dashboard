#!/usr/bin/env python3
"""
SCT Realtime Sync — index.html updater
Reads JSON payload from $GITHUB_EVENT_PATH (repository_dispatch event),
updates specific fields in index.html in place.

Idempotent: running twice with same payload produces no diff.
"""

import json
import os
import re
import sys
from pathlib import Path


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


def update_sid(html: str, payload: dict) -> str:
    """Strategic Intersection Dashboard — refresh footer Last Sync."""
    ts = payload.get("timestamp", "")
    if ts:
        # Update footer "Last Sync: ..."
        html = re.sub(
            r"Last Sync: [^<]+<br>",
            f"Last Sync: {ts}<br>",
            html,
            count=1,
        )
        # Update "As of ..." subtitle (keep date only, drop time)
        date_only = ts.split(" ")[0:3]
        if len(date_only) == 3:
            date_str = " ".join(date_only)
            html = re.sub(
                r"As of [^·]+ ·",
                f"As of {date_str} ·",
                html,
                count=1,
            )
    return html


def update_esb(html: str, payload: dict) -> str:
    """Executive Status Board — refresh header timestamp + KPI strip."""
    ts = payload.get("timestamp", "")
    if ts:
        # Header "อัปเดต: 5 พ.ค. 2569 21:01"
        html = re.sub(
            r'(<span id="last-update">)[^<]+(</span>)',
            rf'\g<1>{ts}\g<2>',
            html,
            count=1,
        )

    kpis = payload.get("kpis", {})

    # KPI: Cash Unlocked
    cash = next((v for k, v in kpis.items() if "Cash Unlocked" in k), None)
    if cash:
        target = fmt_money(cash.get("target"))
        actual = fmt_money(cash.get("actual"))
        gap = fmt_money(cash.get("gap"), suffix="")
        pct = cash.get("achieved", "0%")
        if isinstance(pct, float):
            pct = f"{int(pct * 100)}%" if pct <= 1 else f"{int(pct)}%"
        html = _replace_kpi_card(
            html, "Cash Unlocked",
            target=target, actual=actual, gap=gap, pct=pct
        )

    # KPI: LG Capacity
    lg = next((v for k, v in kpis.items() if "LG Capacity" in k), None)
    if lg:
        target = fmt_money(lg.get("target"))
        actual = fmt_money(lg.get("actual"))
        pct = lg.get("achieved", "0%")
        if isinstance(pct, float):
            pct = f"{int(pct * 100)}%" if pct <= 1 else f"{int(pct)}%"
        html = _replace_kpi_card(
            html, "LG Capacity",
            target=target, actual=actual, pct=pct
        )

    # KPI: Backlog Capacity
    bl = next((v for k, v in kpis.items() if "Backlog Capacity" in k), None)
    if bl:
        target = fmt_money(bl.get("target"))
        actual = fmt_money(bl.get("actual"))
        pct = bl.get("achieved", "0%")
        if isinstance(pct, float):
            pct = f"{int(pct * 100)}%" if pct <= 1 else f"{int(pct)}%"
        html = _replace_kpi_card(
            html, "Backlog Capacity",
            target=target, actual=actual, pct=pct
        )

    # Active risks count
    risks = payload.get("risks", [])
    active_count = sum(1 for r in risks if str(r.get("status", "")).strip().lower() == "active")
    if active_count > 0:
        html = re.sub(
            r'(<div class="hero-kd-label">Active Risks</div>\s*<div class="hero-kd-value">)[^<]+(</div>)',
            rf'\g<1>{active_count}\g<2>',
            html,
            count=1,
        )

    return html


def _replace_kpi_card(html: str, kpi_title: str, target=None, actual=None, gap=None, pct=None):
    """Replace one KPI card's target/actual/pct based on title."""
    # Find the card block for this KPI title
    title_pattern = re.escape(kpi_title)
    block_re = re.compile(
        r'(<div class="kpi-card-title">' + title_pattern + r'</div>.*?</div>\s*</div>)',
        re.DOTALL,
    )
    m = block_re.search(html)
    if not m:
        return html
    block = m.group(1)

    if target is not None:
        block = re.sub(
            r'(<span class="kpi-target">Target: )[^<]+(</span>)',
            rf'\g<1>{target}\g<2>',
            block,
            count=1,
        )
    if actual is not None:
        block = re.sub(
            r'(<span class="kpi-actual">)[^<]+(</span>)',
            rf'\g<1>{actual}\g<2>',
            block,
            count=1,
        )
    # Update bar width if pct given
    if pct is not None:
        try:
            pct_num = int(str(pct).rstrip("%"))
            block = re.sub(
                r'(class="kpi-bar-fill[^"]*" style="width:)\d+%(")',
                rf'\g<1>{pct_num}%\g<2>',
                block,
                count=1,
            )
        except ValueError:
            pass

    return html.replace(m.group(1), block, 1)


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
