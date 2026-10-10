# 承認済みの削除対象だけを、通常の repository の削除防止から分離する。
moved {
  from = github_repository.repositories["private"]
  to   = github_repository.private
}

removed {
  from = github_repository.private

  lifecycle {
    destroy = true
  }
}
