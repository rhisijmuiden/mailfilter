#!/usr/bin/env bash
# The text on the console of a Mailfilter container (before login, /etc/issue, and after
# login, /etc/motd): where the web interface is and, until the setup wizard is done,
# the user name and password of the first login.
#
#   issue.sh [preparing|failed]
# Called by firstboot.sh, at the end of install.sh and by mailfilterctl setup finish.
set -uo pipefail
ETC=/etc/mailfilter
state=${1:-}
sv() { sed -n "s/^$1=//p" "$ETC/mailfilter.env" 2>/dev/null | tail -1; }

{
  echo
  echo "  RHIS Mailfilter  ($(head -1 "$ETC/version" 2>/dev/null || echo ?))"
  echo "  ------------------------------------------------------------"
  if [[ $state == preparing || -f $ETC/appliance-firstboot-pending && $state != failed ]]; then
    echo "  This server is being prepared. This takes a few minutes."
    echo "  Press Enter to refresh this screen."
  elif [[ $state == failed ]]; then
    echo "  Preparing this server failed. Log in as root and see"
    echo "  /var/log/mailfilter-install.log, then: systemctl restart mailfilter-firstboot"
  else
    port=$(sv MF_WEB_PORT); mode=$(sv MF_WEB_MODE); v4=$(sv MF_PUBLIC_V4); v6=$(sv MF_PUBLIC_V6); url=$(sv MF_WEB_URL)
    scheme=https; [[ $mode == http || $mode == proxy ]] && scheme=http
    sfx=""
    if [[ $scheme == https && -n $port && $port != 443 ]] || [[ $scheme == http && -n $port && $port != 80 ]]; then sfx=":$port"; fi
    echo "  Web interface:"
    [[ -n $v4 ]] && echo "    $scheme://$v4$sfx/"
    [[ -n $v6 ]] && echo "    $scheme://[$v6]$sfx/"
    [[ -n $url && $url != *.localdomain* ]] && echo "    $url/"
    if [[ -s $ETC/initial-admin-password ]]; then
      echo
      echo "  Username:  $(cat "$ETC/initial-admin-login" 2>/dev/null || echo admin)"
      echo "  Password:  $(cat "$ETC/initial-admin-password")"
      echo
      echo "  Log in and follow the setup wizard."
    fi
  fi
  echo
} >/etc/motd.mailfilter.tmp

# agetty treats backslashes in /etc/issue as escapes; the text has none.
cp /etc/motd.mailfilter.tmp /etc/issue
mv /etc/motd.mailfilter.tmp /etc/motd
