data "external" "onepassword_actions_secrets" {
  program = ["${path.module}/op-read-secret.rb"]

  query = {
    sources = jsonencode(local.onepassword_actions_secret_sources)
  }
}

resource "github_actions_secret" "repository" {
  for_each = local.repository_actions_secret_keys

  repository  = split("/", each.key)[0]
  secret_name = split("/", each.key)[1]
  value = sensitive(data.external.onepassword_actions_secrets.result[
    local.repository_actions_secret_specs[each.key].onepassword_source_key
  ])
}
