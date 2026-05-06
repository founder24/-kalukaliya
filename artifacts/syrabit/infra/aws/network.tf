# infra/aws/network.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# VPC baseline for the async worker tier. Sized for Lambda + occasional
# Fargate jobs, NOT for synchronous API traffic (the sync API tier lives
# on Azure Container Apps).
#
# Layout
# ──────
# • /16 VPC in the primary region (ap-south-1).
# • Two AZs (a, b) for HA — Lambda VPC ENIs and Fargate tasks must land
#   in private subnets across both.
# • One public subnet per AZ for the NAT gateway egress path.
# • One private subnet per AZ for Lambda / Fargate workloads.
# • Single NAT gateway in AZ-a (cost-tuned; the worker tier can tolerate
#   a brief egress hiccup if AZ-a NAT is degraded — DR fallback is the
#   secondary region, not a multi-NAT setup).
# • Baseline security groups:
#     - workers-egress: outbound 443 only, no inbound
#     - vpc-endpoints: 443 inbound from workers-egress only
# • VPC interface endpoints for the AWS APIs workers actually call,
#   so private-subnet Lambdas don't pay NAT egress for SQS/SES/Secrets.

resource "aws_vpc" "workers" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-workers-vpc"
  })
}

resource "aws_internet_gateway" "workers" {
  vpc_id = aws_vpc.workers.id

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-workers-igw"
  })
}

# ─── Subnets ─────────────────────────────────────────────────────────────────

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.workers.id
  cidr_block              = "10.40.0.0/20"
  availability_zone       = "${local.lz_primary_region}a"
  map_public_ip_on_launch = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-public-a"
    tier = "public"
  })
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.workers.id
  cidr_block              = "10.40.16.0/20"
  availability_zone       = "${local.lz_primary_region}b"
  map_public_ip_on_launch = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-public-b"
    tier = "public"
  })
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.workers.id
  cidr_block        = "10.40.32.0/20"
  availability_zone = "${local.lz_primary_region}a"

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-private-a"
    tier = "private"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.workers.id
  cidr_block        = "10.40.48.0/20"
  availability_zone = "${local.lz_primary_region}b"

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-private-b"
    tier = "private"
  })
}

# ─── NAT for private-subnet egress ───────────────────────────────────────────

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-nat-eip"
  })
}

resource "aws_nat_gateway" "workers" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public_a.id

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-nat"
  })

  depends_on = [aws_internet_gateway.workers]
}

# ─── Route tables ────────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.workers.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.workers.id
  }

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-public-rt"
  })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.workers.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.workers.id
  }

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-private-rt"
  })
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# ─── Baseline security groups ────────────────────────────────────────────────

resource "aws_security_group" "workers_egress" {
  name        = "${local.lz_project}-workers-egress"
  description = "Outbound 443 + DNS (53) to VPC resolver; used by Lambda/Fargate workers."
  vpc_id      = aws_vpc.workers.id

  egress {
    description = "HTTPS to AWS APIs and the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # DNS to the VPC resolver (.2 of the VPC CIDR). Without this, Lambda
  # ENIs in the private subnets cannot resolve the AWS service endpoints
  # they then call over 443 — including the interface VPC endpoints
  # below, which are reached via private DNS names.
  egress {
    description = "DNS (UDP) to VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [aws_vpc.workers.cidr_block]
  }

  egress {
    description = "DNS (TCP fallback) to VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.workers.cidr_block]
  }

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-workers-egress"
  })
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.lz_project}-vpc-endpoints"
  description = "Allows workers SG to reach VPC interface endpoints over 443."
  vpc_id      = aws_vpc.workers.id

  ingress {
    description     = "HTTPS from worker tasks"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.workers_egress.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-vpc-endpoints"
  })
}

# ─── VPC endpoints (avoid NAT charges for high-volume AWS API calls) ─────────

resource "aws_vpc_endpoint" "sqs" {
  vpc_id              = aws_vpc.workers.id
  service_name        = "com.amazonaws.${local.lz_primary_region}.sqs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-vpce-sqs"
  })
}

resource "aws_vpc_endpoint" "secrets_manager" {
  vpc_id              = aws_vpc.workers.id
  service_name        = "com.amazonaws.${local.lz_primary_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-vpce-secrets"
  })
}

resource "aws_vpc_endpoint" "ses" {
  vpc_id              = aws_vpc.workers.id
  service_name        = "com.amazonaws.${local.lz_primary_region}.email-smtp"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-vpce-ses"
  })
}

# Gateway endpoint for S3 (free, used for Lambda image layer pulls / logs).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.workers.id
  service_name      = "com.amazonaws.${local.lz_primary_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.lz_common_tags, {
    Name = "${local.lz_project}-vpce-s3"
  })
}

# ─── Outputs (consumed by downstream worker Terraform in Phase 4) ────────────

output "workers_vpc_id" {
  value       = aws_vpc.workers.id
  description = "VPC ID for the async worker tier."
}

output "workers_private_subnet_ids" {
  value       = [aws_subnet.private_a.id, aws_subnet.private_b.id]
  description = "Private subnet IDs to attach Lambda/Fargate workers to."
}

output "workers_security_group_id" {
  value       = aws_security_group.workers_egress.id
  description = "Default egress SG for worker Lambdas / Fargate tasks."
}
