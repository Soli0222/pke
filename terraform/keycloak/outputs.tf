output "sui_issuer" {
  value = "${var.public_url}/realms/${keycloak_realm.sui.realm}"
}
output "sui_client_id" {
  value = keycloak_openid_client.sui_web.client_id
}
output "sui_subject" {
  value = keycloak_user.sui.id
}
# Kubernetes vault の sui-keycloak item の values.yaml フィールドへ転記する。
# 認証情報ではないが、環境ごとの subject を Git に固定しない。
output "sui_helm_values" {
  value = yamlencode({
    image = { tag = var.sui_image_tag }
    auth = {
      oidc = {
        allowedSubjects = [keycloak_user.sui.id]
      }
    }
  })
}
