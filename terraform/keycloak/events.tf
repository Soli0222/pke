# 管理イベントの representation は資格情報を含み得るため保存しない。
# 管理イベントの保持期間は各 realm の adminEventsExpiration で設定する。
resource "keycloak_realm_events" "audit" {
  for_each = {
    master = keycloak_realm.master.id
    pke    = keycloak_realm.pke.id
  }

  realm_id                     = each.value
  events_enabled               = true
  events_expiration            = 2592000
  admin_events_enabled         = true
  admin_events_details_enabled = false
  events_listeners             = ["jboss-logging"]
}
