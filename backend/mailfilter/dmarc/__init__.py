"""DMARC aggregate reports: read them from mail, store them, explain them.

parse.py   report mail / file -> ``Report`` objects (XML, .gz, .zip)
ingest.py  store reports; also the Postfix pipe command (mailfilter-dmarc-ingest)
senders.py who is this IP: Mailfilter itself, a known mail service, or unknown
advice.py  the next safe step towards p=reject
"""

RUA_LOCAL_PART = "dmarc-reports"


def rua_address(hostname: str) -> str:
    """The address on this server that receives the reports."""
    return f"{RUA_LOCAL_PART}@{hostname}"
