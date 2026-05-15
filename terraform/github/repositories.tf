resource "github_repository" "repositories" {
  for_each = local.repositories

  name                        = each.key
  allow_auto_merge            = each.value.allow_auto_merge
  allow_forking               = each.value.allow_forking
  allow_merge_commit          = each.value.allow_merge_commit
  allow_rebase_merge          = each.value.allow_rebase_merge
  allow_squash_merge          = each.value.allow_squash_merge
  allow_update_branch         = each.value.allow_update_branch
  archived                    = each.value.archived
  delete_branch_on_merge      = each.value.delete_branch_on_merge
  description                 = each.value.description
  has_discussions             = each.value.has_discussions
  has_issues                  = each.value.has_issues
  has_projects                = each.value.has_projects
  has_wiki                    = each.value.has_wiki
  homepage_url                = each.value.homepage_url
  is_template                 = each.value.is_template
  merge_commit_message        = each.value.merge_commit_message
  merge_commit_title          = each.value.merge_commit_title
  squash_merge_commit_message = each.value.squash_merge_commit_message
  squash_merge_commit_title   = each.value.squash_merge_commit_title
  topics                      = each.value.topics
  visibility                  = each.value.visibility
  web_commit_signoff_required = each.value.web_commit_signoff_required

  dynamic "security_and_analysis" {
    for_each = try(each.value.security_and_analysis, null) == null ? [] : [each.value.security_and_analysis]

    content {
      dynamic "advanced_security" {
        for_each = try(security_and_analysis.value.advanced_security, null) == null ? [] : [security_and_analysis.value.advanced_security]

        content {
          status = advanced_security.value
        }
      }

      dynamic "code_security" {
        for_each = try(security_and_analysis.value.code_security, null) == null ? [] : [security_and_analysis.value.code_security]

        content {
          status = code_security.value
        }
      }

      dynamic "secret_scanning" {
        for_each = try(security_and_analysis.value.secret_scanning, null) == null ? [] : [security_and_analysis.value.secret_scanning]

        content {
          status = secret_scanning.value
        }
      }

      dynamic "secret_scanning_ai_detection" {
        for_each = try(security_and_analysis.value.secret_scanning_ai_detection, null) == null ? [] : [security_and_analysis.value.secret_scanning_ai_detection]

        content {
          status = secret_scanning_ai_detection.value
        }
      }

      dynamic "secret_scanning_non_provider_patterns" {
        for_each = try(security_and_analysis.value.secret_scanning_non_provider_patterns, null) == null ? [] : [security_and_analysis.value.secret_scanning_non_provider_patterns]

        content {
          status = secret_scanning_non_provider_patterns.value
        }
      }

      dynamic "secret_scanning_push_protection" {
        for_each = try(security_and_analysis.value.secret_scanning_push_protection, null) == null ? [] : [security_and_analysis.value.secret_scanning_push_protection]

        content {
          status = secret_scanning_push_protection.value
        }
      }
    }
  }

  lifecycle {
    prevent_destroy = true

    ignore_changes = [
      auto_init,
      gitignore_template,
      has_downloads,
      ignore_vulnerability_alerts_during_read,
      license_template,
      vulnerability_alerts,
    ]
  }
}

resource "github_branch_default" "repositories" {
  for_each = local.repositories

  repository = github_repository.repositories[each.key].name
  branch     = each.value.default_branch
}
