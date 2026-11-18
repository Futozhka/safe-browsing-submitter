import asyncio
import uuid
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import agent
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
RUNS_DIR = BASE_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Массовая отправка отчётов")
app.mount("/runs", StaticFiles(directory=str(RUNS_DIR)), name="runs")

JOBS: dict = {}
# Используем верхний регистр для сравнения
SUBMISSION_TYPES = {"DOMAIN", "IP", "URL"}


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/run")
async def run(
    submission_type: str = Form(...),
    targets: str = Form(...),
    threat_type: str = Form(...),
    reason: str = Form(...),
    file: Optional[UploadFile] = File(None),
):
    # Нормализуем к ЗАГЛАВНЫМ
    submission_type = submission_type.strip().upper()
    if submission_type not in SUBMISSION_TYPES:
        raise HTTPException(400, f"Submission Type должен быть Domain, IP или URL. Получено: {submission_type}")
    
    lines = [t.strip() for t in targets.splitlines() if t.strip()]
    if not lines:
        raise HTTPException(400, "Список целей пуст")

    logger.info(f"Новая заявка: Type={submission_type}, Count={len(lines)}")

    attachment = None
    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in config.ALLOWED_EXT:
            raise HTTPException(400, "Файл должен быть jpg, jpeg, png или pdf")
        content = await file.read()
        if len(content) > config.MAX_FILE_BYTES:
            raise HTTPException(400, "Файл больше 2 МБ")
        job_id = uuid.uuid4().hex[:12]
        att_dir = RUNS_DIR / job_id
        att_dir.mkdir(parents=True, exist_ok=True)
        attachment = att_dir / f"attachment{ext}"
        attachment.write_bytes(content)
        logger.info(f"Файл прикреплен: {attachment.name}")
    else:
        job_id = uuid.uuid4().hex[:12]
        (RUNS_DIR / job_id).mkdir(parents=True, exist_ok=True)

    job = {
        "id": job_id,
        "finished": False,
        "params": {
            "submission_type": submission_type,
            "threat_type": threat_type,
            "reason": reason,
            "attachment": str(attachment) if attachment else None,
        },
        "items": [
            {
                "index": i,
                "target": t,
                "status": "pending",
                "code": None,
                "error": None,
                "attempt": 1,
            }
            for i, t in enumerate(lines)
        ],
    }
    JOBS[job_id] = job
    asyncio.create_task(agent.run_job(job, RUNS_DIR))
    logger.info(f"Задание запущено с ID: {job_id}")
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Задание не найдено")
    return {"finished": job["finished"], "items": job["items"]}


@app.post("/api/retry")
async def retry(request: Request):
    data = await request.json()
    job = JOBS.get(data.get("job_id"))
    if not job:
        raise HTTPException(404, "Задание не найдено")
    if not job["finished"]:
        raise HTTPException(400, "Задание ещё выполняется")
    indexes = data.get("indexes") or [i["index"] for i in job["items"] if i["status"] == "error"]
    restarted = False
    for idx in indexes:
        item = job["items"][idx]
        if item["status"] == "error":
            item["status"] = "pending"
            item["code"] = None
            item["error"] = None
            item["attempt"] += 1
            restarted = True
    if not restarted:
        raise HTTPException(400, "Нет неудачных строк для повторной отправки")
    job["finished"] = False
    logger.info(f"Перезапуск строк: {indexes}")
    asyncio.create_task(agent.run_job(job, RUNS_DIR))
    return {"ok": True}
