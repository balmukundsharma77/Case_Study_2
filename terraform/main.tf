terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../main.py"
  output_path = "${path.module}/lambda.zip"
}

resource "aws_iam_role" "lambda_role" {
  name = "s3-lifecycle-enforcer-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "lambda_policy" {
  name = "s3-lifecycle-enforcer-least-privilege"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteLambdaLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Sid    = "ReadS3BucketInventoryLifecycleAndTags"
        Effect = "Allow"
        Action = [
          "s3:ListAllMyBuckets",
          "s3:GetBucketLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
          "s3:GetBucketTagging"
        ]
        Resource = "*"
      },
      {
        Sid    = "ApplyLifecycleOnly"
        Effect = "Allow"
        Action = ["s3:PutLifecycleConfiguration"]
        Resource = "arn:aws:s3:::*"
      },
      {
        Sid    = "ReadS3CloudWatchStorageMetrics"
        Effect = "Allow"
        Action = ["cloudwatch:GetMetricStatistics"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

resource "aws_lambda_function" "enforcer" {
  function_name    = "s3-lifecycle-enforcer"
  role             = aws_iam_role.lambda_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 900
  memory_size      = 512

  environment {
    variables = {
      DRY_RUN              = var.dry_run
      SIZE_THRESHOLD_BYTES = tostring(var.size_threshold_bytes)
      METRIC_LOOKBACK_DAYS = "3"
    }
  }
}

resource "aws_scheduler_schedule" "nightly" {
  name                         = "s3-lifecycle-enforcer-nightly-2am-eastern"
  schedule_expression          = "cron(0 2 * * ? *)"
  schedule_expression_timezone = "America/New_York"
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.enforcer.arn
    role_arn = aws_iam_role.scheduler_role.arn
  }
}

resource "aws_iam_role" "scheduler_role" {
  name = "s3-lifecycle-enforcer-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "scheduler_policy" {
  name = "s3-lifecycle-enforcer-scheduler-invoke"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.enforcer.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_attach" {
  role       = aws_iam_role.scheduler_role.name
  policy_arn = aws_iam_policy.scheduler_policy.arn
}
