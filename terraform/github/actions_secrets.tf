data "external" "onepassword_actions_secret" {
  for_each = local.repository_actions_secret_specs

  program = ["${path.module}/op-read-secret.rb"]

  query = merge(
    {
      repository  = each.value.onepassword.repository
      secret_name = each.value.onepassword.secret_name
    },
    {
      for key, value in try(each.value.onepassword, {}) : key => tostring(value)
      if value != null
    },
  )
}

resource "github_actions_secret" "repository" {
  for_each = local.repository_actions_secret_keys

  repository  = split("/", each.key)[0]
  secret_name = split("/", each.key)[1]
  value       = sensitive(data.external.onepassword_actions_secret[each.key].result.value)
}
