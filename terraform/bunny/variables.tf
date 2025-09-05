variable "bunny_api_key" {
  description = "Bunny.net API key"
  type        = string
  sensitive   = true
}

variable "pullzones_config_file" {
  description = "Path to the pullzones configuration YAML file"
  type        = string
  default     = "pullzones.yaml"
}
