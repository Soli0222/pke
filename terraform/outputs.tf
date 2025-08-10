output "kkg_cluster_info" {
  description = "Information about the KKG Kubernetes cluster"
  value = module.kkg.vm_info
}
