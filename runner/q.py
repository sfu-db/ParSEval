from queue import Queue
import threading, atexit
from typing import List, Dict, Set, NewType

UniqueId = NewType("UniqueId", str)

from concurrent.futures import Future, ThreadPoolExecutor


from threading import Lock, Thread

class SingletonMeta(type):
    """
    A thread-safe implementation of Singleton.
    """
    _instances = {}
    _lock: Lock = Lock()
    """
    We now have a lock object that will be used to synchronize threads during
    first access to the Singleton.
    """
    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]

class ThreadWorker(metaclass = SingletonMeta):
    pool = ThreadPoolExecutor(max_workers = 32)
    def __call__(cls, *args, **kwargs):
        return cls.pool.submit(*args, **kwargs)


def close_workers():
    tw = ThreadWorker()
    tw.pool.shutdown(wait= False)

def submit_work(*args, **kwargs):
    return ThreadWorker()(*args, **kwargs)

class SharedQueue(object):
    def __init__(self, maxsize = 0) -> None:
        self.q = Queue()
        self.inner: Queue = Queue(maxsize= maxsize)
        
        self.queued: Set[UniqueId] = set() # all
        self.in_progress: Set[UniqueId] = set() ## in progress
        self.completed: Set[UniqueId] = set() # completed

        self.lock = threading.Lock()
    def get(self, block = True, timeoout = None):
        job = self.inner.get(block=block, timeout= timeoout)
        with self.lock:
            self._mark_in_progress(job.unique_id)   
        return job

    def put(self, item, block = True, timeout = None):
        self.inner.put(item = item, block = block, timeout = timeout)
        with self.lock:
            self.queued.add(item.unique_id)

    def _mark_in_progress(self, unique_id):
        self.queued.remove(unique_id)
        self.in_progress.add(unique_id)
    
    def mark_done(self, node_id):
        with self.lock:
            
            self.in_progress.remove(node_id)
            print(f"inprogress: {self.in_progress}")
            self.completed.add(node_id)
            self.inner.task_done()
    def join(self) -> None:
        self.inner.join()

    def empty(self):
        return len(self.queued) == 0
