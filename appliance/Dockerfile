# Base system of the Proxmox LXC template: Debian 13 with systemd and what Proxmox expects
# of a Debian container (ifupdown for its network settings, SSH, a console login).
# appliance/build.sh boots this with systemd, runs the Mailfilter installer in it
# (install.sh --image), generalises it and exports the file system as .tar.zst.
FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus libpam-systemd ifupdown iproute2 iputils-ping \
      openssh-server ca-certificates curl less nano procps logrotate cron locales tzdata git \
 && (apt-get install -y --no-install-recommends isc-dhcp-client || apt-get install -y --no-install-recommends dhcpcd-base) \
 && sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/; s/^# *nl_NL.UTF-8/nl_NL.UTF-8/' /etc/locale.gen && locale-gen \
 && echo 'LANG=C.UTF-8' >/etc/default/locale \
 && rm -f /usr/sbin/policy-rc.d \
 && rm -rf /var/lib/apt/lists/*

# Git checkout of the branch this template follows (main or test), prepared by build.sh
COPY mailfilter /opt/mailfilter
RUN chown -R root:root /opt/mailfilter && chmod -R go-w /opt/mailfilter

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
