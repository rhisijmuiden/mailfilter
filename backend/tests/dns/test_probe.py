import socket
import threading

from mailfilter_dns.probe import smtp


def _server(lines_after_ehlo: list[bytes]):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        f.write(b"220 mx.test ESMTP\r\n"); f.flush()
        while True:
            line = f.readline()
            if not line:
                break
            cmd = line.strip().upper()
            if cmd.startswith(b"EHLO"):
                f.write(b"".join(lines_after_ehlo)); f.flush()
            elif cmd == b"QUIT":
                f.write(b"221 bye\r\n"); f.flush()
                break
            else:
                f.write(b"502 no\r\n"); f.flush()
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_smtp_without_starttls():
    port = _server([b"250-mx.test\r\n", b"250 SIZE 1000\r\n"])
    r = smtp("mx.test", "127.0.0.1", port, timeout=5)
    assert r.reachable and r.banner.startswith("mx.test") and not r.starttls and r.error == ""


def test_smtp_refused():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    r = smtp("mx.test", "127.0.0.1", port, timeout=2)
    assert not r.reachable and r.error
