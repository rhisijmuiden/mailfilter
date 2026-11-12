from mailfilter import spamreport

T = {"greylist": 4.0, "add_header": 5.0, "quarantine": 8.0, "reject": 15.0}


def groups(r):
    return {g["id"]: g for g in r["groups"]}


def test_clean_message_passes_every_check():
    r = spamreport.build(
        {"R_SPF_ALLOW": -0.2, "R_DKIM_ALLOW": -0.2, "DMARC_POLICY_ALLOW": -0.5, "BAYES_HAM": -1.5, "ARC_NA": 0, "ASN": 0},
        {"R_SPF_ALLOW": ["+ip4:192.0.2.0/24"]}, -2.4, "no action", "inbound", "nl", T)
    g = groups(r)
    assert r["action"] == "no_action" and r["score"] == -2.4
    assert g["auth"]["status"] == "ok" and g["auth"]["points"] == -0.9
    assert g["auth"]["items"][0]["symbol"] == "DMARC_POLICY_ALLOW"
    spf = next(i for i in g["auth"]["items"] if i["symbol"] == "R_SPF_ALLOW")
    assert spf["label"].startswith("SPF: de verzendende server mag mailen") and spf["options"] == ["+ip4:192.0.2.0/24"]
    assert g["content"]["status"] == "ok"
    assert all(g[k]["status"] == "ok" for k in ("reputation", "links", "attachments", "format"))
    assert g["policy"]["status"] == "neutral"
    # unexplained zero-score rules are left out of the checklist
    assert not any(i["symbol"] in ("ARC_NA", "ASN") for grp in r["groups"] for i in grp["items"])


def test_spam_message_explains_what_failed():
    r = spamreport.build(
        {"R_SPF_FAIL": 1.0, "DBL_SPAM": 6.5, "RBL_MAILSPIKE_BAD": 1.5, "BAYES_SPAM": 2.4, "SOME_NEW_RULE": 0.3},
        {"DBL_SPAM": ["rabobank-veiligheid.info"]}, 11.7, "quarantine", "inbound", "en", T)
    g = groups(r)
    assert g["links"]["status"] == "bad" and g["links"]["items"][0]["options"] == ["rabobank-veiligheid.info"]
    assert g["reputation"]["status"] == "warn"
    assert g["auth"]["status"] == "warn"
    assert g["format"]["items"][0] == {"symbol": "SOME_NEW_RULE", "label": None, "score": 0.3, "options": [], "decisive": False}


def test_virus_and_lists_are_decisive():
    r = spamreport.build({"CLAM_VIRUS": 0, "MF_ALLOW_FROM": -20}, {}, -20, "quarantine", "inbound", "en", T)
    g = groups(r)
    assert g["attachments"]["status"] == "bad"
    assert set(r["decisive"]) == {"CLAM_VIRUS", "MF_ALLOW_FROM"}


def test_outbound_skips_sender_checks_and_unscanned_has_no_report():
    r = spamreport.build({}, {}, 0.1, "no action", "outbound", "en", T)
    assert groups(r)["auth"]["status"] == "skipped"
    assert spamreport.build({}, {}, None, None, "inbound", "en", T) is None


def test_thresholds_from_actions_conf(tmp_path):
    p = tmp_path / "actions.conf"
    p.write_text("# x\nreject = 20;\nquarantine = 9.5;\nadd_header = 5;\ngreylist = 4;\n")
    assert spamreport.load_thresholds(p) == {"greylist": 4.0, "add_header": 5.0, "quarantine": 9.5, "reject": 20.0}
    assert spamreport.load_thresholds(tmp_path / "missing") == spamreport.DEFAULT_THRESHOLDS
