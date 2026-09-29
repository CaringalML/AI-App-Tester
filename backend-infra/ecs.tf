resource "aws_ecs_cluster" "this" {
  name = var.project_name

  setting {
    name  = "containerInsights"
    value = "disabled" # CloudWatch Logs is enough for one task; Insights bills per metric
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # matches the GitHub-hosted runner that builds the image
  }

  container_definitions = jsonencode([{
    name      = local.container
    image     = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{ containerPort = local.api_port, protocol = "tcp" }]

    environment = concat(
      [
        { name = "ANTHROPIC_MODEL", value = var.anthropic_model },
        { name = "AGENT_EFFORT", value = var.agent_effort },
        { name = "MAX_AGENT_STEPS", value = tostring(var.max_agent_steps) },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.scans.name },
        { name = "ARTIFACT_BUCKET", value = aws_s3_bucket.artifacts.bucket },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "CORS_ORIGINS", value = local.cors_origin },
        { name = "PUBLIC_BASE_URL", value = "https://${local.api_host}" },
        { name = "ALLOW_PRIVATE_TARGETS", value = "false" },
      ],
      var.anthropic_workspace_id == "" ? [] : [
        { name = "ANTHROPIC_WORKSPACE_ID", value = var.anthropic_workspace_id },
      ],
    )

    # Injected by ECS at start from Secrets Manager; never in the image or the task definition.
    secrets = [{ name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn }]

    # Reaps the Chromium child processes each scan leaves behind.
    linuxParameters = { initProcessEnabled = true }
    stopTimeout     = 30

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:${local.api_port}/healthz', timeout=3)\" || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = local.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Start the replacement before stopping the old task, and roll back
  # automatically if the new one never becomes healthy.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true # outbound internet without a NAT gateway; inbound is ALB-only
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = local.container
    container_port   = local.api_port
  }

  lifecycle {
    # CI registers a new revision per deploy (image pinned to the commit SHA).
    # Terraform still owns the task definition's shape: the deploy workflow
    # copies the latest revision Terraform registered and only swaps the image.
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.https]
}
