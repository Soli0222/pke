# Provider reads credentials from environment variables:
# PM_API_URL, PM_API_TOKEN_ID, PM_API_TOKEN_SECRET, PM_TLS_INSECURE
provider "proxmox" {
  pm_minimum_permission_check = false
}
