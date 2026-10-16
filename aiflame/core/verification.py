"""Evidence from actual source events; demos and adapter tests never qualify."""

import json

from .events import EventError


def begin_verification(engine, source):
    if (
        not isinstance(source, str)
        or source not in engine.sources
        or source in ("manual", "demo")
    ):
        raise EventError("请选择真实应用或本地 API 来源")
    if not engine.settings["source_enabled"].get(source):
        raise EventError("请先在应用接入中开启该来源", 409)
    engine.db.execute(
        "INSERT OR REPLACE INTO settings VALUES (?,?)",
        ("verification:" + source, json.dumps(engine.wall())),
    )
    engine.db.commit()


def verification_evidence(engine, source):
    """Return stored lifecycle metadata, including explicit simulation markers."""
    if not isinstance(source, str) or source not in engine.sources or source in ("manual", "demo"):
        raise EventError("请选择真实应用或本地 API 来源", error_code="source_invalid")
    summary = verification_summary(engine)[source]
    limit = 1000
    total = engine.db.execute("SELECT COUNT(*) FROM events WHERE source=? AND created>=?", (source, summary["since"] or 0)).fetchone()[0]
    rows = engine.db.execute(
        "SELECT id AS event_id, task AS task_id, seq, state, reason, created, status, "
        "EXISTS(SELECT 1 FROM self_tests WHERE event=events.id) AS simulated "
        "FROM events WHERE source=? AND created>=? ORDER BY created DESC, rowid DESC LIMIT ?",
        (source, summary["since"] or 0, limit),
    )
    events = [{**dict(row), "simulated": bool(row["simulated"])} for row in rows]
    events.reverse()
    return {"source": source, "since": summary["since"], "summary": summary, "events": events,
            "total": total, "limit": limit, "truncated": total > limit,
            "retention": {"days": 7, "max_events": 10000}}


def verification_summary(engine):
    windows = {
        row["key"].removeprefix("verification:"): json.loads(row["value"])
        for row in engine.db.execute(
            "SELECT * FROM settings WHERE key LIKE 'verification:%'"
        )
    }
    summaries = {
        source: {
            "source": source,
            "since": windows.get(source),
            "status": "waiting",
            "started_at": None,
            "finished_at": None,
            "last_seen": None,
            "last_state": None,
            "delivery": None,
            "complete_tasks": 0,
        }
        for source in engine.sources
        if source not in ("manual", "demo")
    }
    tasks = {}
    task_states = {
        (row["source"], row["task"]): row["state"]
        for row in engine.db.execute("SELECT source,task,state FROM tasks")
    }
    for event in engine.db.execute(
        "SELECT events.* FROM events WHERE source NOT IN ('manual','demo') "
        "AND NOT EXISTS(SELECT 1 FROM self_tests WHERE event=events.id) "
        "ORDER BY created, events.rowid"
    ):
        summary = summaries.get(event["source"])
        if summary is None or event["created"] < (summary["since"] or 0):
            continue
        key = (event["source"], event["task"])
        task = tasks.setdefault(key, {"start": None, "last": None})
        if event["state"] == "working" and event["seq"] == 1:
            task["start"] = dict(event)
        task["last"] = dict(event)
        summary["last_seen"] = event["created"]
        summary["latest_task"] = key
    for (source, task_id), task in tasks.items():
        summary = summaries[source]
        start, last = task["start"], task["last"]
        complete = bool(
            start
            and start["status"] != "unmatched"
            and last["seq"] > start["seq"]
            and last["state"] == "done"
            and last["status"] != "unmatched"
        )
        summary["complete_tasks"] += int(complete)
        if summary.get("latest_task") != (source, task_id):
            continue
        summary.update(
            started_at=start["created"] if start else None,
            finished_at=last["created"] if complete else None,
            last_state="cancelled" if last["reason"] == "cancelled" else last["state"],
            delivery=last["status"],
            status=(
                "disabled"
                if last["status"] == "unmatched"
                else "verified"
                if complete
                else "cancelled"
                if last["reason"] == "cancelled" or last["state"] == "cancelled"
                else "needs_input"
                if last["state"] == "needs_input"
                else "timeout"
                if last["reason"] == "timeout" or last["state"] == "timeout"
                else "unknown"
                if last["state"] == "unknown"
                else "timeout"
                if last["state"] == "working"
                and (
                    last["status"] in ("expired", "lease_expired")
                    or task_states.get((source, task_id)) in ("unknown", "cancelled")
                )
                else "working"
                if start and last["state"] == "working"
                else "incomplete"
            ),
        )
    for source, summary in summaries.items():
        summary.pop("latest_task", None)
        if not engine.settings["source_enabled"].get(source):
            summary["status"] = "disabled"
    return summaries
