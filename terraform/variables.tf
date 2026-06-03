variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "dry_run" {
  type    = string
  default = "true"
}

variable "size_threshold_bytes" {
  type    = number
  default = 107374182400
}
