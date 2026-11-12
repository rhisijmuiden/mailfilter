--[[
Managed by Mailfilter (Configuration > Cloud mail). Overwritten on every save.

Microsoft 365 and Google Workspace tenants send their outgoing mail through
the Mailfilter from the providers' shared mail servers. Postfix already lets
those servers relay only with a sender of a linked domain
(/etc/mailfilter/postfix/cloud_relay.cidr). This check makes sure relayed mail
really comes from the linked tenant, so another customer of the same provider
cannot send through this Mailfilter in your domain's name:

  Microsoft 365     X-MS-Exchange-CrossTenant-Id must be the domain's tenant ID
  Google Workspace  the message needs a valid DKIM signature of the domain

Mail that fails is refused. Mail TO your own domains is never touched here.
Settings: /etc/rspamd/mailfilter_cloud.json (written by mailfilter-config).

Symbols:
  MF_CLOUD_TENANT_OK      relayed mail verified as coming from the linked tenant (score 0)
  MF_CLOUD_TENANT_FORGED  relayed mail from a provider server that is not the linked tenant (refused)
]]

local rspamd_logger = require "rspamd_logger"
local rspamd_ip = require "rspamd_ip"
local ucl = require "ucl"

local N = 'mailfilter_cloud'
local SETTINGS = '/etc/rspamd/mailfilter_cloud.json'

local settings = { networks = {}, m365 = {}, google = {}, local_domains = {} }
do
  local parser = ucl.parser()
  local ok, err = parser:parse_file(SETTINGS)
  if ok then
    settings = parser:get_object() or settings
  else
    rspamd_logger.infox(rspamd_config, '%s: cannot read %s (%s); cloud mail check is off', N, SETTINGS, err)
  end
end

-- networks[provider] = { {masked ip string, bits, version}, ... }
local networks = {}
for provider, list in pairs(settings.networks or {}) do
  networks[provider] = {}
  for _, cidr in ipairs(list) do
    local addr, bits = cidr:match('^([^/]+)/(%d+)$')
    local ip = addr and rspamd_ip.from_string(addr)
    if ip and ip:is_valid() then
      bits = tonumber(bits)
      table.insert(networks[provider], { ip:apply_mask(bits):to_string(), bits, ip:get_version() })
    end
  end
end

local local_domains = {}
for _, d in ipairs(settings.local_domains or {}) do
  local_domains[d:lower()] = true
end
local google = {}
for _, d in ipairs(settings.google or {}) do
  google[d:lower()] = true
end
local m365 = {}
local m365_ids = {}
for d, id in pairs(settings.m365 or {}) do
  m365[d:lower()] = id:lower()
  m365_ids[id:lower()] = true
end

local function provider_of(ip)
  if not (ip and ip:is_valid()) then
    return nil
  end
  local v = ip:get_version()
  for provider, list in pairs(networks) do
    for _, n in ipairs(list) do
      if n[3] == v and ip:apply_mask(n[2]):to_string() == n[1] then
        return provider
      end
    end
  end
  return nil
end

-- The value for `domain` or its closest parent in `tbl`.
local function lookup(tbl, domain)
  local d = domain
  while d and d ~= '' do
    if tbl[d] ~= nil then
      return tbl[d], d
    end
    d = d:match('^[^.]+%.(.+)$')
  end
  return nil
end

local function is_local(domain)
  return lookup(local_domains, (domain or ''):lower()) ~= nil
end

-- Relayed = at least one recipient is not one of our own domains.
local function relayed(task)
  for _, r in ipairs(task:get_recipients('smtp') or {}) do
    if not is_local(r.domain) then
      return true
    end
  end
  return false
end

local function dkim_domains(task)
  local out = {}
  local s = task:get_symbol('R_DKIM_ALLOW')
  if s and s[1] and s[1].options then
    for _, opt in ipairs(s[1].options) do
      local d = opt:match('^([^:]+)')
      if d then
        out[d:lower()] = true
      end
    end
  end
  return out
end

local function refuse(task, why)
  task:insert_result('MF_CLOUD_TENANT_FORGED', 1.0, why)
  task:set_pre_result({ action = 'reject', module = N,
    message = 'Mailfilter: this message does not come from the linked mail environment of the sender' })
end

local function check(task)
  local provider = provider_of(task:get_from_ip())
  if not provider or task:get_user() or not relayed(task) then
    return
  end
  local from = (task:get_from('smtp') or {})[1]
  local domain = ((from and from.domain) or ''):lower()

  if provider == 'm365' then
    local got = (task:get_header('X-MS-Exchange-CrossTenant-Id') or ''):lower():gsub('%s', '')
    if domain == '' then
      -- A bounce: it must come from one of the linked tenants.
      if m365_ids[got] then
        task:insert_result('MF_CLOUD_TENANT_OK', 1.0, 'm365:bounce')
      else
        refuse(task, 'm365:bounce:' .. got)
      end
      return
    end
    local want = lookup(m365, domain)
    if want and got == want then
      task:insert_result('MF_CLOUD_TENANT_OK', 1.0, 'm365:' .. domain)
    else
      refuse(task, 'm365:' .. domain .. ':' .. got)
    end
  elseif provider == 'google' then
    local signed = dkim_domains(task)
    if domain == '' then
      for d in pairs(signed) do
        if lookup(google, d) then
          task:insert_result('MF_CLOUD_TENANT_OK', 1.0, 'google:bounce')
          return
        end
      end
      refuse(task, 'google:bounce')
      return
    end
    local _, linked = lookup(google, domain)
    if linked and (lookup(signed, domain) or signed[linked]) then
      task:insert_result('MF_CLOUD_TENANT_OK', 1.0, 'google:' .. domain)
    else
      refuse(task, 'google:' .. domain .. ':no-dkim')
    end
  end
end

local id = rspamd_config:register_symbol({
  name = 'MF_CLOUD_TENANT_CHECK',
  type = 'callback',
  callback = check,
  group = 'mailfilter',
  flags = 'empty,nice',
})
rspamd_config:register_symbol({
  name = 'MF_CLOUD_TENANT_OK', type = 'virtual', parent = id, group = 'mailfilter', score = 0.0,
  description = 'Relayed mail verified as coming from the linked Microsoft 365 / Google Workspace tenant',
})
rspamd_config:register_symbol({
  name = 'MF_CLOUD_TENANT_FORGED', type = 'virtual', parent = id, group = 'mailfilter', score = 0.0,
  description = 'Relayed mail from a Microsoft/Google server that is not the linked tenant (refused)',
})
rspamd_config:register_dependency('MF_CLOUD_TENANT_CHECK', 'DKIM_CHECK')
