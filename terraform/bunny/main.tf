# YAMLファイルから設定を読み込み
locals {
  pullzones_config = yamldecode(file(var.pullzones_config_file))
  
  # ホスト名のリストを平坦化（for_eachで使用するため）
  hostnames = flatten([
    for pullzone in local.pullzones_config.pullzones : [
      for hostname in pullzone.hostnames : {
        pullzone_key  = pullzone.name
        hostname_name = hostname.name
        tls_enabled   = hostname.tls_enabled
        force_ssl     = hostname.force_ssl
      }
    ]
  ])
  
  # ホスト名のマップ（リソース名として使用）
  hostnames_map = {
    for hostname in local.hostnames :
    "${hostname.pullzone_key}_${replace(hostname.hostname_name, ".", "_")}" => hostname
  }
}

# Pullzoneリソースの動的作成
resource "bunnynet_pullzone" "pullzones" {
  for_each = { for pz in local.pullzones_config.pullzones : pz.name => pz }
  
  name = each.value.name

  origin {
    type        = "OriginUrl"
    url         = each.value.origin_url
    host_header = each.value.host_header
  }

  routing {
    tier = each.value.tier
  }
}

# Hostnameリソースの動的作成
resource "bunnynet_pullzone_hostname" "hostnames" {
  for_each = local.hostnames_map
  
  pullzone    = bunnynet_pullzone.pullzones[each.value.pullzone_key].id
  name        = each.value.hostname_name
  tls_enabled = each.value.tls_enabled
  force_ssl   = each.value.force_ssl
}

