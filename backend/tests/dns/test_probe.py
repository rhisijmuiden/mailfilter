import shutil
import socket
import ssl
import subprocess
import threading

import pytest

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


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
def test_smtp_starttls_with_ip(tmp_path):
    """Probing by IP must still send the host name as SNI (an empty one made starttls() raise ValueError)."""
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                    "-days", "1", "-subj", "/CN=mx.test", "-addext", "subjectAltName=DNS:mx.test"],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    seen = {}
    ctx.sni_callback = lambda sock, name, c: seen.update(sni=name)
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.sendall(b"220 mx.test ESMTP\r\n")
        f = conn.makefile("rb")
        f.readline(); conn.sendall(b"250-mx.test\r\n250 STARTTLS\r\n")
        f.readline(); conn.sendall(b"220 go ahead\r\n")
        tls = ctx.wrap_socket(conn, server_side=True)
        tf = tls.makefile("rb")
        while line := tf.readline():
            cmd = line.strip().upper()
            tls.sendall(b"221 bye\r\n" if cmd == b"QUIT" else b"250-mx.test\r\n250 SIZE 1\r\n")
            if cmd == b"QUIT":
                break
        tls.close(); srv.close()

    threading.Thread(target=run, daemon=True).start()
    r = smtp("mx.test", "127.0.0.1", srv.getsockname()[1], timeout=5)
    assert r.error == "" and r.starttls and r.tls_ok, r.error
    assert seen["sni"] == "mx.test"
