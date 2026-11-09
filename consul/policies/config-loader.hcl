# The consul-config job (deploy/consul-config) that publishes this repository's config/ tree into Consul KV.
# It writes configuration and nothing else: no services, no ACLs.
key_prefix "config/" {
  policy = "write"
}
