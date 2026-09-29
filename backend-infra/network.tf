# Two public subnets and no NAT gateway, on purpose.
#
# The scanner needs outbound internet (the sites it tests, the Claude API, AWS
# APIs). A NAT gateway would provide that for a private task at roughly 35 USD a
# month before traffic. Instead the task gets a public IP, and the task's
# security group accepts inbound traffic only from the load balancer. Nothing
# can connect to the task directly, which is the property the NAT would buy.

resource "aws_vpc" "this" {
  cidr_block           = local.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(local.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false # the ECS service assigns public IPs explicitly

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "HTTPS from Cloudflare edge ranges only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS from Cloudflare"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = data.cloudflare_ip_ranges.this.ipv4_cidr_blocks
  }

  egress {
    description = "To tasks in the VPC"
    from_port   = local.api_port
    to_port     = local.api_port
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }
}

resource "aws_security_group" "task" {
  name        = "${local.name}-task"
  description = "API port from the load balancer only; outbound to the internet"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "API from the ALB"
    from_port       = local.api_port
    to_port         = local.api_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Sites under test, Claude API, AWS APIs"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
