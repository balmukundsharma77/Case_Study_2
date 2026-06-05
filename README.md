# Automated S3 Lifecycle Enforcement

## Purpose
Nightly AWS-native automation that audits S3 buckets and applies a standard lifecycle policy to buckets that are larger than 100GB, have no active lifecycle policy, and are not tagged `lifecycle-exempt=true`.

## Runtime Behavior
This project implements a fully serverless AWS-native automation that:

* Audits every S3 bucket in the account
* Detects buckets missing lifecycle policies
* Skips exempt buckets using tags
* Uses CloudWatch storage metrics to identify large buckets
* Automatically applies a standard lifecycle policy
* Supports DRY_RUN mode for safe testing
* Logs all actions with US/Eastern timestamps
* Uses least-privilege IAM permissions
* Supports future fan-out redesign for enterprise scale

---

# Business Problem

Developers frequently create S3 buckets and never implement lifecycle management.

Consequences:

* Rising AWS storage costs
* Cold data remains in Standard storage
* No governance enforcement
* Manual auditing becomes operationally expensive

Goal:

Automatically enforce lifecycle policies on large unmanaged buckets without requiring human intervention.

---

# Lifecycle Policy Applied

Eligible buckets receive:

| Age       | Storage Class              |
| --------- | -------------------------- |
| 0-30 Days | Standard                   |
| 30+ Days  | Standard-IA                |
| 180+ Days | Glacier Flexible Retrieval |

---

# Solution Components

## EventBridge Scheduler

Runs nightly:

```hcl
schedule_expression = "cron(0 2 * * ? *)"
schedule_expression_timezone = "America/New_York"
```

Why?

* Handles Daylight Saving Time automatically
* No hardcoded UTC offsets
* Meets business requirement exactly

---

## AWS Lambda

The Lambda performs:

### Bucket Discovery

```python
s3.list_buckets()
```

### Lifecycle Detection

```python
s3.get_bucket_lifecycle_configuration()
```

### Exemption Validation

```python
s3.get_bucket_tagging()
```

### CloudWatch Metric Query

```python
cloudwatch.get_metric_statistics()
```

### Remediation

```python
s3.put_bucket_lifecycle_configuration()
```

---

# Decision Tree

For every bucket:

```text
Has Lifecycle Policy?
│
├── YES → Skip
│
└── NO
     │
     ├── lifecycle-exempt=true ?
     │
     ├── YES → Skip
     │
     └── NO
          │
          ├── BucketSizeBytes Available?
          │
          ├── NO → Skip
          │
          └── YES
                │
                ├── >100 GB ?
                │
                ├── NO → Skip
                │
                └── YES
                       │
                       └── Apply Lifecycle Policy
```

---

# DRY_RUN Safety Mode

Default:

```bash
DRY_RUN=true
```

Behavior:

```text
No changes made
Logs intended actions
Safe validation mode
```

Enable remediation:

```bash
terraform apply -var="dry_run=false"
```

---

# Why CloudWatch Instead of Listing Objects?

Many candidates attempt:

```python
list_objects_v2()
```

Problems:

* Slow
* Expensive
* Millions of API calls

Instead:

```python
BucketSizeBytes
```

Benefits:

* Already aggregated by AWS
* Low API cost
* Fast execution
* Scales significantly better

This is a key FinOps optimization.

---

# IAM Least Privilege

The Lambda is restricted to:

```text
s3:ListAllMyBuckets
s3:GetLifecycleConfiguration
s3:GetBucketTagging
s3:PutLifecycleConfiguration
cloudwatch:GetMetricStatistics
logs:CreateLogGroup
logs:CreateLogStream
logs:PutLogEvents
```

The Lambda cannot:

```text
Read objects
Delete objects
Delete buckets
Modify IAM
```

---

# Error Handling

Production safeguards include:

* Adaptive retry mode
* AWS API exception handling
* Timeout handling
* Per-bucket isolation
* Structured JSON logging

Example:

```python
except ClientError as exc:
```

One failing bucket does not stop the audit.

---

# Logging

All logs are structured JSON:

```json
{
  "timestamp_eastern": "2026-06-04T15:00:16-04:00",
  "bucket": "example-bucket",
  "action": "EXEMPT",
  "message": "Exempt — No Action Taken"
}
```

Benefits:

* CloudWatch Logs Insights compatible
* Auditable
* Searchable

---

# Terraform Deployment

Initialize:

```bash
terraform init
```

Plan:

```bash
terraform plan
```

Deploy:

```bash
terraform apply
```

---

# Testing Scenarios

## Bucket With Existing Lifecycle Policy

Expected:

```text
SKIPPED
```

---

## Exempt Bucket

Tag:

```bash
aws s3api put-bucket-tagging \
--bucket example-bucket \
--tagging 'TagSet=[{Key=lifecycle-exempt,Value=true}]'
```

Expected:

```text
EXEMPT
```

---

## Small Bucket (<100 GB)

Expected:

```text
SKIPPED_BELOW_THRESHOLD
```

---

## Large Bucket (>100 GB)

Expected:

```text
DRY_RUN
```

or

```text
REMEDIATED
```

---

### Enterprise-Scale Fan-Out Design

To support large-scale environments, I would redesign the solution using Amazon SQS and parallel worker Lambdas.

```text
EventBridge Scheduler
        ↓
Orchestrator Lambda
(List All Buckets)
        ↓
Amazon SQS Queue
(1 Message Per Bucket)
        ↓
Worker Lambda Fleet
(Parallel Processing)
        ↓
S3 + CloudWatch APIs
        ↓
DLQ + Audit Logs
```

If serial iteration approaches Lambda's 15-minute timeout, redesign as a fan-out workflow:

1. Scheduler triggers an orchestrator Lambda or Step Functions state machine.
2. Orchestrator lists buckets and writes one work item per bucket to SQS.
3. Worker Lambda consumes SQS messages in parallel.
4. Each worker evaluates one bucket: lifecycle status, exemption tag, CloudWatch metric, remediation.
5. Use reserved concurrency to control API pressure.
6. Failed messages go to a DLQ for replay.
7. Aggregate results in DynamoDB or CloudWatch Embedded Metric Format.

### How It Works

**Step 1 – Orchestrator Lambda**

* Triggered nightly by EventBridge.
* Discovers all S3 buckets.
* Creates one SQS message per bucket.

**Step 2 – SQS Queue**

* Acts as a durable buffer.
* Decouples discovery from processing.
* Supports retries and failure handling.

**Step 3 – Worker Lambda Fleet**

Each worker processes a single bucket:

1. Check lifecycle policy.
2. Check exemption tag.
3. Query BucketSizeBytes metric.
4. Apply lifecycle policy if eligible.

Since buckets are processed independently, AWS can automatically scale out multiple Lambda workers in parallel.

---

### Benefits

| Current Design            | Fan-Out Design              |
| ------------------------- | --------------------------- |
| Sequential processing     | Parallel processing         |
| Risk of 15-minute timeout | No timeout bottleneck       |
| Single point of failure   | Failure isolated per bucket |
| Limited scalability       | Horizontally scalable       |
| Manual retry handling     | Native SQS retry + DLQ      |

---


# FinOps Benefits

Storage cost reduction:

```text
Standard
      ↓
Standard-IA
      ↓
Glacier Flexible Retrieval
```

Expected outcomes:

* Reduced storage spend
* Automated governance
* Reduced operational overhead
* Improved compliance

---



This keeps each unit of work small, retryable, and horizontally scalable.
