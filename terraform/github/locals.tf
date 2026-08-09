locals {
  github_config = yamldecode(file("${path.module}/repositories.yaml"))

  repository_defaults = try(local.github_config.global.repository, {})

  repositories = {
    for name, repository in local.github_config.repositories : name => merge(
      local.repository_defaults,
      repository,
      {
        security_and_analysis = (
          try(repository.visibility, local.repository_defaults.visibility) == "private" ? null : merge(
            try(local.repository_defaults.security_and_analysis, {}),
            try(repository.security_and_analysis, {}),
          )
        )
      },
    )
  }

  active_repositories = {
    for name, repository in local.repositories : name => repository
    if !repository.archived
  }

  archived_repositories = {
    for name, repository in local.repositories : name => repository
    if repository.archived
  }

  global_actions_secret_specs = try(local.github_config.global.actions_secrets, {})

  repository_actions_secret_specs = merge(flatten([
    for repository in keys(local.active_repositories) : [
      {
        for secret_name, secret in merge(
          local.global_actions_secret_specs,
          try(local.github_config.repositories[repository].actions_secrets, {}),
          ) : "${repository}/${secret_name}" => merge(secret, {
            onepassword_source_key = sha256(jsonencode(try(secret.onepassword, {})))
        })
      }
    ]
  ])...)

  onepassword_actions_secret_source_groups = {
    for secret in values(local.repository_actions_secret_specs) :
    secret.onepassword_source_key => secret.onepassword...
  }

  onepassword_actions_secret_sources = {
    for source_key, sources in local.onepassword_actions_secret_source_groups :
    source_key => sources[0]
  }

  repository_actions_secret_keys = toset(keys(local.repository_actions_secret_specs))
}
