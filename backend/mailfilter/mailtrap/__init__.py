"""Mailtrap: mail from test environments is stored in a mailbox instead of sent.

Postfix (main.cf/master.cf, maps from mailfilter_config) hands that mail to the
``mftrap`` pipe, which runs ``mailfilter-trap-deliver`` (deliver.py) as the
mailfilter user. It writes one file per message in the mailbox's Maildir
(store.py), where the web app and Dovecot (IMAP) read it. checks.py looks at
each message for the mistakes test mail is made to catch.

This package only uses the standard library, so the pipe starts fast.
"""
