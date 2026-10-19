"""The delay relay of x7_proxy.py with explicit listen and target hosts (run_hardening.sh).

Every chunk is delayed by a fixed one-way delay in each direction, so a connection through the relay
sees a round trip of 2 * DELAY; the order of the bytes is kept and the bandwidth is not limited.
Listening on 127.0.0.2 while the server binds 127.0.0.1 lets a relay sit at the address that a
server advertises (the tablet host name of Vitess) without taking the server's own port.
Prints one line per accepted connection, so that a run can check which paths went through it.

usage: python -I relay.py DELAY_MS LHOST:LPORT:THOST:TPORT [...]
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


def serve(lh, lp, th, tp):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((lh, lp))
    ls.listen(1024)
    n = 0
    while True:
        a, peer = ls.accept()
        try:
            b = socket.create_connection((th, tp))
        except OSError:
            a.close()
            continue
        n += 1
        print(f'accepted {lh}:{lp} #{n} from {peer[0]}:{peer[1]} at {time.strftime("%H:%M:%S")}', flush=True)
        for s in (a, b):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        pipe(a, b)
        pipe(b, a)


if __name__ == '__main__':
    for spec in sys.argv[2:]:
        lh, lp, th, tp = spec.split(':')
        threading.Thread(target=serve, args=(lh, int(lp), th, int(tp)), daemon=True).start()
    print(f'relay up: delay {DELAY * 1000:.3f} ms, {" ".join(sys.argv[2:])}', flush=True)
    while True:
        time.sleep(3600)
