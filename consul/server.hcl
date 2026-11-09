# Consul server for pv-config-service: configuration (KV) and service discovery for every
# PayVizio service. Local/single-node shape; production runs three servers (see README).
datacenter = "pv-dc1"
data_dir   = "/consul/data"
log_level  = "INFO"

server           = true
bootstrap_expect = 1
ui_config {
  enabled = true
}

client_addr    = "0.0.0.0"
bind_addr      = "0.0.0.0"
advertise_addr = "127.0.0.1"

# Default deny: nothing can read or write without a token, and each service's token can read
# only its own configuration (policies/*.hcl, created by scripts/bootstrap-acl.sh).
acl {
  enabled                  = true
  default_policy           = "deny"
  enable_token_persistence = true
}

# Consul's own DNS answers <service>.service.consul for discovery from non-Spring clients.
ports {
  dns = 8600
}
