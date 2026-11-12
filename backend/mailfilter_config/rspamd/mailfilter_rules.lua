--[[
Managed by Mailfilter (Configuration > Custom rules). Overwritten on every save.

Newsletter recognition and the custom rules that build on it. Which rules are
on, their options and per-domain overrides come from
/etc/rspamd/mailfilter_rules.json, written by mailfilter-config; Rspamd is
reloaded after every change. The rule texts shown in the web interface live in
backend/mailfilter_config/rules.py.

Symbols:
  MF_NEWSLETTER             newsletter / bulk mail recognised (score 0, options = why)
  MF_NEWSLETTER_NO_UNSUB    newsletter without any way to unsubscribe
  MF_NEWSLETTER_NO_ONECLICK newsletter without one-click unsubscribe (RFC 8058)
  MF_NEWSLETTER_NO_DKIM     newsletter without a DKIM signature of the sender's domain
]]

local rspamd_logger = require "rspamd_logger"
local rspamd_util = require "rspamd_util"
local lua_mime = require "lua_mime"
local ucl = require "ucl"

local N = 'mailfilter_rules'
local SETTINGS = '/etc/rspamd/mailfilter_rules.json'
local GROUP = 'mailfilter'

local rules = {}
do
  local parser = ucl.parser()
  local ok, err = parser:parse_file(SETTINGS)
  if ok then
    local obj = parser:get_object() or {}
    rules = obj.rules or {}
  else
    rspamd_logger.infox(rspamd_config, '%s: cannot read %s (%s); custom rules are off', N, SETTINGS, err)
  end
end

local function rule(id)
  return rules[id] or {}
end

local function option(id, name, default)
  local o = rule(id).options
  if type(o) == 'table' and o[name] ~= nil then
    return o[name]
  end
  return default
end

-- On for this message when it is on for at least one of the message's own
-- domains (per-domain choice first, then the global switch).
local function rule_on(id, domains)
  local r = rules[id]
  if not r then
    return false
  end
  local per = r.domains or {}
  if #domains == 0 then
    return r.enabled == true
  end
  for _, d in ipairs(domains) do
    local v = per[d]
    if v == nil then
      v = r.enabled
    end
    if v == true then
      return true
    end
  end
  return false
end

-- Mail from relay servers (local_addrs) or SMTP accounts is outgoing.
local function is_outbound(task)
  if task:get_user() then
    return true
  end
  local ip = task:get_from_ip()
  return (ip and ip:is_valid() and ip:is_local()) and true or false
end

-- Incoming: the recipients' domains. Outgoing: the sender's domain.
local function message_domains(task, outbound)
  local out, seen = {}, {}
  local function add(d)
    if type(d) == 'string' and d ~= '' then
      d = d:lower()
      if not seen[d] then
        seen[d] = true
        out[#out + 1] = d
      end
    end
  end
  if outbound then
    local f = task:get_from('smtp')
    if not (f and f[1] and f[1].domain ~= '') then
      f = task:get_from('mime')
    end
    if f and f[1] then
      add(f[1].domain)
    end
  else
    for _, r in ipairs(task:get_recipients('smtp') or task:get_recipients('mime') or {}) do
      add(r.domain)
    end
  end
  return out
end

local function header(task, name)
  local h = task:get_header(name)
  if h and h ~= '' then
    return h
  end
  return nil
end

-- Mailing tools that are only used for bulk mail (X-Mailer, lower case).
local BULK_MAILERS = {
  'mailchimp', 'mailjet', 'sendinblue', 'brevo', 'laposta', 'mailerlite', 'mailpoet', 'phplist',
  'mautic', 'sendy', 'cleverreach', 'spotler', 'mailplus', 'copernica', 'activecampaign', 'klaviyo',
  'campaign monitor', 'createsend', 'constant contact', 'hubspot', 'getresponse', 'aweber',
  'emailoctopus', 'moosend', 'flexmail', 'enormail', 'mailblue', 'acumbamail', 'newsletter2go',
  'rapidmail', 'sendgrid marketing', 'mailingwork', 'inxmail', 'emarsys', 'selligent',
}

-- Headers that e-mail marketing platforms only add to campaigns.
local CAMPAIGN_HEADERS = {
  'X-Campaign', 'X-Campaign-Id', 'X-CampaignID', 'X-Mailjet-Campaign', 'X-Mailin-Campaign',
  'X-Roving-Campaignid', 'X-Newsletter', 'X-Newsletter-Id', 'X-CSA-Complaints', 'X-Mailchimp-Campaign',
  'X-MC-Campaign', 'X-Klaviyo-Campaign', 'X-HS-Campaign',
}

-- Words for "unsubscribe" in the languages Mailfilter users get mail in.
local UNSUB_WORDS = {
  'unsubscribe', 'uitschrijven', 'uitschrijf', 'afmelden', 'afmelding', 'abmelden', 'abbestellen',
  'désinscri', 'desinscri', 'désabonner', 'desabonner', 'darse de baja', 'cancelar suscrip',
  'disiscriv', 'opt-out', 'opt out', 'optout', 'manage your subscription', 'manage preferences',
  'email preferences', 'e-mailvoorkeuren', 'nieuwsbrief opzeggen',
}
local UNSUB_URL = { 'unsub', 'optout', 'opt-out', 'opt_out', 'afmeld', 'uitschrijv', 'abmeld', 'abbestell' }

local function contains_any(s, list)
  for _, w in ipairs(list) do
    if s:find(w, 1, true) then
      return true
    end
  end
  return false
end

-- Why this looks like a newsletter (empty list: it does not).
local function newsletter_signals(task)
  local why = {}
  -- Bounces and automatic replies are not newsletters.
  local env_from = task:get_from('smtp')
  if env_from and env_from[1] and env_from[1].addr == '' then
    return why
  end
  local auto = header(task, 'Auto-Submitted')
  if auto and auto:lower():find('auto-replied', 1, true) then
    return why
  end
  if header(task, 'List-Unsubscribe') then
    why[#why + 1] = 'list-unsubscribe'
  end
  if header(task, 'List-Id') then
    why[#why + 1] = 'list-id'
  end
  local prec = header(task, 'Precedence')
  if prec then
    prec = prec:lower()
    if prec:find('bulk', 1, true) or prec:find('list', 1, true) then
      why[#why + 1] = 'precedence-' .. (prec:find('bulk', 1, true) and 'bulk' or 'list')
    end
  end
  for _, h in ipairs(CAMPAIGN_HEADERS) do
    if header(task, h) then
      why[#why + 1] = 'header-' .. h:lower()
      break
    end
  end
  local mailer = header(task, 'X-Mailer')
  if mailer then
    mailer = mailer:lower()
    for _, m in ipairs(BULK_MAILERS) do
      if mailer:find(m, 1, true) then
        why[#why + 1] = 'mailer-' .. m:gsub('%s', '-')
        break
      end
    end
  end
  return why
end

local MAX_SCAN = 300 * 1024

local function body_has_unsubscribe(task)
  for _, u in ipairs(task:get_urls(true) or {}) do
    local s = (u:get_text() or ''):lower()
    local visible = u.get_visible and u:get_visible() or nil
    if contains_any(s, UNSUB_URL) or (s:sub(1, 7) == 'mailto:' and s:find('unsub', 1, true))
        or (visible and contains_any(tostring(visible):lower(), UNSUB_WORDS)) then
      return true
    end
  end
  for _, p in ipairs(task:get_text_parts() or {}) do
    local c = p:get_content()
    if c then
      local s = tostring(c)
      if #s > MAX_SCAN then
        s = s:sub(1, MAX_SCAN)
      end
      if contains_any(s:lower(), UNSUB_WORDS) then
        return true
      end
    end
  end
  return false
end

-- RFC 8058: List-Unsubscribe with an https link plus
-- "List-Unsubscribe-Post: List-Unsubscribe=One-Click".
local function has_one_click(task)
  local lu = header(task, 'List-Unsubscribe')
  local post = header(task, 'List-Unsubscribe-Post')
  if not (lu and post) then
    return false
  end
  return lu:lower():find('<https://', 1, true) ~= nil
      and post:lower():gsub('%s', ''):find('list-unsubscribe=one-click', 1, true) ~= nil
end

-- A valid DKIM signature of the From domain (same organisational domain).
local function has_aligned_dkim(task)
  local from = task:get_from('mime')
  if not (from and from[1] and from[1].domain and from[1].domain ~= '') then
    return false
  end
  local from_org = rspamd_util.get_tld(from[1].domain:lower())
  for _, r in ipairs(task:get_dkim_results() or {}) do
    if r.result == 'allow' and r.domain and rspamd_util.get_tld(r.domain:lower()) == from_org then
      return true
    end
  end
  return false
end

local function tag_message(task, want_header, signals, want_subject)
  local add, remove = {}, {}
  if want_header then
    add['X-Mailfilter-Newsletter'] = 'yes (' .. table.concat(signals, ', ') .. ')'
  end
  if want_subject then
    local tag = tostring(option('newsletter_subject', 'tag', '[Newsletter]'))
    local subject = task:get_subject() or ''
    if tag ~= '' and not subject:find(tag, 1, true) then
      local tagged = subject ~= '' and (tag .. ' ' .. subject) or tag
      add['Subject'] = rspamd_util.fold_header('Subject', rspamd_util.mime_header_encode(tagged))
      remove['Subject'] = 1
    end
  end
  if next(add) then
    lua_mime.modify_headers(task, { add = add, remove = remove })
  end
end

local function check(task)
  local signals = newsletter_signals(task)
  if #signals == 0 then
    return
  end
  task:insert_result('MF_NEWSLETTER', 1.0, signals)

  local outbound = is_outbound(task)
  local domains = message_domains(task, outbound)
  local has_header_unsub = header(task, 'List-Unsubscribe') ~= nil

  if not has_header_unsub and rule_on('newsletter_no_unsubscribe', domains)
      and (not outbound or option('newsletter_no_unsubscribe', 'outbound', false) == true)
      and not body_has_unsubscribe(task) then
    task:insert_result('MF_NEWSLETTER_NO_UNSUB', 1.0)
  end

  if outbound then
    return -- the rules below are about mail coming in
  end

  if has_header_unsub and rule_on('newsletter_no_one_click', domains) and not has_one_click(task) then
    task:insert_result('MF_NEWSLETTER_NO_ONECLICK', 1.0)
  end
  if rule_on('newsletter_no_dkim', domains) and not has_aligned_dkim(task) then
    task:insert_result('MF_NEWSLETTER_NO_DKIM', 1.0)
  end
  tag_message(task, rule_on('newsletter_header', domains), signals, rule_on('newsletter_subject', domains))
end

local function score_of(id, default)
  local s = tonumber(option(id, 'score', default)) or default
  if s < 0 then
    s = 0
  elseif s > 20 then
    s = 20
  end
  return s
end

local id = rspamd_config:register_symbol({
  name = 'MF_NEWSLETTER',
  type = 'normal',
  callback = check,
  score = 0.0,
  group = GROUP,
  description = 'Newsletter or bulk mail recognised',
})
rspamd_config:register_symbol({
  name = 'MF_NEWSLETTER_NO_UNSUB', type = 'virtual', parent = id, group = GROUP,
  score = score_of('newsletter_no_unsubscribe', 4.0),
  description = 'Newsletter without a way to unsubscribe',
})
rspamd_config:register_symbol({
  name = 'MF_NEWSLETTER_NO_ONECLICK', type = 'virtual', parent = id, group = GROUP,
  score = score_of('newsletter_no_one_click', 1.0),
  description = 'Newsletter without one-click unsubscribe (RFC 8058)',
})
rspamd_config:register_symbol({
  name = 'MF_NEWSLETTER_NO_DKIM', type = 'virtual', parent = id, group = GROUP,
  score = score_of('newsletter_no_dkim', 1.5),
  description = "Newsletter not DKIM-signed by the sender's domain",
})
-- DKIM results must be known before the newsletter rules run.
rspamd_config:register_dependency('MF_NEWSLETTER', 'DKIM_CHECK')
