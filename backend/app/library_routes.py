"""Small batch metadata for the video library; never send full job reports."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/library/status")
def library_status():
    from backend.app import main as state

    with state.connect() as db:
        rows = db.execute("""
            SELECT json_extract(j.value, '$.game_id') AS game_id,
                   json_extract(j.value, '$.job_id') AS job_id,
                   json_extract(j.value, '$.status') AS status,
                   json_extract(j.value, '$.phase') AS phase
            FROM records j JOIN records g
              ON g.kind='game' AND g.id=json_extract(j.value, '$.game_id')
            WHERE j.kind='job'
              AND COALESCE(json_extract(g.value, '$.removed_from_library'), 0)=0
            ORDER BY j.rowid
        """).fetchall()
    latest = {}
    for row in rows:
        latest[row["game_id"]] = dict(row)
    return {"items": list(latest.values())}
