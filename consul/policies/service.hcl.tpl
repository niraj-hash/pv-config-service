# Least privilege for one service, __SERVICE__ (rendered by scripts/bootstrap-acl.sh):
# read its own configuration and the shared defaults, register itself, discover the others.
key_prefix "config/application/" {
  policy = "read"
}
key_prefix "config/application," {
  policy = "read"
}
key_prefix "config/__SERVICE__/" {
  policy = "read"
}
key_prefix "config/__SERVICE__," {
  policy = "read"
}
service "__SERVICE__" {
  policy = "write"
}
service_prefix "" {
  policy = "read"
}
node_prefix "" {
  policy = "read"
}
agent_prefix "" {
  policy = "read"
}
