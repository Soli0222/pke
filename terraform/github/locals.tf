locals {
  github_config = yamldecode(file("${path.module}/repositories.yaml"))

  repository_defaults = try(local.github_config.global.repository, {})

  repositories = {
    for name, repository in local.github_config.repositories : name => merge(
      local.repository_defaults,
      repository,
      {
        security_and_analysis = merge(
          try(local.repository_defaults.security_and_analysis, {}),
          try(repository.security_and_analysis, {}),
        )
      },
    )
  }

  global_actions_secret_specs = try(local.github_config.global.actions_secrets, {})

  repository_actions_secret_specs = merge(flatten([
    for repository in keys(local.repositories) : [
      {
        for secret_name, secret in merge(
          local.global_actions_secret_specs,
          try(local.github_config.repositories[repository].actions_secrets, {}),
          ) : "${repository}/${secret_name}" => merge(secret, {
            onepassword = merge(
              try(secret.onepassword, {}),
              {
                repository  = repository
                secret_name = secret_name
              },
            )
        })
      }
    ]
  ])...)

  repository_actions_secret_keys = toset(keys(local.repository_actions_secret_specs))
}
