import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

celery_app = Celery(
    "codegrapher",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

celery_app.autodiscover_tasks(["codegrapher.api"])
