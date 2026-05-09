terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = local.lz_primary_region
  default_tags {
    tags = local.lz_common_tags
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = local.lz_secondary_region
  default_tags {
    tags = local.lz_common_tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_caller_identity" "current_use1" {
  provider = aws.us_east_1
}
