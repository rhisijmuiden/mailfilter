"""Catalog entries for phishing protection, the attachment policy and ARC
(Configuration > Custom rules). Same shape as the newsletter rules in rules.py.

Phishing and attachment rules run in rspamd/mailfilter_protection.lua; the ARC
rules are written to Rspamd's own arc module (local.d/arc.conf) by rules.py.
"""
from __future__ import annotations

from typing import Any


def _t(en: str, nl: str) -> dict[str, str]:
    return {"en": en, "nl": nl}


# Executable and script types that have no business in e-mail. Rspamd also
# recognises them by their content, so renaming invoice.exe to invoice.pdf
# does not help.
DANGEROUS_EXTENSIONS = [
    "ade", "adp", "app", "appx", "appxbundle", "bat", "cab", "chm", "cmd", "com", "cpl", "dll", "exe",
    "gadget", "hta", "img", "inf", "ins", "iso", "isp", "jar", "jnlp", "js", "jse", "lnk", "mde", "msc",
    "msi", "msix", "msp", "mst", "one", "pif", "ps1", "ps2", "psc1", "reg", "scf", "scr", "sct", "shb",
    "sys", "url", "vb", "vbe", "vbs", "vhd", "vhdx", "vxd", "wsc", "wsf", "wsh", "xll",
]

TRUSTED_ARC_SEALERS = ["google.com", "microsoft.com", "yahoo.com", "icloud.com"]

ACTIONS = [
    {"id": "reject", "label": _t("Refuse the message", "Bericht weigeren")},
    {"id": "quarantine", "label": _t("Put the message in quarantine", "Bericht in quarantaine zetten")},
]

PHISHING: list[dict[str, Any]] = [
    {
        "id": "phishing_lookalike",
        "group": "phishing",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_LOOKALIKE_DOMAIN",
        "title": _t("Warn about domains that look like yours", "Waarschuwen voor domeinen die op dat van jou lijken"),
        "summary": _t(
            "Raises the spam score when the sender's domain looks like one of your own domains, for example "
            "rhls.nl or rhis-nl.com instead of rhis.nl.",
            "Verhoogt de spamscore als het domein van de afzender lijkt op een van je eigen domeinen, "
            "bijvoorbeeld rhls.nl of rhis-nl.com in plaats van rhis.nl."),
        "details": _t(
            "Criminals register a domain that differs from yours by one letter or a look-alike character (rn for "
            "m, 0 for o, 1 for l) and send invoices or payment requests 'from a colleague'. Mailfilter compares "
            "the From address, the envelope sender and the Reply-To address with every domain in Domains, plus "
            "the extra names below. Your own domains themselves never count; DMARC already protects those. "
            "With the default score of 7, a lookalike message that is a little suspicious otherwise goes to "
            "quarantine.",
            "Criminelen registreren een domein dat één letter of een lijkend teken verschilt van dat van jou (rn "
            "voor m, 0 voor o, 1 voor l) en sturen facturen of betaalverzoeken 'van een collega'. Mailfilter "
            "vergelijkt het Van-adres, de envelopafzender en het Antwoord-aan-adres met alle domeinen onder "
            "Domeinen en de extra namen hieronder. Je eigen domeinen zelf tellen nooit mee; die beschermt DMARC "
            "al. Met de standaard score van 7 gaat een lijkend bericht dat ook maar een beetje verdacht is naar "
            "quarantaine."),
        "options": [
            {"id": "score", "type": "score", "default": 7.0, "label": _t("Spam score", "Spamscore")},
            {"id": "other_tld", "type": "bool", "default": True,
             "label": _t("Same name with another extension counts too (rhis.com for rhis.nl)",
                         "Zelfde naam met een andere extensie telt ook (rhis.com voor rhis.nl)")},
            {"id": "extra", "type": "list", "item": "domain", "default": [], "max_items": 50,
             "label": _t("Extra domains to protect (for example your bank)",
                         "Extra domeinen om te beschermen (bijvoorbeeld je bank)")},
        ],
    },
    {
        "id": "phishing_display_name",
        "group": "phishing",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_DISPLAYNAME_SPOOF",
        "title": _t("Warn about a fake sender name (CEO fraud)", "Waarschuwen voor een valse afzendernaam (CEO-fraude)"),
        "summary": _t(
            "Raises the spam score when an outside sender uses a name that pretends to be someone from your "
            "organisation, or puts a different e-mail address in the name.",
            "Verhoogt de spamscore als een externe afzender een naam gebruikt die doet alsof het iemand uit je "
            "organisatie is, of een ander e-mailadres in de naam zet."),
        "details": _t(
            "Mail programs often show only the name, so 'Jan de Vries <jan.devries.ceo@gmail.com>' looks like "
            "it comes from your director. The rule fires when a message from outside your own domains has a name "
            "that contains an e-mail address of another domain, contains one of your own domain names, or "
            "matches one of the protected names below (for example the names of your directors and finance "
            "staff).",
            "Mailprogramma's tonen vaak alleen de naam, dus 'Jan de Vries <jan.devries.ceo@gmail.com>' lijkt "
            "van je directeur te komen. De regel slaat aan als een bericht van buiten je eigen domeinen een naam "
            "heeft met een e-mailadres van een ander domein erin, een van je eigen domeinnamen erin, of als de "
            "naam gelijk is aan een van de beschermde namen hieronder (bijvoorbeeld de namen van je directie en "
            "de financiële afdeling)."),
        "options": [
            {"id": "score", "type": "score", "default": 5.0, "label": _t("Spam score", "Spamscore")},
            {"id": "names", "type": "list", "item": "name", "default": [], "max_items": 100,
             "label": _t("Protected names", "Beschermde namen")},
        ],
    },
    {
        "id": "phishing_links",
        "group": "phishing",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_SUSPICIOUS_LINK",
        "title": _t("Warn about suspicious links", "Waarschuwen voor verdachte links"),
        "summary": _t(
            "Raises the spam score of messages with links that hide where they really go: link text showing "
            "another site, links to a bare IP address, or links to a domain that looks like yours.",
            "Verhoogt de spamscore van berichten met links die verbergen waar ze echt heen gaan: linktekst die "
            "een andere site toont, links naar een kaal IP-adres, of links naar een domein dat op dat van jou "
            "lijkt."),
        "details": _t(
            "Examples: the text says https://www.ing.nl but the link goes to ing-veilig.example; a link to "
            "http://185.12.3.4/login; a link to rhls.nl when you own rhis.nl. Optionally links via URL "
            "shorteners (bit.ly, tinyurl and the like) count too; those are also used by normal senders, so this "
            "is off by default.",
            "Voorbeelden: de tekst zegt https://www.ing.nl maar de link gaat naar ing-veilig.example; een link "
            "naar http://185.12.3.4/login; een link naar rhls.nl terwijl jij rhis.nl hebt. Desgewenst tellen "
            "links via linkverkorters (bit.ly, tinyurl en dergelijke) ook mee; die gebruiken gewone afzenders "
            "ook, daarom staat dat standaard uit."),
        "options": [
            {"id": "score", "type": "score", "default": 3.5, "label": _t("Spam score", "Spamscore")},
            {"id": "shorteners", "type": "bool", "default": False,
             "label": _t("Links via URL shorteners count too", "Links via linkverkorters tellen ook")},
        ],
    },
    {
        "id": "external_banner",
        "group": "phishing",
        "default": False,
        "direction": "inbound",
        "symbol": "MF_EXTERNAL_BANNER",
        "title": _t("Show an 'External sender' banner", "Banner 'Externe afzender' tonen"),
        "summary": _t(
            "Puts a short warning at the top of messages from outside your own domains, so people think twice "
            "before clicking links or paying an invoice.",
            "Zet een korte waarschuwing boven berichten van buiten je eigen domeinen, zodat mensen even nadenken "
            "voor ze op een link klikken of een factuur betalen."),
        "details": _t(
            "The banner is added to the text and HTML version of the message. Signed (S/MIME, PGP) and encrypted "
            "messages are left alone. Changing the message breaks the sender's DKIM signature: only switch this "
            "on when the mail server behind Mailfilter does not check DKIM or DMARC again. Mail that gets a "
            "banner is not ARC-sealed for your own mail server. Choose 'only suspicious messages' to add the "
            "banner only when one of the phishing rules above fires.",
            "De banner komt in de tekst- en HTML-versie van het bericht. Ondertekende (S/MIME, PGP) en "
            "versleutelde berichten blijven ongemoeid. Het bericht aanpassen maakt de DKIM-handtekening van de "
            "afzender ongeldig: zet dit alleen aan als de mailserver achter Mailfilter DKIM of DMARC niet nog "
            "eens controleert. Mail met een banner krijgt geen ARC-zegel voor je eigen mailserver. Kies 'alleen "
            "verdachte berichten' om de banner alleen te tonen als een van de phishingregels hierboven aanslaat."),
        "options": [
            {"id": "language", "type": "choice", "default": "both", "label": _t("Language", "Taal"),
             "choices": [{"id": "nl", "label": _t("Dutch", "Nederlands")},
                         {"id": "en", "label": _t("English", "Engels")},
                         {"id": "both", "label": _t("Dutch and English", "Nederlands en Engels")}]},
            {"id": "text", "type": "text", "default": "", "max_length": 300,
             "label": _t("Own text (empty: standard text)", "Eigen tekst (leeg: standaardtekst)")},
            {"id": "only_suspicious", "type": "bool", "default": False,
             "label": _t("Only suspicious messages", "Alleen verdachte berichten")},
        ],
    },
]

ATTACHMENTS: list[dict[str, Any]] = [
    {
        "id": "attachments_dangerous",
        "group": "attachments",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_ATTACHMENT_BLOCKED",
        "title": _t("Block dangerous file types", "Gevaarlijke bestandstypen blokkeren"),
        "summary": _t(
            "Refuses messages with programs and scripts attached (.exe, .js, .iso, .lnk and more), also when "
            "they are inside a zip file or renamed.",
            "Weigert berichten met programma's en scripts als bijlage (.exe, .js, .iso, .lnk en meer), ook als "
            "ze in een zipbestand zitten of hernoemd zijn."),
        "details": _t(
            "Nobody sends programs by e-mail any more; these file types are almost only used to install "
            "malware. Mailfilter looks at the file name, at double extensions such as factuur.pdf.exe, at files "
            "inside zip, rar and 7z archives, and at the real content of the file. The sender gets a clear "
            "error message. Remove a type from the list to allow it again, or choose quarantine to review "
            "these messages yourself.",
            "Niemand verstuurt nog programma's per mail; deze bestandstypen worden bijna alleen gebruikt om "
            "malware te installeren. Mailfilter kijkt naar de bestandsnaam, naar dubbele extensies zoals "
            "factuur.pdf.exe, naar bestanden in zip-, rar- en 7z-archieven en naar de echte inhoud van het "
            "bestand. De afzender krijgt een duidelijke foutmelding. Haal een type uit de lijst om het weer toe "
            "te staan, of kies quarantaine om deze berichten zelf te beoordelen."),
        "options": [
            {"id": "action", "type": "choice", "default": "reject", "label": _t("What to do", "Wat te doen"),
             "choices": ACTIONS},
            {"id": "extensions", "type": "list", "item": "extension", "default": DANGEROUS_EXTENSIONS,
             "max_items": 200, "label": _t("File types", "Bestandstypen")},
            {"id": "outbound", "type": "bool", "default": False,
             "label": _t("Also check outgoing mail", "Ook uitgaande mail controleren")},
        ],
    },
    {
        "id": "attachments_macros",
        "group": "attachments",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_ATTACHMENT_MACRO",
        "title": _t("Quarantine Office files with macros", "Office-bestanden met macro's in quarantaine"),
        "summary": _t(
            "Puts messages with Word, Excel or PowerPoint files that contain macros in quarantine.",
            "Zet berichten met Word-, Excel- of PowerPoint-bestanden waarin macro's zitten in quarantaine."),
        "details": _t(
            "Macros are small programs inside an Office file and a common way to infect a computer. Mailfilter "
            "recognises macro file types (.docm, .xlsm, .pptm and others) and also looks inside .docx, .xlsx, "
            ".doc and .xls files for macro code, because criminals rename files to hide it. Release a message "
            "from the quarantine when you trust it.",
            "Macro's zijn kleine programma's in een Office-bestand en een veelgebruikte manier om een computer "
            "te besmetten. Mailfilter herkent macro-bestandstypen (.docm, .xlsm, .pptm en andere) en kijkt ook "
            "in .docx-, .xlsx-, .doc- en .xls-bestanden naar macrocode, omdat criminelen bestanden hernoemen om "
            "die te verbergen. Geef een bericht vrij uit de quarantaine als je het vertrouwt."),
        "options": [
            {"id": "action", "type": "choice", "default": "quarantine", "label": _t("What to do", "Wat te doen"),
             "choices": ACTIONS},
        ],
    },
    {
        "id": "attachments_encrypted",
        "group": "attachments",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_ATTACHMENT_ENCRYPTED",
        "title": _t("Quarantine password-protected archives", "Archieven met wachtwoord in quarantaine"),
        "summary": _t(
            "Puts messages with a password-protected zip, rar or 7z file in quarantine, because the virus "
            "scanner cannot look inside.",
            "Zet berichten met een zip-, rar- of 7z-bestand met wachtwoord in quarantaine, omdat de virusscanner "
            "er niet in kan kijken."),
        "details": _t(
            "Criminals put malware in an archive with a password and write the password in the message, so "
            "virus scanners see nothing. Some companies send documents this way on purpose; release those from "
            "the quarantine, or switch the rule off for the domain that receives them.",
            "Criminelen stoppen malware in een archief met een wachtwoord en zetten het wachtwoord in het "
            "bericht, zodat virusscanners niets zien. Sommige bedrijven sturen documenten bewust zo; geef die "
            "vrij uit de quarantaine, of zet de regel uit voor het domein dat ze ontvangt."),
        "options": [
            {"id": "action", "type": "choice", "default": "quarantine", "label": _t("What to do", "Wat te doen"),
             "choices": ACTIONS},
        ],
    },
]

ARC: list[dict[str, Any]] = [
    {
        "id": "arc_trusted_forwarders",
        "group": "arc",
        "default": True,
        "direction": "inbound",
        "per_domain": False,
        "symbol": "ARC_ALLOW_TRUSTED",
        "title": _t("Trust forwarded mail sealed by known providers", "Doorgestuurde mail van bekende providers vertrouwen"),
        "summary": _t(
            "Forwarded mail often fails DMARC because forwarding changes it. When a trusted provider (Gmail, "
            "Microsoft 365) vouches with an ARC seal that the message passed DMARC before forwarding, Mailfilter "
            "does not count the DMARC failure.",
            "Doorgestuurde mail faalt vaak voor DMARC, omdat doorsturen het bericht verandert. Als een "
            "vertrouwde provider (Gmail, Microsoft 365) met een ARC-zegel verklaart dat het bericht voor het "
            "doorsturen wel door DMARC kwam, telt Mailfilter de DMARC-fout niet mee."),
        "details": _t(
            "ARC (Authenticated Received Chain, RFC 8617) lets every server that forwards a message record what "
            "SPF, DKIM and DMARC said when it received it, and seal that with its own signature. Mailfilter "
            "always checks ARC seals; this rule decides whose seals are trusted. Only add providers you trust, "
            "because a trusted sealer can make a fake message look genuine.",
            "Met ARC (Authenticated Received Chain, RFC 8617) legt elke server die een bericht doorstuurt vast "
            "wat SPF, DKIM en DMARC zeiden toen hij het ontving, en verzegelt dat met zijn eigen handtekening. "
            "Mailfilter controleert ARC-zegels altijd; deze regel bepaalt wiens zegels vertrouwd worden. Voeg "
            "alleen providers toe die je vertrouwt, want een vertrouwde verzegelaar kan een nepbericht echt laten "
            "lijken."),
        "options": [
            {"id": "sealers", "type": "list", "item": "domain", "default": TRUSTED_ARC_SEALERS, "max_items": 50,
             "label": _t("Trusted sealers", "Vertrouwde verzegelaars")},
        ],
    },
    {
        "id": "arc_seal_outbound",
        "group": "arc",
        "default": True,
        "direction": "outbound",
        "per_domain": False,
        "symbol": "ARC_SIGNED",
        "title": _t("ARC-seal outgoing mail", "Uitgaande mail met ARC verzegelen"),
        "summary": _t(
            "Seals mail that your servers send or forward through Mailfilter, so Gmail and Microsoft still "
            "accept forwarded mail that no longer passes DMARC.",
            "Verzegelt mail die je servers via Mailfilter versturen of doorsturen, zodat Gmail en Microsoft "
            "doorgestuurde mail die niet meer door DMARC komt toch accepteren."),
        "details": _t(
            "Think of a mailbox with a forward to Gmail: the original sender's DMARC fails after forwarding, and "
            "Gmail may refuse it. With an ARC seal Mailfilter records that the message was fine when it came in. "
            "The seal uses the DKIM key of the sending domain, so the domain needs a DKIM key in Mailfilter.",
            "Denk aan een mailbox die doorstuurt naar Gmail: na het doorsturen faalt de DMARC van de "
            "oorspronkelijke afzender en kan Gmail het weigeren. Met een ARC-zegel legt Mailfilter vast dat het "
            "bericht in orde was toen het binnenkwam. Het zegel gebruikt de DKIM-sleutel van het verzendende "
            "domein, dus dat domein heeft een DKIM-sleutel in Mailfilter nodig."),
        "options": [],
    },
    {
        "id": "arc_seal_inbound",
        "group": "arc",
        "default": False,
        "direction": "inbound",
        "per_domain": False,
        "symbol": "ARC_SIGNED",
        "title": _t("ARC-seal incoming mail for your mail servers", "Binnenkomende mail met ARC verzegelen voor je mailservers"),
        "summary": _t(
            "Seals incoming mail before Mailfilter hands it to your own mail servers, so they can trust Mailfilter's "
            "SPF, DKIM and DMARC results.",
            "Verzegelt binnenkomende mail voordat Mailfilter die aan je eigen mailservers geeft, zodat die de "
            "SPF-, DKIM- en DMARC-uitslag van Mailfilter kunnen vertrouwen."),
        "details": _t(
            "Useful when the mail server behind Mailfilter (for example Microsoft 365 or Google Workspace) checks "
            "DMARC again and sees Mailfilter as the sender. The seal uses the DKIM key of the recipient's domain; "
            "domains without a key are not sealed. Add Mailfilter as a trusted ARC sealer in your mail server.",
            "Handig als de mailserver achter Mailfilter (bijvoorbeeld Microsoft 365 of Google Workspace) DMARC "
            "opnieuw controleert en Mailfilter als afzender ziet. Het zegel gebruikt de DKIM-sleutel van het "
            "domein van de ontvanger; domeinen zonder sleutel worden niet verzegeld. Stel Mailfilter in je "
            "mailserver in als vertrouwde ARC-verzegelaar."),
        "options": [],
    },
]

PROTECTION_RULES = PHISHING + ATTACHMENTS + ARC
