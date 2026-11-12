--[[
Managed by Mailfilter (Configuration > Custom rules). Overwritten on every save.

Phishing protection and the attachment policy. Which rules are on, their
options and per-domain overrides come from /etc/rspamd/mailfilter_rules.json,
written by mailfilter-config together with the list of your own domains
("protected_domains"). The rule texts shown in the web interface live in
backend/mailfilter_config/rules_protection.py. ARC is configured in
local.d/arc.conf (Rspamd's own module).

Symbols:
  MF_LOOKALIKE_DOMAIN      sender domain looks like one of your domains
  MF_DISPLAYNAME_SPOOF     outside sender pretends to be someone of yours
  MF_SUSPICIOUS_LINK       link that hides where it goes
  MF_EXTERNAL_BANNER       "External sender" banner added (score 0)
  MF_ATTACHMENT_BLOCKED    dangerous file type (rejected or quarantined)
  MF_ATTACHMENT_MACRO      Office file with macros
  MF_ATTACHMENT_ENCRYPTED  password-protected archive
]]

local rspamd_logger = require "rspamd_logger"
local rspamd_util = require "rspamd_util"
local lua_mime = require "lua_mime"
local ucl = require "ucl"

local N = 'mailfilter_protection'
local SETTINGS = '/etc/rspamd/mailfilter_rules.json'
local GROUP = 'mailfilter'

local rules, protected_list = {}, {}
do
  local parser = ucl.parser()
  local ok, err = parser:parse_file(SETTINGS)
  if ok then
    local obj = parser:get_object() or {}
    rules = obj.rules or {}
    protected_list = obj.protected_domains or {}
  else
    rspamd_logger.infox(rspamd_config, '%s: cannot read %s (%s); protection rules are off', N, SETTINGS, err)
  end
end

local function option(id, name, default)
  local o = (rules[id] or {}).options
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

local function set_of(list)
  local s = {}
  for _, v in ipairs(list or {}) do
    if type(v) == 'string' then
      s[v:lower()] = true
    end
  end
  return s
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

---------------------------------------------------------------------------
-- Own domains and look-alikes
---------------------------------------------------------------------------

local function clean_host(h)
  if type(h) ~= 'string' then
    return nil
  end
  h = h:lower():gsub('%.$', '')
  if h == '' then
    return nil
  end
  return h
end

-- organisational domain, its first label and the rest ("rhis.nl" -> rhis, nl)
local function split_org(host)
  local org = rspamd_util.get_tld(host) or host
  local label, suffix = org:match('^([^.]+)%.(.+)$')
  return org, label or org, suffix or ''
end

local own = {}          -- your domains (Domains page)
local protected_hosts = {} -- your domains + extra names to protect
do
  local seen = {}
  local function add(h, is_own)
    h = clean_host(h)
    if not h then
      return
    end
    if is_own then
      own[h] = true
    end
    if not seen[h] then
      seen[h] = true
      protected_hosts[#protected_hosts + 1] = h
    end
  end
  for _, d in ipairs(protected_list) do
    add(d, true)
  end
  for _, d in ipairs(option('phishing_lookalike', 'extra', {}) or {}) do
    add(d, false)
  end
end

-- {host, org, label, suffix} per protected domain. Built on first use: the
-- public suffix list (get_tld) is not loaded yet while the config is read.
local protected_cache
local function protected()
  if not protected_cache then
    protected_cache = {}
    for _, h in ipairs(protected_hosts) do
      local org, label, suffix = split_org(h)
      protected_cache[#protected_cache + 1] = { host = h, org = org, label = label, suffix = suffix }
    end
  end
  return protected_cache
end

-- One of your own domains or a subdomain of one.
local function is_own(host)
  host = clean_host(host)
  if not host then
    return false
  end
  if own[host] then
    return true
  end
  for d in pairs(own) do
    if #host > #d and host:sub(-(#d + 1)) == '.' .. d then
      return true
    end
  end
  return false
end

local function is_protected(host)
  for _, p in ipairs(protected()) do
    if host == p.host or (#host > #p.host and host:sub(-(#p.host + 1)) == '.' .. p.host) or
        rspamd_util.get_tld(host) == p.org then
      return true
    end
  end
  return false
end

-- Characters that look alike in most fonts.
local function skeleton(s)
  s = s:gsub('rn', 'm'):gsub('vv', 'w'):gsub('cl', 'd'):gsub('%-', '')
  return (s:gsub('[01il|!53]', { ['0'] = 'o', ['1'] = 'l', ['i'] = 'l', ['|'] = 'l', ['!'] = 'l',
                                 ['5'] = 's', ['3'] = 'e' }))
end

-- Reason and the domain it imitates, or nil.
local function lookalike(host, other_tld)
  host = clean_host(host)
  if not host or #protected_hosts == 0 or is_protected(host) then
    return nil
  end
  local _, label, _ = split_org(host)
  for _, p in ipairs(protected()) do
    if label == p.label then
      if other_tld then
        return 'other-extension', p.host
      end
    elseif #p.label >= 4 then
      if skeleton(label) == skeleton(p.label) then
        return 'look-alike-characters', p.host
      end
      local max = #p.label >= 9 and 2 or 1
      if math.abs(#label - #p.label) <= max and rspamd_util.levenshtein_distance(label, p.label) <= max then
        return 'typo', p.host
      end
      if label:find(p.label .. '-', 1, true) == 1 or label:find('-' .. p.label, 1, true)
          or label == p.label .. p.suffix:gsub('%.', '') then
        return 'contains-name', p.host
      end
    end
    -- rhis.nl.login-secure.com
    if host:find(p.host .. '.', 1, true) then
      return 'contains-domain', p.host
    end
  end
  return nil
end

---------------------------------------------------------------------------
-- Phishing rules
---------------------------------------------------------------------------

local function from_mime(task)
  local f = task:get_from('mime')
  return f and f[1] or nil
end

local function reply_to_domain(task)
  local rt = task:get_header('Reply-To')
  if not rt then
    return nil
  end
  local addrs = rspamd_util.parse_mail_address(rt, task:get_mempool())
  if addrs and addrs[1] and addrs[1].domain then
    return addrs[1].domain
  end
  return nil
end

local function check_lookalike(task)
  local other_tld = option('phishing_lookalike', 'other_tld', true) == true
  local seen = {}
  local sources = {
    { 'from', (from_mime(task) or {}).domain },
    { 'envelope', ((task:get_from('smtp') or {})[1] or {}).domain },
    { 'reply-to', reply_to_domain(task) },
  }
  for _, s in ipairs(sources) do
    local host = clean_host(s[2])
    if host and not seen[host] then
      seen[host] = true
      local why, target = lookalike(host, other_tld)
      if why then
        task:insert_result('MF_LOOKALIKE_DOMAIN', 1.0, string.format('%s:%s~%s(%s)', s[1], host, target, why))
        return true
      end
    end
  end
  return false
end

local function norm_name(s)
  return (s or ''):lower():gsub('["\'<>]', ' '):gsub('%s+', ' '):gsub('^ ', ''):gsub(' $', '')
end

local function check_display_name(task)
  local f = from_mime(task)
  if not (f and f.name and f.name ~= '' and f.domain and f.domain ~= '') then
    return false
  end
  local from_dom = f.domain:lower()
  if is_own(from_dom) then
    return false -- your own domain: DMARC decides whether it is genuine
  end
  local name = norm_name(f.name)
  local from_org = rspamd_util.get_tld(from_dom)
  for dom in name:gmatch('[%w%.%-_+]+@([%w%-]+[%w%.%-]*%.%a%a+)') do
    if rspamd_util.get_tld(dom) ~= from_org then
      task:insert_result('MF_DISPLAYNAME_SPOOF', 1.0, 'address-in-name:' .. dom)
      return true
    end
  end
  for d in pairs(own) do
    if name:find(d, 1, true) then
      task:insert_result('MF_DISPLAYNAME_SPOOF', 1.0, 'own-domain-in-name:' .. d)
      return true
    end
  end
  for _, n in ipairs(option('phishing_display_name', 'names', {}) or {}) do
    local want = norm_name(n)
    if want ~= '' and (name == want or name:find(want, 1, true)) then
      task:insert_result('MF_DISPLAYNAME_SPOOF', 1.0, 'protected-name:' .. want)
      return true
    end
  end
  return false
end

local SHORTENERS = set_of({
  'bit.ly', 'bitly.com', 'tinyurl.com', 't.co', 'goo.gl', 'ow.ly', 'is.gd', 'buff.ly', 'rebrand.ly',
  'cutt.ly', 'shorturl.at', 'rb.gy', 't.ly', 'tiny.cc', 's.id', 'bl.ink', 'lnkd.in', 'v.gd', 'qrco.de',
  'shorturl.com', 'tr.im', 'x.co', 'soo.gd', 'clck.ru', 'u.to',
})

local function check_links(task)
  local shorteners = option('phishing_links', 'shorteners', false) == true
  local other_tld = option('phishing_lookalike', 'other_tld', true) == true
  local hits, n = {}, 0
  local function hit(s)
    if n < 5 then
      hits[#hits + 1] = s
    end
    n = n + 1
  end
  for _, u in ipairs(task:get_urls({ 'http', 'https' }) or {}) do
    local host = clean_host(u:get_host())
    if host then
      local flags = u:get_flags() or {}
      if flags.numeric then
        hit('ip-address:' .. host)
      elseif flags.has_user and (u:get_user() or ''):find('.', 1, true) then
        hit('user-part:' .. host) -- http://www.bank.nl@evil.example/
      elseif shorteners and SHORTENERS[host] then
        hit('shortener:' .. host)
      else
        local why, target = lookalike(host, other_tld)
        if why then
          hit(string.format('lookalike:%s~%s', host, target))
        elseif flags.phished and u.get_phished then
          -- the text shows one of your domains but the link goes elsewhere
          local shown = u:get_phished()
          local shown_host = shown and clean_host(shown:get_host())
          if shown_host and is_protected(shown_host) and not is_protected(host) then
            hit(string.format('shows:%s~goes:%s', shown_host, host))
          end
        end
      end
    end
  end
  if n > 0 then
    task:insert_result('MF_SUSPICIOUS_LINK', 1.0, hits)
    return true
  end
  return false
end

---------------------------------------------------------------------------
-- "External sender" banner
---------------------------------------------------------------------------

local TEXTS = {
  normal = {
    nl = 'Let op: dit bericht komt van buiten je organisatie. Klik niet op links en open geen bijlagen als je de afzender niet herkent.',
    en = 'Caution: this message comes from outside your organisation. Do not click links or open attachments unless you recognise the sender.',
  },
  warn = {
    nl = 'Waarschuwing: dit bericht van buiten je organisatie lijkt op phishing. Klik niet op links, open geen bijlagen en betaal niets zonder het eerst na te vragen.',
    en = 'Warning: this message from outside your organisation looks like phishing. Do not click links, open attachments or pay anything without checking first.',
  },
}

local function banner_text(suspicious)
  local custom = option('external_banner', 'text', '')
  if type(custom) == 'string' and custom ~= '' then
    return custom
  end
  local t = TEXTS[suspicious and 'warn' or 'normal']
  local lang = option('external_banner', 'language', 'both')
  if lang == 'nl' or lang == 'en' then
    return t[lang]
  end
  return t.nl .. ' / ' .. t.en
end

local function html_escape(s)
  return (s:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;'):gsub('"', '&quot;'))
end

local function html_banner(text, suspicious)
  local bg, border, fg = '#fff4e5', '#e8a33d', '#5a3b00'
  if suspicious then
    bg, border, fg = '#fdecec', '#d93025', '#7a1212'
  end
  return string.format('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" ' ..
      'style="border-collapse:collapse;margin:0 0 12px 0"><tr><td style="background:%s;border-left:4px solid %s;' ..
      'padding:8px 12px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.4;color:%s">%s</td>' ..
      '</tr></table>', bg, border, fg, html_escape(text))
end

local function newline(task)
  local t = task:get_newlines_type()
  if t == 'cr' then
    return '\r'
  elseif t == 'lf' then
    return '\n'
  end
  return '\r\n'
end

-- Signed or encrypted messages, and forwarded messages as attachment, are left alone.
local function can_rewrite(task)
  for _, p in ipairs(task:get_parts() or {}) do
    local t, st = p:get_type()
    t, st = (t or ''):lower(), (st or ''):lower()
    if (t == 'multipart' and (st == 'signed' or st == 'encrypted')) or
        (t == 'application' and (st == 'pkcs7-mime' or st == 'x-pkcs7-mime' or st == 'pgp-encrypted')) or
        p:is_message() then
      return false
    end
  end
  local tp = task:get_text_parts()
  return tp and #tp > 0
end

-- Adds the banner on top of every text and HTML part (the structure follows
-- lua_mime.add_text_footer) and hands Rspamd the new message.
local function add_banner(task, suspicious)
  local nl = newline(task)
  local text = banner_text(suspicious)
  local html = html_banner(text, suspicious)
  local out = {}
  local single_ct
  local boundaries = {}
  local cur_boundary
  local changed = false

  local function rewrite(part, is_multipart)
    local tp = part:get_text()
    local is_html = tp:is_html()
    local content = tp:get_content('raw_utf')
    local s = content and tostring(content) or ''
    if is_html then
      local _, b_end = s:find('<[bB][oO][dD][yY][^>]*>')
      if b_end then
        s = s:sub(1, b_end) .. html .. s:sub(b_end + 1)
      else
        s = html .. s
      end
    else
      s = text .. nl .. nl .. s
    end
    local ct = is_html and 'text/html' or 'text/plain'
    if is_multipart then
      out[#out + 1] = string.format('Content-Type: %s; charset=utf-8%sContent-Transfer-Encoding: quoted-printable', ct, nl)
      out[#out + 1] = ''
    else
      single_ct = ct
    end
    out[#out + 1] = { rspamd_util.encode_qp(s, 76, task:get_newlines_type()), true }
    out[#out + 1] = ''
    changed = true
  end

  for _, part in ipairs(task:get_parts()) do
    local boundary = part:get_boundary()
    if part:is_multipart() then
      if cur_boundary then
        out[#out + 1] = string.format('--%s', boundaries[#boundaries])
      end
      boundaries[#boundaries + 1] = boundary or '--XXX'
      cur_boundary = boundary
      local rh = part:get_raw_headers()
      if #rh > 0 then
        out[#out + 1] = { rh, true }
      end
    else
      local parent = part:get_parent()
      if boundary then
        if cur_boundary and boundary ~= cur_boundary then
          out[#out + 1] = string.format('--%s--%s', boundaries[#boundaries], nl)
          table.remove(boundaries)
          cur_boundary = boundary
        end
        out[#out + 1] = string.format('--%s', boundary)
      end
      if part:is_text() and not part:is_attachment() and part:get_text() then
        rewrite(part, parent and parent:is_multipart())
      else
        out[#out + 1] = { part:get_raw_headers(), true }
        out[#out + 1] = { part:get_raw_content(), false }
      end
    end
  end
  local b = table.remove(boundaries)
  while b do
    out[#out + 1] = string.format('--%s--', b)
    if #boundaries > 0 then
      out[#out + 1] = ''
    end
    b = table.remove(boundaries)
  end
  if not changed then
    return false
  end

  -- Original headers stay byte for byte (the milter only replaces the body).
  -- Everything up to and including the empty line after the headers.
  local whole = tostring(task:get_content())
  local eoh
  for _, sep in ipairs({ '\r\n\r\n', '\n\n' }) do
    local _, e = whole:find(sep, 1, true)
    if e and (not eoh or e < eoh) then
      eoh = e
    end
  end
  if not eoh then
    return false
  end
  local pieces = { whole:sub(1, eoh) }
  for _, o in ipairs(out) do
    if type(o) == 'string' then
      pieces[#pieces + 1] = o
      pieces[#pieces + 1] = nl
    else
      pieces[#pieces + 1] = o[1]
      if o[2] then
        pieces[#pieces + 1] = nl
      end
    end
  end
  task:set_message(pieces)

  local add = { ['X-Mailfilter-External'] = suspicious and 'yes; suspicious' or 'yes' }
  local remove = {}
  if single_ct then
    add['Content-Type'] = single_ct .. '; charset=utf-8'
    add['Content-Transfer-Encoding'] = 'quoted-printable'
    remove['Content-Type'] = 0
    remove['Content-Transfer-Encoding'] = 0
  end
  lua_mime.modify_headers(task, { add = add, remove = remove })
  return true
end

-- Decided before ARC sealing (which depends on this), so mail that gets a
-- banner is not sealed with a signature the banner would break.
local function banner_wanted(task, domains, suspicious)
  if not rule_on('external_banner', domains) then
    return false
  end
  local f = from_mime(task)
  if f and f.domain and f.domain ~= '' and is_own(f.domain) then
    return false
  end
  if option('external_banner', 'only_suspicious', false) == true and not suspicious then
    return false
  end
  return can_rewrite(task)
end

local function phishing_check(task)
  if is_outbound(task) then
    return
  end
  local domains = message_domains(task, false)
  local suspicious = false
  if rule_on('phishing_lookalike', domains) and check_lookalike(task) then
    suspicious = true
  end
  if rule_on('phishing_display_name', domains) and check_display_name(task) then
    suspicious = true
  end
  if rule_on('phishing_links', domains) and check_links(task) then
    suspicious = true
  end
  if banner_wanted(task, domains, suspicious) then
    task:cache_set('mf_banner', { suspicious = suspicious })
    task:disable_symbol('ARC_SIGNED')
  end
end

local function banner_postfilter(task)
  local want = task:cache_get('mf_banner')
  if not want then
    return
  end
  local action = task:get_metric_action()
  if action == 'reject' or action == 'soft reject' or action == 'discard' then
    return
  end
  local ok, res = pcall(add_banner, task, want.suspicious)
  if ok and res then
    task:insert_result('MF_EXTERNAL_BANNER', 1.0, want.suspicious and 'suspicious' or 'external')
  elseif not ok then
    rspamd_logger.errx(task, '%s: could not add the banner: %s', N, res)
  end
end

---------------------------------------------------------------------------
-- Attachment policy
---------------------------------------------------------------------------

local MACRO_EXT = set_of({ 'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'pptm', 'potm', 'ppam', 'ppsm', 'sldm' })
local OFFICE_EXT = set_of({ 'doc', 'dot', 'xls', 'xlt', 'xla', 'ppt', 'pps', 'pot', 'docx', 'dotx', 'xlsx',
                            'xltx', 'xlsb', 'pptx', 'ppsx', 'potx' })
-- Macro storage: vbaProject.bin in Office Open XML (zip names are plain text),
-- the _VBA_PROJECT stream in old OLE files (UTF-16 stream names).
local MACRO_MARKERS = { 'vbaProject.bin', 'xl/macrosheets/', '_\0V\0B\0A\0_\0P\0R\0O\0J\0E\0C\0T\0' }

local function extension(name)
  if type(name) ~= 'string' then
    return nil
  end
  name = name:gsub('%%(%x%x)', function(h)
    return string.char(tonumber(h, 16))
  end):gsub('[%s%.]+$', ''):lower()
  return name:match('%.([%w_-]+)$')
end

local function has_macros(part)
  local content = part:get_content()
  if not content then
    return false
  end
  for _, m in ipairs(MACRO_MARKERS) do
    if content:find(m) then
      return true
    end
  end
  return false
end

local function attachments_check(task)
  local outbound = is_outbound(task)
  local domains = message_domains(task, outbound)
  local block_on = rule_on('attachments_dangerous', domains)
      and (not outbound or option('attachments_dangerous', 'outbound', false) == true)
  if outbound and not block_on then
    return
  end
  local macro_on = not outbound and rule_on('attachments_macros', domains)
  local enc_on = not outbound and rule_on('attachments_encrypted', domains)
  if not (block_on or macro_on or enc_on) then
    return
  end
  local blocked_ext = set_of(option('attachments_dangerous', 'extensions', {}))
  local found = { blocked = {}, macro = {}, encrypted = {} }

  local function check_name(name, label)
    local ext = extension(name)
    if not ext then
      return
    end
    if block_on and blocked_ext[ext] then
      found.blocked[#found.blocked + 1] = label
    elseif macro_on and MACRO_EXT[ext] then
      found.macro[#found.macro + 1] = label
    end
  end

  for _, p in ipairs(task:get_parts() or {}) do
    if not p:is_multipart() and not p:is_message() then
      local fname = p:get_filename()
      local detected = p:get_detected_ext()
      local label = fname or ('part ' .. tostring(p:get_id()))
      if fname then
        check_name(fname, label)
      end
      if block_on and detected and blocked_ext[detected:lower()] and extension(fname) ~= detected:lower() then
        found.blocked[#found.blocked + 1] = string.format('%s (really .%s)', label, detected:lower())
      end
      local ext = extension(fname)
      if macro_on and ((ext and OFFICE_EXT[ext]) or (detected and OFFICE_EXT[detected:lower()]))
          and not MACRO_EXT[ext or ''] and has_macros(p) then
        found.macro[#found.macro + 1] = label
      end
      if p:is_archive() then
        local arch = p:get_archive()
        if arch then
          if enc_on and arch:is_encrypted() then
            found.encrypted[#found.encrypted + 1] = label
          end
          for _, f in ipairs(arch:get_files_full(1000) or {}) do
            if f.encrypted and enc_on and #found.encrypted == 0 then
              found.encrypted[#found.encrypted + 1] = label
            end
            if f.name then
              check_name(f.name, string.format('%s in %s', f.name, label))
            end
          end
        end
      end
    end
  end

  -- The strictest action wins.
  local actions = {}
  local function act(rule, sym, list, default_action, what)
    if #list == 0 then
      return
    end
    task:insert_result(sym, 1.0, list)
    local a = option(rule, 'action', default_action)
    actions[#actions + 1] = { a == 'reject' and 'reject' or 'quarantine',
                              string.format('%s: %s', what, table.concat(list, ', '):sub(1, 200)) }
  end
  act('attachments_dangerous', 'MF_ATTACHMENT_BLOCKED', found.blocked, 'reject', 'Attachment type not allowed')
  act('attachments_macros', 'MF_ATTACHMENT_MACRO', found.macro, 'quarantine', 'Office file with macros')
  act('attachments_encrypted', 'MF_ATTACHMENT_ENCRYPTED', found.encrypted, 'quarantine', 'Password-protected archive')
  if #actions == 0 then
    return
  end
  table.sort(actions, function(a, b)
    return a[1] == 'reject' and b[1] ~= 'reject'
  end)
  task:set_pre_result({ action = actions[1][1], message = actions[1][2], module = N })
end

---------------------------------------------------------------------------
-- Registration
---------------------------------------------------------------------------

local phish_id = rspamd_config:register_symbol({
  name = 'MF_PHISHING_CHECK',
  type = 'callback',
  callback = phishing_check,
  group = GROUP,
  description = 'Mailfilter phishing protection',
})
rspamd_config:register_symbol({
  name = 'MF_LOOKALIKE_DOMAIN', type = 'virtual', parent = phish_id, group = GROUP,
  score = score_of('phishing_lookalike', 7.0),
  description = 'Sender domain looks like one of your own domains',
})
rspamd_config:register_symbol({
  name = 'MF_DISPLAYNAME_SPOOF', type = 'virtual', parent = phish_id, group = GROUP,
  score = score_of('phishing_display_name', 5.0),
  description = 'Outside sender uses a name that pretends to be one of yours',
})
rspamd_config:register_symbol({
  name = 'MF_SUSPICIOUS_LINK', type = 'virtual', parent = phish_id, group = GROUP,
  score = score_of('phishing_links', 3.5),
  description = 'Link that hides where it really goes',
})
-- Authentication results first; ARC sealing waits for the banner decision.
rspamd_config:register_dependency('MF_PHISHING_CHECK', 'DMARC_CHECK')
rspamd_config:register_dependency('ARC_SIGNED', 'MF_PHISHING_CHECK')

rspamd_config:register_symbol({
  name = 'MF_EXTERNAL_BANNER',
  type = 'postfilter',
  callback = banner_postfilter,
  -- after the action is known (force_actions), before headers are written
  priority = 5,
  score = 0.0,
  group = GROUP,
  flags = 'ignore_passthrough',
  description = 'External sender banner added',
})

local att_id = rspamd_config:register_symbol({
  name = 'MF_ATTACHMENT_CHECK',
  type = 'callback',
  callback = attachments_check,
  group = GROUP,
  description = 'Mailfilter attachment policy',
})
for _, s in ipairs({
  { 'MF_ATTACHMENT_BLOCKED', 'Dangerous attachment type' },
  { 'MF_ATTACHMENT_MACRO', 'Office file with macros' },
  { 'MF_ATTACHMENT_ENCRYPTED', 'Password-protected archive' },
}) do
  rspamd_config:register_symbol({
    name = s[1], type = 'virtual', parent = att_id, group = GROUP, score = 0.0, description = s[2],
  })
end
