"""Spam check report: turn Rspamd's rule hits into a readable checklist.

Rspamd logs every rule that fired for a message, with its score and what it
found (options). This module sorts those rules into the checks people
recognise (is the sender real, blocklists, links, attachments, content, format,
your own lists and rules), explains each rule in English and Dutch, and says
per check whether it passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GROUPS = ("auth", "reputation", "links", "attachments", "content", "format", "policy")

# Default thresholds; the live values are read from Rspamd's actions.conf.
DEFAULT_THRESHOLDS = {"greylist": 4.0, "add_header": 5.0, "quarantine": 8.0, "reject": 15.0}
ACTIONS_CONF = Path("/etc/rspamd/local.d/actions.conf")

# Rules that decide on their own, whatever the total score is.
DECISIVE = re.compile(r"^(CLAM_VIRUS|MF_BLOCK_.*|MF_LIST_BLOCK_.*|MF_ATTACHMENT_(BLOCKED|MACRO|ENCRYPTED)|MF_BAD_ATTACHMENT"
                      r"|MF_ALLOW_.*|MF_LIST_ALLOW_.*|GTUBE)$")

# symbol: (group, English, Dutch)
CATALOG: dict[str, tuple[str, str, str]] = {
    # Sender authenticity
    "R_SPF_ALLOW": ("auth", "SPF: the sending server may send for this domain", "SPF: de verzendende server mag mailen voor dit domein"),
    "R_SPF_FAIL": ("auth", "SPF: the sending server may NOT send for this domain", "SPF: de verzendende server mag NIET mailen voor dit domein"),
    "R_SPF_SOFTFAIL": ("auth", "SPF: the sending server is probably not allowed", "SPF: de verzendende server mag waarschijnlijk niet mailen"),
    "R_SPF_NEUTRAL": ("auth", "SPF: the domain makes no statement about this server", "SPF: het domein zegt niets over deze server"),
    "R_SPF_NA": ("auth", "SPF: the domain has no SPF record", "SPF: het domein heeft geen SPF-record"),
    "R_SPF_DNSFAIL": ("auth", "SPF: the record could not be looked up", "SPF: het record kon niet worden opgezocht"),
    "R_SPF_PERMFAIL": ("auth", "SPF: the domain's SPF record is broken", "SPF: het SPF-record van het domein is kapot"),
    "R_DKIM_ALLOW": ("auth", "DKIM signature is valid", "DKIM-handtekening klopt"),
    "R_DKIM_REJECT": ("auth", "DKIM signature is invalid", "DKIM-handtekening klopt niet"),
    "R_DKIM_TEMPFAIL": ("auth", "DKIM signature could not be checked right now", "DKIM-handtekening kon nu niet worden gecontroleerd"),
    "R_DKIM_PERMFAIL": ("auth", "DKIM signature could not be checked", "DKIM-handtekening kon niet worden gecontroleerd"),
    "R_DKIM_NA": ("auth", "No DKIM signature", "Geen DKIM-handtekening"),
    "DMARC_POLICY_ALLOW": ("auth", "DMARC: passes the domain's policy", "DMARC: voldoet aan het beleid van het domein"),
    "DMARC_POLICY_ALLOW_WITH_FAILURES": ("auth", "DMARC: passes, with some failures", "DMARC: gehaald, met enkele fouten"),
    "DMARC_POLICY_REJECT": ("auth", "DMARC: fails, the domain says to refuse it", "DMARC: gezakt, het domein zegt weigeren"),
    "DMARC_POLICY_QUARANTINE": ("auth", "DMARC: fails, the domain says to quarantine it", "DMARC: gezakt, het domein zegt quarantaine"),
    "DMARC_POLICY_SOFTFAIL": ("auth", "DMARC: fails, the domain only monitors", "DMARC: gezakt, het domein monitort alleen"),
    "DMARC_NA": ("auth", "DMARC: the domain has no DMARC policy", "DMARC: het domein heeft geen DMARC-beleid"),
    "DMARC_BAD_POLICY": ("auth", "DMARC: the domain's policy is broken", "DMARC: het beleid van het domein is kapot"),
    "ARC_ALLOW": ("auth", "Valid ARC seal (forwarded mail)", "Geldig ARC-zegel (doorgestuurde mail)"),
    "ARC_ALLOW_TRUSTED": ("auth", "Forwarded by a trusted provider (ARC)", "Doorgestuurd door een vertrouwde provider (ARC)"),
    "ARC_REJECT": ("auth", "Invalid ARC seal", "Ongeldig ARC-zegel"),
    "ARC_INVALID": ("auth", "Invalid ARC seal", "Ongeldig ARC-zegel"),
    "MF_LOOKALIKE_DOMAIN": ("auth", "Sender domain looks like one of yours", "Afzenderdomein lijkt op een van jouw domeinen"),
    "MF_DISPLAYNAME_SPOOF": ("auth", "Sender name pretends to be someone else", "Afzendernaam doet zich voor als iemand anders"),
    "FORGED_SENDER": ("auth", "Envelope sender and From address differ", "Envelop-afzender en Van-adres verschillen"),
    "FORGED_RECIPIENTS": ("auth", "Recipients in the message differ from the real ones", "Ontvangers in het bericht wijken af van de echte"),
    "SPOOF_DISPLAY_NAME": ("auth", "Sender name contains a different address", "Afzendernaam bevat een ander adres"),
    "FREEMAIL_FROM": ("auth", "Sent from a free mail service", "Verstuurd vanaf een gratis maildienst"),
    "FREEMAIL_REPLYTO": ("auth", "Replies go to a free mail service", "Antwoorden gaan naar een gratis maildienst"),
    "FREEMAIL_REPLYTO_NEQ_FROM_DOM": ("auth", "Replies go to a different free mail address", "Antwoorden gaan naar een ander gratis mailadres"),
    "REPLYTO_DOM_NEQ_FROM_DOM": ("auth", "Replies go to a different domain than the sender's", "Antwoorden gaan naar een ander domein dan de afzender"),
    # Reputation and blocklists
    "RBL_SPAMHAUS": ("reputation", "Sending server on the Spamhaus blocklist", "Verzendende server op de Spamhaus-blocklist"),
    "RBL_SPAMHAUS_ZEN": ("reputation", "Sending server on the Spamhaus blocklist", "Verzendende server op de Spamhaus-blocklist"),
    "RBL_SPAMHAUS_SBL": ("reputation", "Sending server on the Spamhaus SBL", "Verzendende server op de Spamhaus SBL"),
    "RBL_SPAMHAUS_XBL": ("reputation", "Sending server is a known infected machine (Spamhaus XBL)", "Verzendende server is een bekende besmette machine (Spamhaus XBL)"),
    "RBL_SPAMHAUS_PBL": ("reputation", "Sent from a home or dynamic IP address (Spamhaus PBL)", "Verstuurd vanaf een thuis- of dynamisch IP-adres (Spamhaus PBL)"),
    "RECEIVED_SPAMHAUS_PBL": ("reputation", "Passed through a home or dynamic IP address", "Via een thuis- of dynamisch IP-adres gegaan"),
    "RBL_MAILSPIKE_BAD": ("reputation", "Sending server has a bad reputation (Mailspike)", "Verzendende server heeft een slechte reputatie (Mailspike)"),
    "RBL_MAILSPIKE_VERYBAD": ("reputation", "Sending server has a very bad reputation (Mailspike)", "Verzendende server heeft een zeer slechte reputatie (Mailspike)"),
    "RBL_MAILSPIKE_WORST": ("reputation", "Sending server has the worst reputation (Mailspike)", "Verzendende server heeft de slechtste reputatie (Mailspike)"),
    "RWL_MAILSPIKE_GOOD": ("reputation", "Sending server has a good reputation (Mailspike)", "Verzendende server heeft een goede reputatie (Mailspike)"),
    "RWL_MAILSPIKE_VERYGOOD": ("reputation", "Sending server has a very good reputation (Mailspike)", "Verzendende server heeft een zeer goede reputatie (Mailspike)"),
    "RWL_MAILSPIKE_EXCELLENT": ("reputation", "Sending server has an excellent reputation (Mailspike)", "Verzendende server heeft een uitstekende reputatie (Mailspike)"),
    "RWL_MAILSPIKE_NEUTRAL": ("reputation", "Sending server has a neutral reputation (Mailspike)", "Verzendende server heeft een neutrale reputatie (Mailspike)"),
    "RBL_BARRACUDA": ("reputation", "Sending server on the Barracuda blocklist", "Verzendende server op de Barracuda-blocklist"),
    "RBL_SPAMCOP": ("reputation", "Sending server on the SpamCop blocklist", "Verzendende server op de SpamCop-blocklist"),
    "RBL_SEM": ("reputation", "Sending server on the Spameatingmonkey blocklist", "Verzendende server op de Spameatingmonkey-blocklist"),
    "RBL_SENDERSCORE": ("reputation", "Sending server has a low SenderScore", "Verzendende server heeft een lage SenderScore"),
    "RCVD_IN_DNSWL_HI": ("reputation", "Sending server on a trusted list (DNSWL, high)", "Verzendende server op een vertrouwde lijst (DNSWL, hoog)"),
    "RCVD_IN_DNSWL_MED": ("reputation", "Sending server on a trusted list (DNSWL, medium)", "Verzendende server op een vertrouwde lijst (DNSWL, middel)"),
    "RCVD_IN_DNSWL_LOW": ("reputation", "Sending server on a trusted list (DNSWL, low)", "Verzendende server op een vertrouwde lijst (DNSWL, laag)"),
    "RCVD_IN_DNSWL_NONE": ("reputation", "Sending server known to DNSWL, no trust given", "Verzendende server bekend bij DNSWL, geen vertrouwen"),
    "DWL_DNSWL_HI": ("reputation", "Sender domain on a trusted list (DNSWL)", "Afzenderdomein op een vertrouwde lijst (DNSWL)"),
    "DWL_DNSWL_MED": ("reputation", "Sender domain on a trusted list (DNSWL)", "Afzenderdomein op een vertrouwde lijst (DNSWL)"),
    "DWL_DNSWL_LOW": ("reputation", "Sender domain on a trusted list (DNSWL)", "Afzenderdomein op een vertrouwde lijst (DNSWL)"),
    "RDNS_NONE": ("reputation", "Sending server has no reverse DNS name", "Verzendende server heeft geen reverse-DNS-naam"),
    "RDNS_DNSFAIL": ("reputation", "Reverse DNS of the sending server failed", "Reverse-DNS van de verzendende server mislukte"),
    "HFILTER_HOSTNAME_UNKNOWN": ("reputation", "Sending server has no name", "Verzendende server heeft geen naam"),
    "HFILTER_HELO_BAREIP": ("reputation", "Server introduced itself with a bare IP address", "Server stelde zich voor met een kaal IP-adres"),
    "HFILTER_HELO_NOT_FQDN": ("reputation", "Server introduced itself with an incomplete name", "Server stelde zich voor met een onvolledige naam"),
    "MX_INVALID": ("reputation", "Sender domain cannot receive mail (no valid MX)", "Afzenderdomein kan geen mail ontvangen (geen geldige MX)"),
    "MX_MISSING": ("reputation", "Sender domain has no MX record", "Afzenderdomein heeft geen MX-record"),
    "MX_GOOD": ("reputation", "Sender domain can receive mail (valid MX)", "Afzenderdomein kan mail ontvangen (geldige MX)"),
    # Links
    "URIBL_BLACK": ("links", "Link to a blocked website (URIBL)", "Link naar een geblokkeerde website (URIBL)"),
    "URIBL_GREY": ("links", "Link to a doubtful website (URIBL)", "Link naar een twijfelachtige website (URIBL)"),
    "URIBL_RED": ("links", "Link to a suspicious website (URIBL)", "Link naar een verdachte website (URIBL)"),
    "SURBL_MULTI": ("links", "Link to a blocked website (SURBL)", "Link naar een geblokkeerde website (SURBL)"),
    "ABUSE_SURBL": ("links", "Link to an abuse website (SURBL)", "Link naar een misbruikwebsite (SURBL)"),
    "PH_SURBL_MULTI": ("links", "Link to a phishing website (SURBL)", "Link naar een phishingwebsite (SURBL)"),
    "MW_SURBL_MULTI": ("links", "Link to a malware website (SURBL)", "Link naar een malwarewebsite (SURBL)"),
    "DBL_SPAM": ("links", "Link to a known spam domain (Spamhaus DBL)", "Link naar een bekend spamdomein (Spamhaus DBL)"),
    "DBL_PHISH": ("links", "Link to a known phishing domain (Spamhaus DBL)", "Link naar een bekend phishingdomein (Spamhaus DBL)"),
    "DBL_MALWARE": ("links", "Link to a known malware domain (Spamhaus DBL)", "Link naar een bekend malwaredomein (Spamhaus DBL)"),
    "DBL_BOTNET": ("links", "Link to a botnet domain (Spamhaus DBL)", "Link naar een botnetdomein (Spamhaus DBL)"),
    "DBL_ABUSE": ("links", "Link to an abused domain (Spamhaus DBL)", "Link naar een misbruikt domein (Spamhaus DBL)"),
    "SEM_URIBL": ("links", "Link to a blocked website (SEM)", "Link naar een geblokkeerde website (SEM)"),
    "PHISHING": ("links", "Link text and real destination differ (phishing)", "Linktekst en echte bestemming verschillen (phishing)"),
    "PHISHED_OPENPHISH": ("links", "Link to a known phishing page (OpenPhish)", "Link naar een bekende phishingpagina (OpenPhish)"),
    "PHISHED_PHISHTANK": ("links", "Link to a known phishing page (PhishTank)", "Link naar een bekende phishingpagina (PhishTank)"),
    "MF_SUSPICIOUS_LINK": ("links", "Link that hides where it goes", "Link die verbergt waar hij heen gaat"),
    "R_SUSPICIOUS_URL": ("links", "Link with a suspicious address", "Link met een verdacht adres"),
    "HAS_IPFS_GATEWAY_URL": ("links", "Link to a file on IPFS (often used for phishing)", "Link naar een bestand op IPFS (vaak gebruikt voor phishing)"),
    "REDIRECTOR_URL": ("links", "Link through a redirect service", "Link via een doorstuurdienst"),
    "OMOGRAPH_URL": ("links", "Link with look-alike characters", "Link met lookalike-tekens"),
    "HAS_ONION_URI": ("links", "Link to a Tor (.onion) website", "Link naar een Tor-website (.onion)"),
    "R_URI_UNREADABLE": ("links", "Link that cannot be read", "Link die niet te lezen is"),
    # Attachments and viruses
    "CLAM_VIRUS": ("attachments", "Virus found (ClamAV)", "Virus gevonden (ClamAV)"),
    "CLAM_VIRUS_FAIL": ("attachments", "Virus scanner could not check the message", "Virusscanner kon het bericht niet controleren"),
    "MF_BAD_ATTACHMENT": ("attachments", "Risky attachment", "Riskante bijlage"),
    "MF_ATTACHMENT_BLOCKED": ("attachments", "Dangerous attachment type", "Gevaarlijk bijlagetype"),
    "MF_ATTACHMENT_MACRO": ("attachments", "Office file with macros", "Office-bestand met macro's"),
    "MF_ATTACHMENT_ENCRYPTED": ("attachments", "Password-protected archive", "Archief met wachtwoord"),
    "MIME_BAD_ATTACHMENT": ("attachments", "Attachment's type does not match its name", "Type van de bijlage past niet bij de naam"),
    "MIME_BAD_EXTENSION": ("attachments", "Attachment with a dangerous file extension", "Bijlage met een gevaarlijke extensie"),
    "MIME_DOUBLE_BAD_EXTENSION": ("attachments", "Attachment hides its real type (double extension)", "Bijlage verbergt het echte type (dubbele extensie)"),
    "MIME_ARCHIVE_IN_ARCHIVE": ("attachments", "Archive inside an archive", "Archief in een archief"),
    "MIME_EXE_IN_GEN_SPLIT_RAR": ("attachments", "Program hidden in a split archive", "Programma verstopt in een gesplitst archief"),
    "MIME_OBFUSCATED_ARCHIVE": ("attachments", "Disguised archive", "Vermomd archief"),
    "MIME_ENCRYPTED_ARCHIVE": ("attachments", "Password-protected archive", "Archief met wachtwoord"),
    "MIME_BAD_UNICODE": ("attachments", "Attachment name with misleading characters", "Bijlagenaam met misleidende tekens"),
    # Content and images
    "BAYES_SPAM": ("content", "Looks like mail that was learned as spam (Bayes)", "Lijkt op mail die als spam is geleerd (Bayes)"),
    "BAYES_HAM": ("content", "Looks like mail that was learned as good (Bayes)", "Lijkt op mail die als goed is geleerd (Bayes)"),
    "FUZZY_DENIED": ("content", "Matches known spam (fuzzy hash)", "Komt overeen met bekende spam (fuzzy hash)"),
    "FUZZY_PROB": ("content", "Probably matches known spam (fuzzy hash)", "Komt waarschijnlijk overeen met bekende spam (fuzzy hash)"),
    "FUZZY_WHITE": ("content", "Matches known good mail (fuzzy hash)", "Komt overeen met bekende goede mail (fuzzy hash)"),
    "NEURAL_SPAM": ("content", "Neural network says spam", "Neuraal netwerk zegt spam"),
    "NEURAL_HAM": ("content", "Neural network says good mail", "Neuraal netwerk zegt goede mail"),
    "GTUBE": ("content", "Spam test message (GTUBE)", "Spamtestbericht (GTUBE)"),
    "R_SUSPICIOUS_IMAGES": ("content", "Suspicious images with little text", "Verdachte plaatjes met weinig tekst"),
    "R_EMPTY_IMAGE": ("content", "Only an image, no text", "Alleen een plaatje, geen tekst"),
    "HTML_SHORT_LINK_IMG_1": ("content", "Short text with a linked image", "Korte tekst met een plaatje als link"),
    "HTML_SHORT_LINK_IMG_2": ("content", "Short text with a linked image", "Korte tekst met een plaatje als link"),
    "HTML_SHORT_LINK_IMG_3": ("content", "Short text with a linked image", "Korte tekst met een plaatje als link"),
    "ZERO_FONT": ("content", "Hidden text (font size zero)", "Verborgen tekst (lettergrootte nul)"),
    "MANY_INVISIBLE_PARTS": ("content", "Many invisible parts", "Veel onzichtbare delen"),
    "R_PARTS_DIFFER": ("content", "Text and HTML version say different things", "Tekst- en HTML-versie zeggen iets anders"),
    "LEAKED_PASSWORD_SCAM": ("content", "Looks like a 'we have your password' scam", "Lijkt op een 'we hebben je wachtwoord'-oplichting"),
    "BITCOIN_ADDR": ("content", "Contains a Bitcoin address", "Bevat een Bitcoin-adres"),
    "SUBJ_ALL_CAPS": ("content", "Subject in capitals", "Onderwerp in hoofdletters"),
    "SUBJ_EXCESS_QP": ("content", "Subject oddly encoded", "Onderwerp vreemd gecodeerd"),
    "SUBJECT_ENDS_EXCLAIM": ("content", "Subject ends with an exclamation mark", "Onderwerp eindigt met een uitroepteken"),
    "SUBJECT_HAS_CURRENCY": ("content", "Subject mentions money", "Onderwerp noemt geld"),
    # Format and headers
    "MIME_GOOD": ("format", "Well-formed message (text and/or HTML)", "Goed opgebouwd bericht (tekst en/of HTML)"),
    "MIME_HTML_ONLY": ("format", "Only an HTML version, no plain text", "Alleen een HTML-versie, geen platte tekst"),
    "MID_RHS_MATCH_FROM": ("format", "Message-ID matches the sender domain", "Message-ID past bij het afzenderdomein"),
    "MISSING_MID": ("format", "No Message-ID", "Geen Message-ID"),
    "MISSING_DATE": ("format", "No date", "Geen datum"),
    "MISSING_TO": ("format", "No To address", "Geen Aan-adres"),
    "MISSING_SUBJECT": ("format", "No subject", "Geen onderwerp"),
    "INVALID_DATE": ("format", "Invalid date", "Ongeldige datum"),
    "DATE_IN_FUTURE": ("format", "Date in the future", "Datum in de toekomst"),
    "DATE_IN_PAST": ("format", "Date long in the past", "Datum ver in het verleden"),
    "R_BAD_CTE_7BIT": ("format", "Wrongly encoded text", "Verkeerd gecodeerde tekst"),
    "BROKEN_CONTENT_TYPE": ("format", "Broken message structure", "Kapotte berichtopbouw"),
    "TO_DN_NONE": ("format", "Recipient without a name", "Ontvanger zonder naam"),
    "TO_MATCH_ENVRCPT_ALL": ("format", "To address matches the real recipient", "Aan-adres klopt met de echte ontvanger"),
    "RCVD_TLS_ALL": ("format", "Sent over an encrypted connection all the way", "Helemaal via een versleutelde verbinding verstuurd"),
    "RCVD_TLS_LAST": ("format", "Last hop over an encrypted connection", "Laatste stap via een versleutelde verbinding"),
    "RCVD_NO_TLS_LAST": ("format", "Last hop without encryption", "Laatste stap zonder versleuting"),
    "HAS_LIST_UNSUB": ("format", "Has an unsubscribe link", "Heeft een afmeldlink"),
    "PREVIOUSLY_DELIVERED": ("format", "Already delivered once to this recipient", "Al eens aan deze ontvanger afgeleverd"),
    # Lists, rules and policy
    "MF_ALLOW_FROM": ("policy", "Sender on the allow list", "Afzender op de toestaanlijst"),
    "MF_ALLOW_FROM_DOMAIN": ("policy", "Sender domain on the allow list", "Afzenderdomein op de toestaanlijst"),
    "MF_ALLOW_IP": ("policy", "Sending IP on the allow list", "Verzendend IP op de toestaanlijst"),
    "MF_ALLOW_FROM_RE": ("policy", "Sender matches an allow list pattern", "Afzender past bij een patroon op de toestaanlijst"),
    "MF_BLOCK_FROM": ("policy", "Sender on the block list", "Afzender op de blokkeerlijst"),
    "MF_BLOCK_FROM_DOMAIN": ("policy", "Sender domain on the block list", "Afzenderdomein op de blokkeerlijst"),
    "MF_BLOCK_IP": ("policy", "Sending IP on the block list", "Verzendend IP op de blokkeerlijst"),
    "MF_BLOCK_FROM_RE": ("policy", "Sender matches a block list pattern", "Afzender past bij een patroon op de blokkeerlijst"),
    "MF_NEWSLETTER": ("policy", "Newsletter", "Nieuwsbrief"),
    "MF_NEWSLETTER_NO_UNSUB": ("policy", "Newsletter without unsubscribe option", "Nieuwsbrief zonder afmeldmogelijkheid"),
    "MF_NEWSLETTER_NO_ONECLICK": ("policy", "Newsletter without one-click unsubscribe", "Nieuwsbrief zonder afmelden met één klik"),
    "MF_NEWSLETTER_NO_DKIM": ("policy", "Newsletter not signed by the sender's domain", "Nieuwsbrief niet ondertekend door het afzenderdomein"),
    "MF_EXTERNAL_BANNER": ("policy", "'External sender' banner added", "Melding 'externe afzender' toegevoegd"),
    "GREYLIST": ("policy", "Greylisting: asked the sender to try again later", "Greylisting: afzender gevraagd het later opnieuw te proberen"),
    "RATELIMITED": ("policy", "Sender is sending too much (rate limit)", "Afzender verstuurt te veel (snelheidslimiet)"),
}

# Rules not in the catalog are sorted by name.
PREFIXES: list[tuple[str, str]] = [
    (r"^(R_SPF_|R_DKIM_|DKIM_|DMARC_|ARC_|FORGED_|SPOOF_|FREEMAIL_|FROM_NEQ_|REPLYTO_|AUTH_NA)", "auth"),
    (r"^(RBL_|RWL_|RCVD_IN_|RECEIVED_|DWL_|RDNS_|HFILTER_|MX_|IP_REPUTATION|SENDER_REP|ASN)", "reputation"),
    (r"(URIBL|SURBL|^DBL_|^PHISH|URL|^URI_|_URI$|^HAS_.*URI|REDIRECTOR)", "links"),
    (r"^(CLAM_|MF_ATTACHMENT_|MF_BAD_ATTACH|MIME_BAD|MIME_DOUBLE|MIME_ARCHIVE|MIME_EXE|MIME_ENCRYPTED|MIME_OBFUSCATED|BAD_ATTACH|VIRUS)", "attachments"),
    (r"^(BAYES_|FUZZY_|NEURAL_|HTML_|R_SUSPICIOUS|R_EMPTY|ZERO_FONT|SUBJ|LEAKED_|BITCOIN|.*_IMG)", "content"),
    (r"^(MF_|MULTIMAP|GREYLIST|RATELIMIT|WHITELIST|FORCE_)", "policy"),
]


def group_of(symbol: str) -> str:
    if symbol in CATALOG:
        return CATALOG[symbol][0]
    for rx, group in PREFIXES:
        if re.search(rx, symbol):
            return group
    return "format"


def label_of(symbol: str, lang: str) -> str | None:
    entry = CATALOG.get(symbol)
    if not entry:
        return None
    return entry[2] if lang == "nl" else entry[1]


def load_thresholds(path: Path = ACTIONS_CONF) -> dict[str, float]:
    out = dict(DEFAULT_THRESHOLDS)
    try:
        text = path.read_text()
    except OSError:
        return out
    for name, value in re.findall(r"^\s*(\w+)\s*=\s*(-?[\d.]+)\s*;", text, re.M):
        if name in out:
            try:
                out[name] = float(value)
            except ValueError:
                pass
    return out


@dataclass
class Item:
    symbol: str
    label: str | None
    score: float
    options: list[str]
    decisive: bool


def _status(group: str, items: list[Item]) -> str:
    """ok, warn, bad or neutral (not checked / nothing to say)."""
    points = sum(i.score for i in items)
    if any(i.decisive and i.score >= 0 and i.symbol.startswith(("CLAM_VIRUS", "MF_BLOCK", "MF_LIST_BLOCK", "MF_ATTACHMENT", "MF_BAD_ATTACH", "GTUBE"))
           for i in items):
        return "bad"
    if points >= 3 or any(i.score >= 3 for i in items):
        return "bad"
    if points > 0.5:
        return "warn"
    if group == "auth" and not items:
        return "neutral"
    if group == "policy" and not any(i.score for i in items):
        return "neutral"
    return "ok"


def build(symbols: dict, options: dict | None, score: float | None, action: str | None,
          direction: str | None, lang: str, thresholds: dict[str, float] | None = None) -> dict | None:
    """The report for one message, or None when Rspamd never scanned it."""
    if score is None:
        return None
    options = options or {}
    groups: dict[str, list[Item]] = {g: [] for g in GROUPS}
    for name, raw in (symbols or {}).items():
        try:
            sc = float(raw.get("score", 0) if isinstance(raw, dict) else raw or 0)
        except (TypeError, ValueError):
            sc = 0.0
        label = label_of(name, lang)
        decisive = bool(DECISIVE.match(name))
        # Rspamd adds many informational rules with 0 points; only keep the ones we can explain.
        if sc == 0 and label is None and not decisive:
            continue
        opts = options.get(name) or []
        groups[group_of(name)].append(Item(name, label, round(sc, 2), [str(o) for o in opts][:10], decisive))
    out = []
    for g in GROUPS:
        items = sorted(groups[g], key=lambda i: (-abs(i.score), i.symbol))
        status = _status(g, items)
        if g == "auth" and not items and direction == "outbound":
            status = "skipped"
        out.append({
            "id": g,
            "status": status,
            "points": round(sum(i.score for i in items), 2),
            "items": [i.__dict__ for i in items],
        })
    return {
        "score": round(score, 2),
        "action": (action or "no action").replace(" ", "_"),
        "thresholds": thresholds or load_thresholds(),
        "groups": out,
        "decisive": [i["symbol"] for grp in out for i in grp["items"] if i["decisive"]],
    }
