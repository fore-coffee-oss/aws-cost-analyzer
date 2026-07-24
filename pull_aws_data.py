#!/usr/bin/env python3
"""
Pull AWS billing and infrastructure data for offline analysis.
Output: ./data/YYYY-MM-DD/<profile>/ (JSON files, one per resource type)

Usage:
    python3 pull_aws_data.py
    python3 pull_aws_data.py --profile staging
    python3 pull_aws_data.py --profile prod --region ap-southeast-1 --months-back 5
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not installed. Run: pip install boto3")

# ── CLI args ──────────────────────────────────────────────────────────────────

def parse_args():
    args = sys.argv[1:]
    profile, months_back = "", 3
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")
    i = 0
    while i < len(args):
        if args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]; i += 2
        elif args[i] == "--region" and i + 1 < len(args):
            region = args[i + 1]; i += 2
        elif args[i] == "--months-back" and i + 1 < len(args):
            months_back = int(args[i + 1]); i += 2
        else:
            sys.exit(f"Unknown flag: {args[i]}")
    return profile, region, months_back

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\033[0;32m[{ts}]\033[0m {msg}")

def warn(msg):
    print(f"\033[1;33m[WARN]\033[0m {msg}")

# ── Date helpers ──────────────────────────────────────────────────────────────

def month_start_n_back(d, n):
    year, month = d.year, d.month - n
    while month <= 0:
        month += 12; year -= 1
    return date(year, month, 1)

# ── I/O helpers ───────────────────────────────────────────────────────────────

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def safe(out_path, func, **kwargs):
    """Call func(**kwargs), strip boto3 metadata, save to out_path. On error save {} and warn."""
    try:
        result = func(**kwargs)
        if isinstance(result, dict):
            result.pop("ResponseMetadata", None)
        save(out_path, result)
        return result
    except Exception as e:
        warn(f"{e}  →  {out_path.name}")
        save(out_path, {})
        return {}


def pages(client, operation, result_key, **kwargs):
    """Collect all pages of a paginated call."""
    try:
        paginator = client.get_paginator(operation)
        items = []
        for page in paginator.paginate(**kwargs):
            items.extend(page.get(result_key, []))
        return items
    except Exception as e:
        warn(f"Pagination error ({operation}): {e}")
        return []

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    profile, region, months_back = parse_args()

    session = boto3.Session(profile_name=profile or None, region_name=region)

    today         = date.today()
    current_month = today.replace(day=1)
    monthly_start = month_start_n_back(today, months_back)
    daily_start   = today - timedelta(days=90)
    metrics_start = today - timedelta(days=7)
    s3_cw_start   = today - timedelta(days=2)

    ts     = today.isoformat()
    cur_mo = current_month.isoformat()
    mo_st  = monthly_start.isoformat()
    da_st  = daily_start.isoformat()
    me_st  = metrics_start.isoformat()
    s3_st  = s3_cw_start.isoformat()

    # ── Pre-flight ────────────────────────────────────────────────────────────
    try:
        identity   = session.client("sts").get_caller_identity()
        account_id = identity["Account"]
    except Exception as e:
        sys.exit(f"\033[0;31m[ERROR]\033[0m AWS credentials invalid or expired: {e}")

    try:
        aliases       = session.client("iam").list_account_aliases().get("AccountAliases", [])
        account_alias = aliases[0] if aliases else "unknown"
    except Exception:
        account_alias = "unknown"

    log(f"Account: {account_id} ({account_alias})  Region: {region}")
    log(f"Billing — monthly: {mo_st} → {cur_mo}  |  daily: {da_st} → {ts}")

    # ── Output directory ──────────────────────────────────────────────────────
    out = Path("data") / ts / (profile or "default")
    for sub in [
        "billing",
        "infra/ec2", "infra/rds", "infra/rds/metrics",
        "infra/s3", "infra/lambda", "infra/ecs", "infra/ecs/taskdefs",
        "infra/eks", "infra/elasticache", "infra/cloudfront",
        "infra/vpc", "infra/logs",
    ]:
        (out / sub).mkdir(parents=True, exist_ok=True)

    # ── Clients ───────────────────────────────────────────────────────────────
    ce       = session.client("ce",                         region_name="us-east-1")
    ec2      = session.client("ec2",                        region_name=region)
    rds_c    = session.client("rds",                        region_name=region)
    s3       = session.client("s3")
    cw       = session.client("cloudwatch",                 region_name=region)
    cw_use1  = session.client("cloudwatch",                 region_name="us-east-1")
    lmb      = session.client("lambda",                     region_name=region)
    ecs      = session.client("ecs",                        region_name=region)
    eks      = session.client("eks",                        region_name=region)
    ecache   = session.client("elasticache",                region_name=region)
    cf       = session.client("cloudfront")
    elb      = session.client("elbv2",                      region_name=region)
    logs     = session.client("logs",                       region_name=region)
    logs_e1  = session.client("logs",                       region_name="us-east-1")
    aas      = session.client("application-autoscaling",    region_name=region)

    mo_period  = {"Start": mo_st,  "End": cur_mo}
    da_period  = {"Start": da_st,  "End": ts}
    cur_period = {"Start": cur_mo, "End": ts}

    # ═════════════════════════════════════════════════════════════════════════
    # BILLING
    # ═════════════════════════════════════════════════════════════════════════
    log("Billing › monthly totals")
    safe(out / "billing/monthly_total.json", ce.get_cost_and_usage,
         TimePeriod=mo_period, Granularity="MONTHLY",
         Metrics=["BlendedCost", "UnblendedCost", "UsageQuantity"])

    log("Billing › monthly by service")
    safe(out / "billing/monthly_by_service.json", ce.get_cost_and_usage,
         TimePeriod=mo_period, Granularity="MONTHLY",
         Metrics=["BlendedCost", "UnblendedCost"],
         GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])

    log("Billing › monthly by region")
    safe(out / "billing/monthly_by_region.json", ce.get_cost_and_usage,
         TimePeriod=mo_period, Granularity="MONTHLY",
         Metrics=["BlendedCost"],
         GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}])

    log("Billing › monthly by linked account")
    safe(out / "billing/monthly_by_account.json", ce.get_cost_and_usage,
         TimePeriod=mo_period, Granularity="MONTHLY",
         Metrics=["BlendedCost"],
         GroupBy=[{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}])

    log("Billing › month-to-date by service")
    safe(out / "billing/mtd_by_service.json", ce.get_cost_and_usage,
         TimePeriod=cur_period, Granularity="MONTHLY",
         Metrics=["BlendedCost", "UnblendedCost"],
         GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])

    log("Billing › daily totals (last 90 days)")
    safe(out / "billing/daily_total.json", ce.get_cost_and_usage,
         TimePeriod=da_period, Granularity="DAILY",
         Metrics=["BlendedCost", "UnblendedCost"])

    log("Billing › daily by service (last 90 days)")
    safe(out / "billing/daily_by_service.json", ce.get_cost_and_usage,
         TimePeriod=da_period, Granularity="DAILY",
         Metrics=["BlendedCost"],
         GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])

    log("Billing › daily RDS by record type")
    safe(out / "billing/daily_rds_by_record_type.json", ce.get_cost_and_usage,
         TimePeriod=da_period, Granularity="DAILY",
         Metrics=["BlendedCost"],
         Filter={"Dimensions": {"Key": "SERVICE",
                                "Values": ["Amazon Relational Database Service"]}},
         GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}])

    log("Billing › cost allocation tags")
    safe(out / "billing/cost_allocation_tags.json",
         ce.list_cost_allocation_tags, Status="Active")

    log("Billing › savings plans utilization")
    safe(out / "billing/savings_plans.json",
         ce.get_savings_plans_utilization, TimePeriod=mo_period)

    log("Billing › RI recommendations")
    safe(out / "billing/ri_recommendations.json",
         ce.get_reservation_purchase_recommendation,
         Service="Amazon Elastic Compute Cloud - Compute",
         LookbackPeriodInDays="SIXTY_DAYS",
         TermInYears="ONE_YEAR",
         PaymentOption="NO_UPFRONT")

    log("Billing › savings plans coverage")
    safe(out / "billing/savings_plans_coverage.json",
         ce.get_savings_plans_coverage,
         TimePeriod=mo_period, Granularity="MONTHLY")

    log("Billing › data transfer by usage type")
    safe(out / "billing/data_transfer.json", ce.get_cost_and_usage,
         TimePeriod=mo_period, Granularity="MONTHLY",
         Metrics=["BlendedCost"],
         Filter={"Dimensions": {"Key": "SERVICE", "Values": [
             "EC2 - Other", "Amazon Elastic Compute Cloud - Compute",
         ]}},
         GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}])

    # ═════════════════════════════════════════════════════════════════════════
    # EC2
    # ═════════════════════════════════════════════════════════════════════════
    log("EC2 › instances")
    safe(out / "infra/ec2/instances.json",         ec2.describe_instances)
    log("EC2 › volumes")
    safe(out / "infra/ec2/volumes.json",            ec2.describe_volumes)
    log("EC2 › snapshots (owned)")
    safe(out / "infra/ec2/snapshots.json",          ec2.describe_snapshots,  OwnerIds=[account_id])
    log("EC2 › AMIs (owned)")
    safe(out / "infra/ec2/amis.json",               ec2.describe_images,     Owners=[account_id])
    log("EC2 › elastic IPs")
    safe(out / "infra/ec2/elastic_ips.json",        ec2.describe_addresses)
    log("EC2 › reserved instances")
    safe(out / "infra/ec2/reserved_instances.json", ec2.describe_reserved_instances)
    log("EC2 › spot requests")
    safe(out / "infra/ec2/spot_requests.json",      ec2.describe_spot_instance_requests)

    # ═════════════════════════════════════════════════════════════════════════
    # RDS
    # ═════════════════════════════════════════════════════════════════════════
    log("RDS › instances")
    rds_resp = safe(out / "infra/rds/instances.json",          rds_c.describe_db_instances)
    log("RDS › clusters (Aurora)")
    safe(out / "infra/rds/clusters.json",                      rds_c.describe_db_clusters)
    log("RDS › snapshots (manual)")
    safe(out / "infra/rds/snapshots.json",                     rds_c.describe_db_snapshots, SnapshotType="manual")
    log("RDS › reserved instances")
    safe(out / "infra/rds/reserved_instances.json",            rds_c.describe_reserved_db_instances)

    # ═════════════════════════════════════════════════════════════════════════
    # S3
    # ═════════════════════════════════════════════════════════════════════════
    log("S3 › buckets")
    buckets_resp = safe(out / "infra/s3/buckets.json", s3.list_buckets)
    bucket_names = [b["Name"] for b in buckets_resp.get("Buckets", [])]

    log("S3 › storage sizes (CloudWatch / us-east-1)")
    s3_sizes = []
    for bucket in bucket_names:
        try:
            resp = cw_use1.get_metric_statistics(
                Namespace="AWS/S3", MetricName="BucketSizeBytes",
                Dimensions=[{"Name": "BucketName", "Value": bucket},
                            {"Name": "StorageType", "Value": "StandardStorage"}],
                StartTime=f"{s3_st}T00:00:00Z", EndTime=f"{ts}T00:00:00Z",
                Period=86400, Statistics=["Average"],
            )
            dps  = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
            size = dps[-1]["Average"] if dps else None
        except Exception:
            size = None
        s3_sizes.append({"Bucket": bucket, "BucketSizeBytes": size})
    save(out / "infra/s3/storage_sizes.json", s3_sizes)

    log("S3 › lifecycle policies")
    s3_lifecycle = []
    for bucket in bucket_names:
        try:
            policy = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
            policy.pop("ResponseMetadata", None)
        except ClientError as e:
            policy = None if e.response["Error"]["Code"] == "NoSuchLifecycleConfiguration" else None
        s3_lifecycle.append({"Bucket": bucket, "Lifecycle": policy})
    save(out / "infra/s3/lifecycle_policies.json", s3_lifecycle)

    # ═════════════════════════════════════════════════════════════════════════
    # Lambda
    # ═════════════════════════════════════════════════════════════════════════
    log("Lambda › functions")
    functions = pages(lmb, "list_functions", "Functions")
    save(out / "infra/lambda/functions.json", {"Functions": functions})

    # ═════════════════════════════════════════════════════════════════════════
    # ECS
    # ═════════════════════════════════════════════════════════════════════════
    log("ECS › clusters")
    cluster_arns = pages(ecs, "list_clusters", "clusterArns")
    save(out / "infra/ecs/clusters_list.json", {"clusterArns": cluster_arns})

    if cluster_arns:
        safe(out / "infra/ecs/clusters_detail.json",
             ecs.describe_clusters,
             clusters=cluster_arns, include=["STATISTICS", "SETTINGS"])

        for arn in cluster_arns:
            name = arn.split("/")[-1]
            log(f"ECS › services in {name}")
            svc_arns = pages(ecs, "list_services", "serviceArns", cluster=arn)
            save(out / f"infra/ecs/services_{name}.json", {"serviceArns": svc_arns})

            log(f"ECS › service details for {name}")
            for i in range(0, len(svc_arns), 10):
                batch = svc_arns[i:i + 10]
                safe(out / f"infra/ecs/services_detail_{name}_{i // 10}.json",
                     ecs.describe_services, cluster=arn, services=batch)

    log("ECS › autoscaling targets")
    aas_targets = pages(aas, "describe_scalable_targets", "ScalableTargets",
                        ServiceNamespace="ecs")
    save(out / "infra/ecs/autoscaling_targets.json", {"ScalableTargets": aas_targets})

    log("ECS › autoscaling policies")
    aas_policies = pages(aas, "describe_scaling_policies", "ScalingPolicies",
                         ServiceNamespace="ecs")
    save(out / "infra/ecs/autoscaling_policies.json", {"ScalingPolicies": aas_policies})

    log("ECS › task definition families")
    families = pages(ecs, "list_task_definition_families", "families", status="ACTIVE")
    save(out / "infra/ecs/task_families.json", {"families": families})

    for family in families:
        log(f"ECS › task definition {family}")
        safe(out / f"infra/ecs/taskdefs/{family}.json",
             ecs.describe_task_definition, taskDefinition=family)

    # ═════════════════════════════════════════════════════════════════════════
    # EKS
    # ═════════════════════════════════════════════════════════════════════════
    log("EKS › clusters")
    eks_clusters = pages(eks, "list_clusters", "clusters")
    save(out / "infra/eks/clusters.json", {"clusters": eks_clusters})
    for cluster in eks_clusters:
        log(f"EKS › {cluster}")
        safe(out / f"infra/eks/cluster_{cluster}.json",  eks.describe_cluster, name=cluster)
        safe(out / f"infra/eks/nodegroups_{cluster}.json", eks.list_nodegroups, clusterName=cluster)

    # ═════════════════════════════════════════════════════════════════════════
    # ElastiCache
    # ═════════════════════════════════════════════════════════════════════════
    log("ElastiCache › clusters")
    safe(out / "infra/elasticache/clusters.json",          ecache.describe_cache_clusters)
    log("ElastiCache › replication groups")
    safe(out / "infra/elasticache/replication_groups.json", ecache.describe_replication_groups)

    # ═════════════════════════════════════════════════════════════════════════
    # CloudFront
    # ═════════════════════════════════════════════════════════════════════════
    log("CloudFront › distributions")
    safe(out / "infra/cloudfront/distributions.json", cf.list_distributions)

    # ═════════════════════════════════════════════════════════════════════════
    # VPC / Networking
    # ═════════════════════════════════════════════════════════════════════════
    log("VPC › vpcs");             safe(out / "infra/vpc/vpcs.json",            ec2.describe_vpcs)
    log("VPC › subnets");          safe(out / "infra/vpc/subnets.json",          ec2.describe_subnets)
    log("VPC › NAT gateways");     safe(out / "infra/vpc/nat_gateways.json",     ec2.describe_nat_gateways)
    log("VPC › load balancers");   safe(out / "infra/vpc/load_balancers.json",   elb.describe_load_balancers)
    log("VPC › target groups");    safe(out / "infra/vpc/target_groups.json",    elb.describe_target_groups)
    log("VPC › VPN connections");  safe(out / "infra/vpc/vpn_connections.json",  ec2.describe_vpn_connections)
    log("VPC › transit gateways"); safe(out / "infra/vpc/transit_gateways.json", ec2.describe_transit_gateways)
    log("VPC › endpoints");        safe(out / "infra/vpc/vpc_endpoints.json",    ec2.describe_vpc_endpoints)

    # ═════════════════════════════════════════════════════════════════════════
    # CloudWatch Log Groups
    # ═════════════════════════════════════════════════════════════════════════
    log(f"Logs › CloudWatch log groups ({region})")
    save(out / "infra/logs/log_groups.json",
         {"logGroups": pages(logs, "describe_log_groups", "logGroups")})

    log("Logs › CloudWatch log groups (us-east-1)")
    save(out / "infra/logs/log_groups_us_east_1.json",
         {"logGroups": pages(logs_e1, "describe_log_groups", "logGroups")})

    # ═════════════════════════════════════════════════════════════════════════
    # RDS CloudWatch Metrics  (last 7 days, hourly)
    # ═════════════════════════════════════════════════════════════════════════
    db_ids = [db["DBInstanceIdentifier"] for db in rds_resp.get("DBInstances", [])]
    for db_id in db_ids:
        log(f"RDS metrics › {db_id}")
        for metric in ["CPUUtilization", "FreeableMemory", "DatabaseConnections",
                       "ReadIOPS", "WriteIOPS"]:
            safe(out / f"infra/rds/metrics/{db_id}_{metric}.json",
                 cw.get_metric_statistics,
                 Namespace="AWS/RDS", MetricName=metric,
                 Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
                 StartTime=f"{me_st}T00:00:00Z", EndTime=f"{ts}T00:00:00Z",
                 Period=3600, Statistics=["Average", "Maximum"])

    # ── Metadata ──────────────────────────────────────────────────────────────
    save(out / "pull_metadata.json", {
        "pulled_at":             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id":            account_id,
        "account_alias":         account_alias,
        "region":                region,
        "billing_monthly_start": mo_st,
        "billing_monthly_end":   cur_mo,
        "billing_daily_start":   da_st,
        "billing_daily_end":     ts,
    })

    log(f"Done. Output: {out}")
    print()
    for f in sorted(out.rglob("*.json")):
        print(f"  {f.stat().st_size:>8,} B  {f.relative_to(out)}")


if __name__ == "__main__":
    main()
