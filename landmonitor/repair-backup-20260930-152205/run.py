"""Запуск: python run.py  ->  http://localhost:8000  (на хостинге порт берётся из переменной PORT)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), timeout_graceful_shutdown=3)
