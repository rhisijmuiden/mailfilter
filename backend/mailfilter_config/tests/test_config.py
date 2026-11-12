import os
import subprocess
import tempfile
import unittest

from mailfilter_config import (AddressRoute, TlsPolicy, Applier, ApplyError, ConfigError, Destination, DestinationPolicy, Domain,
                               GatewayConfig, OutboundLimits, QueuePolicy, RelayNetwork, RelayPort, render,
                               validate)
from mailfilter_config.cli import import_legacy, parse_destination
from mailfilter_config.render import BEGIN, END, service_name, splice_master_cf


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
