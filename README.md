# Automated S3 Lifecycle Enforcement

## Purpose
Nightly AWS-native automation that audits S3 buckets and applies a standard lifecycle policy to buckets that are larger than 100GB, have no active lifecycle policy, and are not tagged `lifecycle-exempt=true`.

## Runtime Behavior
1. Enumerates all S3 buckets in the account.
2. Skips buckets with an active lifecycle policy.
3. Checks `lifecycle-exempt=true` bucket tag.
4. Reads CloudWatch `AWS/S3` `BucketSizeBytes` daily storage metric.
5. If the bucket is over 100GB, applies:
   - Standard-IA after 30 days
   - S3 Glacier Flexible Retrieval after 180 days
6. Logs all decisions with US/Eastern timestamps.

## DRY_RUN Mode
Default is safe mode:

```bash
DRY_RUN=true
```

When `DRY_RUN=true`, the Lambda logs intended changes but does not call `PutBucketLifecycleConfiguration`.

## Deploy

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

To enable remediation:

```bash
terraform apply -var='dry_run=false'
```

## Test

Invoke the Lambda manually first with `DRY_RUN=true`, then inspect CloudWatch Logs:

```bash
aws lambda invoke --function-name s3-lifecycle-enforcer output.json
cat output.json
```

Create an exempt bucket for testing:

```bash
aws s3api put-bucket-tagging \
  --bucket example-bucket \
  --tagging 'TagSet=[{Key=lifecycle-exempt,Value=true}]'
```

## Scale-Out Redesign
If serial iteration approaches Lambda's 15-minute timeout, redesign as a fan-out workflow:

1. Scheduler triggers an orchestrator Lambda or Step Functions state machine.
2. Orchestrator lists buckets and writes one work item per bucket to SQS.
3. Worker Lambda consumes SQS messages in parallel.
4. Each worker evaluates one bucket: lifecycle status, exemption tag, CloudWatch metric, remediation.
5. Use reserved concurrency to control API pressure.
6. Failed messages go to a DLQ for replay.
7. Aggregate results in DynamoDB or CloudWatch Embedded Metric Format.

This keeps each unit of work small, retryable, and horizontally scalable.
