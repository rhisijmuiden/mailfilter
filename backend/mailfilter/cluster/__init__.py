"""Cluster: several Mailfilter servers that keep the same configuration.

state.py     this node, the shared secret, sealed messages, revisions
snapshot.py  what is synced and how it is applied
tracking.py  notices configuration changes and bumps the revision
peer.py      talking to the other nodes (join, leave, sync)
"""
