# gunicorn config — starts the 714 engine once the worker is up.
# Single worker is intentional: the trading engine must run exactly once
# (multiple workers would duplicate the live trading loop).

import threading

workers = 1
threads = 4
timeout = 120


def post_fork(server, worker):
    from app import start_engine
    start_engine()
