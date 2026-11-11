"""A resident's walk is a clock-based view of a persisted collection job.

Collection never waits for this view.  Existing jobs have no journey version,
so upgrading the application does not send historical material out on a trip.
"""
import datetime as dt


DURATIONS = dict(outbound_seconds=12, return_seconds=12,
                 forest_pause_seconds=2, unloading_seconds=2)
ACTIVE_PHASES = ("searching", "collecting", "storing")


def _timestamp(value, default=None):
    if not isinstance(value, str):
        return default
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return default


def journey_state(job, now, cargo_count=0):
    """Return stable route timing; cargo is checked from saved source files.

    The recorded successful count chooses the route once a job ends. Removing
    a file later changes its available cargo, never its already chosen route.
    """
    job = job or {}
    cargo_count = max(0, int(cargo_count))
    started = _timestamp(job.get("started_at"))
    phase = job.get("phase", "idle")
    outcome = ("empty" if phase == "completed" and not job.get("completed") else phase)
    result = dict(phase="home", job_id=job.get("id"), server_now=now.isoformat(),
                  started_at=job.get("started_at"), forest_arrival_at=None,
                  return_started_at=None, arrival_at=None, home_at=None,
                  return_from_progress=1.0, route_progress=0.0,
                  cargo_count=cargo_count, can_start=True, outcome=outcome,
                  message="星探在观察站等待出发。", durations=dict(DURATIONS))
    if not started or job.get("journey_version") != 1:
        return result

    outbound = DURATIONS["outbound_seconds"]
    forest_arrival = started + dt.timedelta(seconds=outbound)
    result["forest_arrival_at"] = forest_arrival.isoformat()
    elapsed = max(0.0, (now - started).total_seconds())
    finished = _timestamp(job.get("finished_at"))
    returning = arrival = home = None
    if finished and phase not in ACTIVE_PHASES:
        finished = max(started, finished)
        if job.get("completed", 0) > 0:
            returning = max(finished, forest_arrival + dt.timedelta(seconds=DURATIONS["forest_pause_seconds"]))
        else:
            returning = finished
            result["return_from_progress"] = min(1.0, max(0.0, (finished - started).total_seconds() / outbound))
        arrival = returning + dt.timedelta(seconds=DURATIONS["return_seconds"] * result["return_from_progress"])
        home = arrival + dt.timedelta(seconds=DURATIONS["unloading_seconds"] if job.get("completed", 0) > 0 else 0)
        result.update(return_started_at=returning.isoformat(), arrival_at=arrival.isoformat(), home_at=home.isoformat())

    if job.get("journey_delivered_at") or (home and now >= home):
        if cargo_count:
            result["message"] = "已将 %d 份真实材料送到材料架。" % cargo_count
            if phase in ("partial", "interrupted"):
                result["message"] += "本次仍有未完成部分，请查看任务记录。"
        else:
            result["message"] = "星探已空车返回。" + _empty_reason(job)
        return result

    result["can_start"] = False
    if arrival and now >= arrival:
        result.update(phase="unloading", route_progress=0.0,
                      message="正在将 %d 份材料卸到材料架。" % cargo_count if cargo_count else "已回到观察站，正在收拾空车。")
    elif returning and now >= returning:
        progress = result["return_from_progress"] - max(0.0, (now - returning).total_seconds()) / DURATIONS["return_seconds"]
        result.update(phase="returning", route_progress=max(0.0, progress),
                      message="带着 %d 份真实材料返回观察站。" % cargo_count if cargo_count else "空车返回观察站。" + _empty_reason(job))
        if cargo_count and phase in ("partial", "interrupted"):
            result["message"] += "部分任务未完成，已保存的材料仍会送达。"
    elif elapsed < outbound:
        result.update(phase="outbound", route_progress=min(1.0, elapsed / outbound),
                      message="沿道路前往北部森林，采集工作正在同步进行。" if phase in ACTIVE_PHASES else "已取得材料，前往森林采集点装车。")
    else:
        result.update(phase="working", route_progress=1.0,
                      message="正在森林采集点寻找和整理项目资料。" if phase in ACTIVE_PHASES else "正在森林采集点装车，准备返回观察站。")
    return result


def _empty_reason(job):
    if job.get("completed", 0) > 0:
        return "本次保存的材料文件目前不可用，请查看材料详情。"
    return _reason(job)


def _reason(job):
    errors = job.get("errors", [])
    for error in errors if isinstance(errors, list) else []:
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
            return error["message"][:300]
    return str(job.get("message", "本次没有取得新材料。"))[:300]
