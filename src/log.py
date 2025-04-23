# import logging
# from logging.handlers import QueueListener
# import sys, atexit, asyncio
# import multiprocessing
# from datetime import datetime
# from typing import Optional, List, Union
# from pathlib import Path
# LOGGING_FILE_PREFIX = datetime.now().strftime('%Y-%m-%d_%H-%M') #_%H-%M-%S _%H_%M
# GLOBAL_LOGGER_HANDLERS: List[logging.Handler] = []

# '''
#     Use QueueListener to implement async log modules 
# '''

# def create_folder_if_not_exists(fp):
#     if not Path(fp).exists():
#         Path(fp).mkdir(parents= True, exist_ok= True)

# class AsyncQueueListener(QueueListener):
#     loop: Optional[asyncio.AbstractEventLoop] = None
#     def __init__(self, queue, *handlers: logging.Handler, respect_handler_level: bool = False) -> None:
#         super().__init__(queue, *handlers, respect_handler_level=respect_handler_level)

#     @classmethod
#     def _start(cls):
#         if cls.loop is None:            
#             cls.loop = asyncio.new_event_loop()
#             asyncio.set_event_loop(cls.loop)
#     def _monitor(self) -> None:
#         super()._monitor()

#     def stop(self) -> None:
#         if self.loop is not None:
#             self.loop.stop()
#             self.loop.close()
#         self.enqueue_sentinel()
#         if self._thread:
#             self._thread.join(1)
#             self._thread = None

# class AppLogger(logging.Logger):
    
#     def __init__(self, name: str, level: Union[int,str] = 0) -> None:
#         logging.Logger.__init__(self, name, level)
#         self.propagate = False
#         if name.startswith('src'):
#             if not self.handlers:
#                 ## stdout handler
#                 self.setLevel(20)
                
#                 handler = logging.StreamHandler(stream= sys.stdout)
#                 formatter = logging.Formatter('%(asctime)s %(name)s %(threadName)s %(filename)s:%(lineno)d %(levelname)-8s %(message)s', datefmt= '%Y-%m-%d %H:%M:%S')
#                 handler.setFormatter(formatter)
#                 self.addHandler(handler)
#                 ## stderr handler
#                 err_handler = logging.StreamHandler(stream= sys.stderr)
#                 err_handler.setLevel(logging.ERROR)
#                 err_handler.setFormatter(formatter)
#                 self.addHandler(err_handler)
#                 ## file handler
#                 create_folder_if_not_exists('logs')
#                 file_handler = logging.FileHandler(filename = 'logs/log.log', mode = 'w')
#                 file_handler.setFormatter(formatter)
#                 self.addHandler(file_handler)
#         elif name.startswith('metrics'):
#             self.setup_metrics_logger(name, level)
    
#     def setup_metrics_logger(self, name: str, level: Union[int,str] = 0):
#         name = name[8:]
#         if not self.handlers:
#             target_folder = f'results/{LOGGING_FILE_PREFIX}'
#             create_folder_if_not_exists(target_folder)
#             file_handler = logging.FileHandler(filename = f'{target_folder}/{name}.log', mode = 'w')
#             formatter = logging.Formatter('%(message)s')
#             file_handler.setFormatter(formatter)
#             self.propagate = False
#             queue = multiprocessing.Queue()
#             listener = AsyncQueueListener(queue, file_handler)
#             self.addHandler(logging.handlers.QueueHandler(queue))
#             GLOBAL_LOGGER_HANDLERS.append((listener, self))
#             listener.start()
#             atexit.unregister(_close_loggers)
#             atexit.register(_close_loggers)

# logging.setLoggerClass(AppLogger)

# def _close_loggers():
#     while GLOBAL_LOGGER_HANDLERS:
#         listener, logger = GLOBAL_LOGGER_HANDLERS.pop()
#         logger.handlers = listener.handlers
#         listener.stop()
