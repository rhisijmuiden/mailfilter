import os
import subprocess
import tempfile
import unittest

from mailfilter_config import (OutboundHold, AddressRoute, TlsPolicy, Applier, ApplyError, ConfigError, Destination, DestinationPolicy, Domain,
                               GatewayConfig, OutboundLimits, QueuePolicy, RelayNetwork, RelayPort, render,
                               validate)
from mailfilter_config.cli import import_legacy, parse_destination
from mailfilter_config import LdapSource
from mailfilter_config.render import BEGIN, END, ldap_map_path, service_name, splice_master_cf


def sample() -> GatewayConfig:
    return GatewayConfig(
        hostname="mailfilter.rhis.nl",
        own_addresses=["13.140.223.211", "2a02:c207:2373:943::1"],
        domains=[
            Domain(name="RHIS.nl", customer="rhis",
                   destinations=[Destination("mail.rhis.nl"), Destination("10.0.0.5", 2525)],
                   tls="encrypt", queue=QueuePolicy(concurrency=3), outbound=OutboundLimits(per_hour=5000)),
            Domain(name="bäcker.de", destinations=[Destination("mx.backer.de", mx=True)], verify_recipients=False),
        ],
        relay_networks=[RelayNetwork("203.0.113.25/32", "Webserver 1", "rhis"),
                        RelayNetwork("2a02:58:1f:42::1/64", "Webservers v6")],
        relay_ports=[RelayPort(2525, description="Old webshop")],
        destination_policies=[DestinationPolicy("gmail.com", QueuePolicy(concurrency=5, rate_delay=1))],
        address_routes=[AddressRoute("Info@RHIS.nl", Destination("10.0.0.9", 24, protocol="lmtp"), "Helpdesk")],
        tls_policies=[TlsPolicy(".bank.nl", "secure"), TlsPolicy("[10.0.0.7]:25", "encrypt")],
        holds=[OutboundHold("ip", "203.0.113.25", "45 spam messages in 15 minutes"),
               OutboundHold("account", "Webshop@Klant.nl", "volume spike")],
    )


def codes(cfg):
    return {p.code for p in validate(cfg)}


class ValidateTest(unittest.TestCase):
    def test_sample_is_valid_and_normalised(self):
        cfg = sample()
        self.assertEqual(validate(cfg), [])
        self.assertEqual(cfg.domains[0].name, "rhis.nl")
        self.assertEqual(cfg.domains[1].name, "xn--bcker-gra.de")
        self.assertEqual(cfg.relay_networks[0].cidr, "203.0.113.25")
        self.assertEqual(cfg.relay_networks[1].cidr, "2a02:58:1f:42::/64")

    def test_open_relay_refused(self):
        for net in ("0.0.0.0/0", "::/0"):
            self.assertIn("open_relay", codes(GatewayConfig(relay_networks=[RelayNetwork(net)])))

    def test_broad_network_needs_confirmation(self):
        cfg = GatewayConfig(relay_networks=[RelayNetwork("10.0.0.0/8")])
        self.assertIn("network_too_broad", codes(cfg))
        cfg.relay_networks[0].allow_broad = True
        self.assertEqual(codes(cfg), set())

    def test_loops(self):
        cfg = sample()
        cfg.domains[0].destinations = [Destination("mailfilter.rhis.nl")]
        self.assertIn("loop_self", codes(cfg))
        cfg = sample()
        cfg.domains[0].destinations = [Destination("13.140.223.211")]
        self.assertIn("loop_self", codes(cfg))
        cfg = sample()
        cfg.domains[0].destinations = [Destination("127.0.0.1", 25)]
        self.assertIn("loop_self", codes(cfg))
        cfg.domains[0].destinations = [Destination("127.0.0.1", 10025)]  # a local content filter is fine
        self.assertNotIn("loop_self", codes(cfg))
        cfg = sample()
        cfg.domains[0].destinations = [Destination("rhis.nl", mx=True)]
        self.assertIn("loop_mx", codes(cfg))

    def test_ports(self):
        self.assertIn("port_reserved", codes(GatewayConfig(relay_ports=[RelayPort(587)])))
        self.assertIn("port_duplicate", codes(GatewayConfig(relay_ports=[RelayPort(2525), RelayPort(2525)])))
        self.assertIn("tls_invalid", codes(GatewayConfig(relay_ports=[RelayPort(2525, tls="none")])))

    def test_domain_checks(self):
        self.assertIn("domain_invalid", codes(GatewayConfig(domains=[Domain("not a domain", [Destination("a.nl")])])))
        self.assertIn("destination_missing", codes(GatewayConfig(domains=[Domain("a.nl", [])])))
        self.assertIn("domain_duplicate", codes(GatewayConfig(
            domains=[Domain("a.nl", [Destination("m.a.nl")]), Domain("A.NL", [Destination("m.a.nl")])])))
        cfg = sample()
        cfg.destination_policies.append(DestinationPolicy("rhis.nl"))
        self.assertIn("policy_is_relay_domain", codes(cfg))

    def test_routes_and_tls_policies(self):
        cfg = sample()
        self.assertEqual(validate(cfg), [])
        self.assertEqual(cfg.address_routes[0].address, "info@rhis.nl")
        cfg.address_routes.append(AddressRoute("rhis.nl", Destination("x.nl")))
        cfg.address_routes.append(AddressRoute("not an address@x", Destination("x.nl")))
        cfg.tls_policies.append(TlsPolicy("example.nl", "sometimes"))
        self.assertTrue({"route_is_relay_domain", "address_invalid", "tls_invalid"} <= codes(cfg))
        cfg = sample()
        cfg.domains[0].destinations[1].protocol = "lmtp"
        self.assertIn("protocol_mixed", codes(cfg))

    def test_holds(self):
        cfg = sample()
        self.assertEqual(validate(cfg), [])
        self.assertEqual(cfg.holds[1].value, "webshop@klant.nl")
        for bad in (OutboundHold("ip", "127.0.0.1"), OutboundHold("ip", "nope"), OutboundHold("x", "a")):
            self.assertTrue(codes(GatewayConfig(holds=[bad])) & {"hold_local", "hold_invalid"})
        self.assertIn("hold_duplicate", codes(GatewayConfig(holds=[OutboundHold("ip", "192.0.2.1")] * 2)))

    def test_messages_in_both_languages(self):
        for p in validate(GatewayConfig(relay_networks=[RelayNetwork("0.0.0.0/0")])):
            self.assertTrue(p.en and p.nl and p.en != p.nl)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.cfg = sample()
        self.assertEqual(validate(self.cfg), [])
        self.r = render(self.cfg)

    def test_transport_and_queues(self):
        svc = service_name("mfq_", "rhis.nl")
        transport = self.r.files["/etc/mailfilter/postfix/transport"]
        self.assertIn(f"rhis.nl {svc}:[mail.rhis.nl]:25\n", transport)
        self.assertIn("xn--bcker-gra.de mfq_", transport)
        self.assertIn(":mx.backer.de:25\n", transport)  # MX lookup: no brackets
        self.assertIn(f"gmail.com {service_name('mfo_', 'gmail.com')}:\n", transport)
        self.assertIn(f"{svc} unix", self.r.master_cf_block)
        self.assertIn("smtp_fallback_relay=[10.0.0.5]:2525", self.r.master_cf_block)
        self.assertEqual(self.r.main_cf[f"{svc}_destination_concurrency_limit"], "3")
        self.assertEqual(self.r.main_cf[f"{service_name('mfo_', 'gmail.com')}_destination_rate_delay"], "1s")

    def test_postfix_tables_have_no_inline_comments(self):
        for path in self.r.hash_maps + ["/etc/mailfilter/postfix/relay_networks.cidr"]:
            for line in self.r.files[path].splitlines():
                if not line.startswith("#"):
                    self.assertNotIn("#", line, f"{path}: {line}")

    def test_hold_maps(self):
        self.assertIn("203.0.113.25 HOLD Paused by Mailfilter: 45 spam messages in 15 minutes\n",
                      self.r.files["/etc/mailfilter/postfix/outbound_hold.cidr"])
        self.assertIn("webshop@klant.nl HOLD", self.r.files["/etc/mailfilter/postfix/outbound_hold_sasl"])
        self.assertIn("check_sasl_access", self.r.main_cf["smtpd_end_of_data_restrictions"])

    def test_routes(self):
        transport = self.r.files["/etc/mailfilter/postfix/transport"]
        self.assertIn("# Helpdesk\ninfo@rhis.nl lmtp:[10.0.0.9]:24\n", transport)

    def test_tls_and_verify(self):
        tls = self.r.files["/etc/mailfilter/postfix/tls_policy"]
        self.assertIn("[mail.rhis.nl]:25 encrypt", tls)
        self.assertIn(".bank.nl secure", tls)
        verify = self.r.files["/etc/mailfilter/postfix/recipient_verify"]
        self.assertIn("rhis.nl reject_unverified_recipient", verify)
        self.assertNotIn("xn--bcker-gra.de", verify)

    def test_relay_access(self):
        self.assertIn("203.0.113.25 OK", self.r.files["/etc/mailfilter/postfix/relay_networks.cidr"])
        local = self.r.files["/etc/mailfilter/rspamd/local_addrs.map"]
        self.assertIn("2a02:58:1f:42::/64", local)
        self.assertIn("127.0.0.0/8", local)

    def test_listener_and_firewall(self):
        self.assertIn("2525       inet", self.r.master_cf_block)
        self.assertIn("syslog_name=postfix/port-2525", self.r.master_cf_block)
        self.assertIn("{ 2525 }", self.r.files["/etc/mailfilter/nftables/relay_ports.nft"])

    def test_ratelimit(self):
        rl = self.r.files["/etc/rspamd/local.d/ratelimit.conf"]
        self.assertIn("from('smtp'):domain.lower.not_in('rhis.nl')", rl)
        self.assertIn("from('smtp'):domain.lower.equal('rhis.nl')", rl)
        self.assertIn('rate = "5000 / 1h"', rl)
        self.assertEqual(rl.count("{"), rl.count("}"))

    def test_disabled_items_are_left_out(self):
        self.cfg.domains[0].enabled = False
        self.cfg.relay_networks[0].enabled = False
        r = render(self.cfg)
        self.assertNotIn("rhis.nl", r.files["/etc/mailfilter/postfix/relay_domains"])
        self.assertNotIn("203.0.113.25", r.files["/etc/mailfilter/postfix/relay_networks.cidr"])

    def test_splice_master_cf(self):
        base = "smtp inet n - y - 1 postscreen\n"
        once = splice_master_cf(base, "A")
        self.assertEqual(splice_master_cf(once, "A"), once)
        self.assertEqual(splice_master_cf(once, "B").count(BEGIN), 1)
        templated = f"head\n{BEGIN}\n{END}\ntail\n"
        out = splice_master_cf(templated, "X")
        self.assertTrue(out.startswith("head\n") and out.endswith("tail\n") and "\nX\n" in out)


class FakeServer:
    """Stands in for postconf, postmap, postfix and rspamadm."""

    def __init__(self, fail=None):
        self.params = {"mfq_old_domain_1234_destination_concurrency_limit": "2"}
        self.fail = fail or set()
        self.calls = []
        self.no_ldap = False

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        ok = subprocess.CompletedProcess(cmd, 0, "", "")
        key = " ".join(cmd[:2])
        if key in self.fail or cmd[0] in self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", f"{cmd[0]}: fatal: broken")
        if cmd[:2] == ["postconf", "-n"]:
            return subprocess.CompletedProcess(cmd, 0, "".join(f"{k} = {v}\n" for k, v in self.params.items()), "")
        if cmd[:2] == ["postconf", "-e"]:
            for kv in cmd[2:]:
                k, v = kv.split("=", 1)
                self.params[k] = v
        if cmd[:2] == ["postconf", "-X"]:
            for k in cmd[2:]:
                self.params.pop(k, None)
        if cmd[0] == "nft":
            return subprocess.CompletedProcess(cmd, 1, "", "no such set")
        if cmd[:2] == ["postconf", "-m"]:
            return subprocess.CompletedProcess(cmd, 0, "hash\nlmdb\n" + ("" if self.no_ldap else "ldap\n"), "")
        return ok


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(f"{self.root}/etc/postfix")
        with open(f"{self.root}/etc/postfix/master.cf", "w") as fh:
            fh.write(f"smtp inet n - y - 1 postscreen\n{BEGIN}\n{END}\npickup unix n - y 60 1 pickup\n")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, p):
        with open(self.root + p) as fh:
            return fh.read()

    def test_apply_writes_maps_and_settings(self):
        srv = FakeServer()
        report = Applier(srv, root=self.root).apply(sample())
        self.assertIn("/etc/mailfilter/postfix/transport", report.changed)
        self.assertIn("mfq_old_domain_1234_destination_concurrency_limit", report.params_removed)
        self.assertIn(service_name("mfq_", "rhis.nl") + " unix", self.read("/etc/postfix/master.cf"))
        self.assertTrue(self.read("/etc/postfix/master.cf").endswith("pickup unix n - y 60 1 pickup\n"))
        self.assertIn(["postfix", "reload"], srv.calls)
        self.assertTrue(any(c[0] == "postmap" for c in srv.calls))
        self.assertTrue(report.warnings)  # firewall set missing -> told to re-run the installer
        # second run: nothing to do, no reload
        srv.calls.clear()
        self.assertTrue(Applier(srv, root=self.root).apply(sample()).nothing_changed)
        self.assertNotIn(["postfix", "reload"], srv.calls)

    def test_dkim_key_for_new_domains(self):
        srv = FakeServer()
        os.makedirs(f"{self.root}/etc/mailfilter/rspamd")
        with open(f"{self.root}/etc/mailfilter/rspamd/dkim_selectors.map", "w") as fh:
            fh.write("rhis.nl mf2025\n")
        Applier(srv, root=self.root).apply(sample())
        keygens = [c for c in srv.calls if c[:2] == ["rspamadm", "dkim_keygen"]]
        self.assertEqual([c[c.index("-d") + 1] for c in keygens], ["xn--bcker-gra.de"])  # rhis.nl already had a key
        dkim_map = self.read("/etc/mailfilter/rspamd/dkim_selectors.map")
        self.assertTrue(dkim_map.startswith("rhis.nl mf2025\n") and "xn--bcker-gra.de mf" in dkim_map)

    def test_failed_check_rolls_back(self):
        master_before = self.read("/etc/postfix/master.cf")
        srv = FakeServer(fail={"postfix check"})
        with self.assertRaises(ApplyError):
            Applier(srv, root=self.root).apply(sample())
        self.assertEqual(self.read("/etc/postfix/master.cf"), master_before)
        self.assertFalse(os.path.exists(self.root + "/etc/mailfilter/postfix/transport"))
        self.assertEqual(srv.params, {"mfq_old_domain_1234_destination_concurrency_limit": "2"})
        self.assertNotIn(["postfix", "reload"], srv.calls)

    def test_invalid_config_touches_nothing(self):
        srv = FakeServer()
        with self.assertRaises(ConfigError):
            Applier(srv, root=self.root).apply(GatewayConfig(relay_networks=[RelayNetwork("0.0.0.0/0")]))
        self.assertFalse(any(c[0] in ("postmap", "postfix") for c in srv.calls))


class CliTest(unittest.TestCase):
    def test_parse_destination(self):
        self.assertEqual(parse_destination("[mail.rhis.nl]:2525"), Destination("mail.rhis.nl", 2525))
        self.assertEqual(parse_destination("mail.rhis.nl"), Destination("mail.rhis.nl", 25))
        self.assertEqual(parse_destination("2a02:58::1"), Destination("2a02:58::1", 25))
        self.assertEqual(parse_destination("[2a02:58::1]:26"), Destination("2a02:58::1", 26))

    def test_import_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(f"{root}/etc/mailfilter/postfix")
            with open(f"{root}/etc/mailfilter/relay_networks.list", "w") as fh:
                fh.write("203.0.113.25\n# comment\n\n")
            with open(f"{root}/etc/mailfilter/postfix/relay_domains", "w") as fh:
                fh.write("rhis.nl OK\n")
            with open(f"{root}/etc/mailfilter/postfix/transport", "w") as fh:
                fh.write("rhis.nl relay:[mail.rhis.nl]:25\n")
            cfg = import_legacy(root)
        self.assertEqual([n.cidr for n in cfg.relay_networks], ["203.0.113.25"])
        self.assertEqual(cfg.domains[0].destinations, [Destination("mail.rhis.nl", 25)])

    def test_json_roundtrip(self):
        cfg = sample()
        self.assertEqual(GatewayConfig.from_json(cfg.to_json()), cfg)


if __name__ == "__main__":
    unittest.main()


def ldap_domain(**kw) -> Domain:
    src = LdapSource(servers=["dc1.ad.example.nl", "ldaps://dc2.ad.example.nl"], bind_dn="CN=svc,DC=ad,DC=example,DC=nl",
                     bind_password="geheim $1", base_dn="DC=ad,DC=example,DC=nl", **kw)
    return Domain(name="ad.example.nl", destinations=[Destination("exchange.ad.example.nl")], recipient_check="ldap", ldap=src)


class RecipientCheckTest(unittest.TestCase):
    def cfg(self, *domains):
        return GatewayConfig(hostname="mailfilter.rhis.nl", domains=list(domains))

    def test_old_flag_maps_to_mode(self):
        self.assertEqual(Domain("a.nl").recipient_check, "callout")
        self.assertEqual(Domain("a.nl", verify_recipients=False).recipient_check, "off")
        d = Domain("a.nl", recipient_check="list")
        self.assertTrue(d.verify_recipients)
        self.assertEqual(Domain(**{"name": "a.nl", "recipient_check": "ldap", "ldap": {"servers": ["x"]}}).ldap.servers, ["x"])

    def test_list_is_normalised(self):
        d = Domain("Lijst.nl", [Destination("mail.lijst.nl")], recipient_check="list",
                   recipients=["Info", "sales@LIJST.nl", "info@lijst.nl", " "])
        cfg = self.cfg(d)
        self.assertIn("recipient_invalid", codes(cfg))  # the blank line
        d.recipients = ["Info", "sales@LIJST.nl", "info@lijst.nl"]
        self.assertEqual(codes(cfg), set())
        self.assertEqual(d.recipients, ["info@lijst.nl", "sales@lijst.nl"])

    def test_list_rejects_other_domains_and_empty(self):
        d = Domain("lijst.nl", [Destination("mail.lijst.nl")], recipient_check="list", recipients=["a@other.nl"])
        self.assertIn("recipient_invalid", codes(self.cfg(d)))
        d.recipients = []
        self.assertIn("recipients_empty", codes(self.cfg(d)))

    def test_ldap_validation(self):
        d = ldap_domain()
        self.assertEqual(codes(self.cfg(d)), set())
        self.assertEqual(d.ldap.servers, ["ldap://dc1.ad.example.nl:389", "ldaps://dc2.ad.example.nl:636"])
        bad = ldap_domain()
        bad.ldap.servers = ["dc1 evil"]
        bad.ldap.base_dn = ""
        bad.ldap.bind_password = ""
        bad.ldap.query_filter = "mail=%s"
        self.assertTrue({"ldap_server_invalid", "ldap_base_missing", "ldap_password_missing",
                         "ldap_filter_invalid"} <= codes(self.cfg(bad)))
        nl = ldap_domain()
        nl.ldap.bind_password = "a\nserver_host = ldap://evil"
        self.assertIn("ldap_value_invalid", codes(self.cfg(nl)))
        self.assertIn("ldap_missing", codes(self.cfg(Domain("x.nl", [Destination("m.x.nl")], recipient_check="ldap"))))

    def test_ipv6_ldap_server(self):
        d = ldap_domain()
        d.ldap.servers = ["[2a02:58::5]:3268"]
        self.assertEqual(codes(self.cfg(d)), set())
        self.assertEqual(d.ldap.servers, ["ldap://[2a02:58::5]:3268"])

    def test_render(self):
        cfg = self.cfg(
            Domain("rhis.nl", [Destination("mail.rhis.nl")]),
            Domain("open.nl", [Destination("mail.open.nl")], recipient_check="off"),
            Domain("lijst.nl", [Destination("mail.lijst.nl")], recipient_check="list", recipients=["info"]),
            ldap_domain(),
        )
        cfg.recipient_cache.positive_days = 3
        cfg.recipient_cache.negative_minutes = 30
        self.assertEqual(validate(cfg), [])
        r = render(cfg)
        rcpt = r.files["/etc/mailfilter/postfix/relay_recipients"]
        self.assertIn("@rhis.nl OK\n", rcpt)
        self.assertIn("@open.nl OK\n", rcpt)
        self.assertIn("info@lijst.nl OK\n", rcpt)
        self.assertNotIn("\n@lijst.nl OK", rcpt)
        self.assertNotIn("ad.example.nl", rcpt)
        verify = r.files["/etc/mailfilter/postfix/recipient_verify"]
        self.assertIn("rhis.nl reject_unverified_recipient", verify)
        self.assertNotIn("lijst.nl", verify)
        path = ldap_map_path("ad.example.nl")
        self.assertEqual(r.secret_files, [path])
        self.assertEqual(r.main_cf["relay_recipient_maps"],
                         f"hash:/etc/mailfilter/postfix/relay_recipients, proxy:ldap:{path}")
        cf = r.files[path]
        self.assertIn("domain = ad.example.nl\n", cf)
        self.assertIn("query_filter = (|(mail=%s)(proxyAddresses=smtp:%s))\n", cf)
        self.assertIn("bind_pw = geheim $1\n", cf)
        self.assertNotIn("start_tls", cf)  # one server is ldaps://: no STARTTLS on top
        self.assertIn("tls_require_cert = yes", cf)
        self.assertEqual(r.main_cf["address_verify_positive_expire_time"], "3d")
        self.assertEqual(r.main_cf["address_verify_negative_refresh_time"], "30m")

    def test_starttls_and_anonymous(self):
        d = ldap_domain(security="starttls", verify_cert=False, preset="generic")
        d.ldap.servers = ["ldap.example.nl"]
        d.ldap.bind_dn = d.ldap.bind_password = ""
        cfg = self.cfg(d)
        self.assertEqual(validate(cfg), [])
        cf = render(cfg).files[ldap_map_path("ad.example.nl")]
        self.assertIn("start_tls = yes", cf)
        self.assertIn("bind = no", cf)
        self.assertIn("tls_require_cert = no", cf)
        self.assertIn("mailAlternateAddress", cf)

    def test_relay_domains_are_not_recipients(self):
        cfg = self.cfg(Domain("out.nl", kind="relay"))
        self.assertEqual(validate(cfg), [])
        self.assertNotIn("out.nl", render(cfg).files["/etc/mailfilter/postfix/relay_recipients"])

    def test_json_roundtrip(self):
        cfg = self.cfg(ldap_domain(), Domain("l.nl", [Destination("m.l.nl")], recipient_check="list", recipients=["a@l.nl"]))
        self.assertEqual(GatewayConfig.from_json(cfg.to_json()), cfg)


class RecipientApplyTest(ApplyTest):
    def test_ldap_file_written_and_stale_removed(self):
        srv = FakeServer()
        cfg = GatewayConfig(hostname="mailfilter.rhis.nl", domains=[ldap_domain()])
        Applier(srv, root=self.root).apply(cfg)
        path = ldap_map_path("ad.example.nl")
        self.assertEqual(os.stat(self.root + path).st_mode & 0o777, 0o640)
        self.assertEqual(os.stat(self.root + "/etc/mailfilter/gateway.json").st_mode & 0o777, 0o640)
        self.assertIn("proxy:ldap:", srv.params["relay_recipient_maps"])
        cfg = GatewayConfig(hostname="mailfilter.rhis.nl", domains=[ldap_domain()])
        cfg.domains[0].recipient_check = "callout"
        report = Applier(srv, root=self.root).apply(cfg)
        self.assertFalse(os.path.exists(self.root + path))
        self.assertIn(path, report.changed)
        self.assertNotIn("ldap", srv.params["relay_recipient_maps"])

    def test_without_postfix_ldap(self):
        srv = FakeServer()
        srv.no_ldap = True
        with self.assertRaises(ApplyError) as e:
            Applier(srv, root=self.root).apply(GatewayConfig(hostname="mailfilter.rhis.nl", domains=[ldap_domain()]))
        self.assertEqual(e.exception.step, "ldap")

    def test_unreadable_ldap_table_rolls_back(self):
        srv = FakeServer(fail={"postmap -q"})
        with self.assertRaises(ApplyError) as e:
            Applier(srv, root=self.root).apply(GatewayConfig(hostname="mailfilter.rhis.nl", domains=[ldap_domain()]))
        self.assertEqual(e.exception.step, "ldap")
        self.assertFalse(os.path.exists(self.root + ldap_map_path("ad.example.nl")))
        self.assertNotIn(["postfix", "reload"], srv.calls)
