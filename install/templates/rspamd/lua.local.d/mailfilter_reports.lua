--[[
Managed by Mailfilter. Spam reports from end users.

People forward spam to spam@{{HOSTNAME}} and good mail that was marked as spam
to notspam@{{HOSTNAME}} (or Outlook's Report button sends it there). That mail
contains the spam or virus itself, so it must not be refused or quarantined on
the way in: it is accepted without filtering and handed to the report handler
(Postfix service mfreport, mailfilter-spam-report), which checks who sent it.

Only when EVERY recipient is a report address. A message that also goes to a
real mailbox is filtered as usual, so nobody can sneak spam past the filter by
adding a report address as an extra recipient.
]]

local N = 'mailfilter_reports'
local REPORT = {
  ['spam@{{HOSTNAME}}'] = true,
  ['notspam@{{HOSTNAME}}'] = true,
}

rspamd_config:register_symbol({
  name = 'MF_SPAM_REPORT',
  type = 'prefilter',
  -- Before the allow/block list rules and everything else.
  priority = 10,
  score = 0.0,
  group = 'mailfilter',
  description = 'Spam report for Mailfilter (accepted without filtering)',
  callback = function(task)
    local rcpts = task:get_recipients('smtp')
    if not rcpts or #rcpts == 0 then
      return false
    end
    for _, r in ipairs(rcpts) do
      local addr = string.lower(r.addr or '')
      if not REPORT[addr] then
        return false
      end
    end
    task:insert_result('MF_SPAM_REPORT', 1.0)
    task:set_pre_result('no action', 'Spam report for Mailfilter', N, nil, 100)
    return true
  end,
})
