import tempfile
import shutil
from contextlib import contextmanager

@contextmanager
def job_workspace(job_id:str):
    path = tempfile.mkdtemp(prefix=f"job-{job_id}-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)