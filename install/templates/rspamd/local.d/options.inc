# Managed by Mailfilter.
# Servers allowed to relay count as "local": they skip greylisting and SPF,
# but their mail is still scored for spam and viruses.
local_addrs = "/etc/mailfilter/rspamd/local_addrs.map";
dns {
  nameserver = ["127.0.0.1:53"];
  timeout = 2s;
  retransmits = 3;
}
