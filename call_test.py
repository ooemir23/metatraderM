import threading
def call_with_timeout(func, timeout, *args, **kwargs):
    res = []
    err = []
    def wrapper():
        try:
            res.append(func(*args, **kwargs))
        except Exception as e:
            err.append(e)
    t = threading.Thread(target=wrapper)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Call timed out after {timeout}s")
    if err:
        raise err[0]
    if res:
        return res[0]
    return None

import time
def slow_func():
    time.sleep(2)
    return "done"

print(call_with_timeout(slow_func, 1.0))
