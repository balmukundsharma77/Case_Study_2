"""
Automated S3 Lifecycle Enforcement Lambda
- Audits all S3 buckets in the account
- Skips buckets with active lifecycle policy
- Skips buckets tagged lifecycle-exempt=true
- Uses CloudWatch BucketSizeBytes daily metric to identify buckets >100GB
- Applies two-tier lifecycle policy unless DRY_RUN=true
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError

EASTERN = ZoneInfo("America/New_York")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SIZE_THRESHOLD_BYTES = int(os.getenv("SIZE_THRESHOLD_BYTES", str(100 * 1024**3)))
METRIC_LOOKBACK_DAYS = int(os.getenv("METRIC_LOOKBACK_DAYS", "3"))

AWS_CONFIG = Config(
    retries={"max_attempts": 8, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=20,
)

s3 = boto3.client("s3", config=AWS_CONFIG)
cloudwatch = boto3.client("cloudwatch", config=AWS_CONFIG)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def eastern_now_iso() -> str:
    return datetime.now(EASTERN).isoformat(timespec="seconds")


def log_event(level: str, bucket: str, action: str, message: str, **kwargs) -> None:
    payload = {
        "timestamp_eastern": eastern_now_iso(),
        "bucket": bucket,
        "action": action,
        "message": message,
        **kwargs,
    }
    getattr(logger, level.lower())(json.dumps(payload, default=str))


def bucket_has_active_lifecycle(bucket: str) -> bool:
    try:
        response = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
        rules = response.get("Rules", [])
        return any(rule.get("Status") == "Enabled" for rule in rules)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("NoSuchLifecycleConfiguration", "NoSuchBucket"):
            return False
        raise


def is_lifecycle_exempt(bucket: str) -> bool:
    try:
        response = s3.get_bucket_tagging(Bucket=bucket)
        tags = {tag["Key"].lower(): tag["Value"].lower() for tag in response.get("TagSet", [])}
        return tags.get("lifecycle-exempt") == "true"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("NoSuchTagSet", "NoSuchBucket"):
            return False
        raise


def get_bucket_size_bytes(bucket: str) -> int | None:
    """Return latest BucketSizeBytes value, or None when no datapoint exists."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=METRIC_LOOKBACK_DAYS)

    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/S3",
        MetricName="BucketSizeBytes",
        Dimensions=[
            {"Name": "BucketName", "Value": bucket},
            {"Name": "StorageType", "Value": "StandardStorage"},
        ],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Average"],
    )

    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None

    latest = max(datapoints, key=lambda dp: dp["Timestamp"])
    return int(latest["Average"])


def lifecycle_policy() -> dict:
    return {
        "Rules": [
            {
                "ID": "auto-cost-optimization-standard-ia-glacier",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "Transitions": [
                    {"Days": 30, "StorageClass": "STANDARD_IA"},
                    {"Days": 180, "StorageClass": "GLACIER"},
                ],
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
            }
        ]
    }


def apply_lifecycle(bucket: str) -> None:
    policy = lifecycle_policy()
    if DRY_RUN:
        log_event("info", bucket, "DRY_RUN", "Would apply lifecycle policy", policy=policy)
        return

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration=policy,
    )
    log_event("info", bucket, "REMEDIATED", "Lifecycle policy applied", policy=policy)


def process_bucket(bucket: str) -> str:
    try:
        if bucket_has_active_lifecycle(bucket):
            log_event("info", bucket, "SKIPPED", "Active lifecycle policy already exists")
            return "skipped_has_lifecycle"

        if is_lifecycle_exempt(bucket):
            log_event("info", bucket, "EXEMPT", "Exempt — No Action Taken")
            return "skipped_exempt"

        size_bytes = get_bucket_size_bytes(bucket)
        if size_bytes is None:
            log_event("warning", bucket, "SKIPPED", "No BucketSizeBytes datapoint returned; metric may be delayed")
            return "skipped_no_metric"

        if size_bytes <= SIZE_THRESHOLD_BYTES:
            log_event("info", bucket, "SKIPPED", "Bucket below threshold", size_bytes=size_bytes)
            return "skipped_below_threshold"

        apply_lifecycle(bucket)
        return "dry_run" if DRY_RUN else "remediated"

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        log_event("error", bucket, "ERROR", "AWS API error", error_code=code, error=str(exc))
        return "error"
    except (BotoCoreError, TimeoutError) as exc:
        log_event("error", bucket, "ERROR", "SDK/timeout error", error=str(exc))
        return "error"
    except Exception as exc:  # defensive guard for production Lambda safety
        log_event("error", bucket, "ERROR", "Unhandled error", error=str(exc))
        return "error"


def list_all_buckets() -> list[str]:
    # S3 ListBuckets is account-wide and currently not tokenized, but this wrapper isolates pagination risk.
    response = s3.list_buckets()
    return [bucket["Name"] for bucket in response.get("Buckets", [])]


def lambda_handler(event, context):
    started = time.time()
    buckets = list_all_buckets()
    summary = {
        "timestamp_eastern": eastern_now_iso(),
        "dry_run": DRY_RUN,
        "bucket_count": len(buckets),
        "results": {},
    }

    for bucket in buckets:
        result = process_bucket(bucket)
        summary["results"][result] = summary["results"].get(result, 0) + 1

    summary["duration_seconds"] = round(time.time() - started, 2)
    logger.info(json.dumps({"summary": summary}, default=str))
    return summary
