"""TCP relay that delays every chunk by a fixed one-way delay in each direction.

It emulates a network round trip of 2 * DELAY between nodes that otherwise run on one server.
Every connection gets two pipes; each pipe has a reader thread that stamps the chunks it receives
and a writer thread that forwards each chunk once its stamp plus DELAY has passed, so the order of
the bytes is kept and the bandwidth is not limited.  time.sleep resolves to tens of microseconds on
Linux, which asyncio (millisecond epoll timeouts) would not.

usage: python -I x7_proxy.py DELAY_MS LISTEN:TARGET [LISTEN:TARGET ...]
"""
import collections, socket, sys, threading, time

DELAY = float(sys.argv[1]) / 1000.0


def pipe(src, dst):
    q = collections.deque()
    cv = threading.Condition()

    def reader():
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                data = b''
            with cv:
                q.append((time.perf_counter() + DELAY, data))
                cv.notify()
            if not data:
                return

    def writer():
        while True:
            with cv:
                while not q:
                    cv.wait()
                due, data = q.popleft()
            dt = due - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            if not data:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            try:
                dst.sendall(data)
            except OSError:
                return

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=writer, daemon=True).start()


def serve(listen, target):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(('127.0.0.1', listen))
    ls.listen(1024)
    while True:
        a, _ = ls.accept()
        try:
            b = socket.create_connection(('127.0.0.1', target))
        except OSError:
            a.close()
            continue
        for s in (a, b):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        pipe(a, b)
        pipe(b, a)


if __name__ == '__main__':
    for spec in sys.argv[2:]:
        lp, tp = map(int, spec.split(':'))
        threading.Thread(target=serve, args=(lp, tp), daemon=True).start()
    print(f'proxy up: delay {DELAY * 1000:.3f} ms, {" ".join(sys.argv[2:])}', flush=True)
    while True:
        time.sleep(3600)
