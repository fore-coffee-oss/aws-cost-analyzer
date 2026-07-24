# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AWS billing and infrastructure cost analysis. Pulls raw data from AWS APIs into local JSON snapshots, then runs an offline analysis script to surface cost anomalies and right-sizing opportunities.

The report is designed to be pasted into a Claude conversation for FinOps, Infra, DevOps, and Dev advisory — all context needed to answer questions is embedded in the report itself.

## Scripts

```bash
# Interactive menu (recommended)
python3 cli.py

# Pull data — creates ./data/YYYY-MM-DD/<profile>/
python3 cli.py pull
python3 cli.py pull --profile production --profile staging  # multiple profiles

# Run analysis — output formats
python3 analyze.py                                           # plain text (terminal)
python3 analyze.py | python3 to_html.py > report.html       # HTML report
python3 analyze.py data/2026-07-06/production               # specific snapshot

# Generate RI plan Excel
python3 make_ri_plan.py
python3 make_ri_plan.py data/2026-07-06/production
```

`analyze.py` is fully offline — reads only from `./data/`. No AWS calls, no writes.

`pull_aws_data.py` uses boto3 directly (no AWS CLI required). Default `MONTHS_BACK=3`. Override via `--months-back 5` to pull further history.

`to_html.py` converts the plain-text report to styled HTML. PDF export uses Chrome headless via `python3 cli.py report --pdf`.

## Data Layout

```
data/YYYY-MM-DD/<profile>/
  pull_metadata.json          # account, region, time ranges used
  billing/
    monthly_total.json        # 3-month monthly totals
    monthly_by_service.json   # breakdown by AWS service
    monthly_by_region.json
    monthly_by_account.json
    mtd_by_service.json       # current month-to-date
    daily_total.json          # last 30 days
    daily_by_service.json
    daily_rds_by_record_type.json  # RDS cost by RIFee/Usage/Tax — explains spike days
    savings_plans.json
    savings_plans_coverage.json
    data_transfer.json        # EC2/EC2-Other broken down by usage type
    ri_recommendations.json
    cost_allocation_tags.json
  infra/
    ec2/                      # instances, volumes, snapshots, AMIs, EIPs, RIs, spot
    rds/
      instances.json
      reserved_instances.json
      clusters.json
      snapshots.json
      metrics/                # per-instance CloudWatch: CPU, FreeableMemory, connections, IOPS
    s3/                       # buckets, storage_sizes, lifecycle_policies
    ecs/
      clusters_detail.json    # includes Container Insights status (SETTINGS)
      services_detail_<cluster>_<n>.json  # running/desired counts per service (batched)
      autoscaling_targets.json            # ECS autoscaling min/max per service
      autoscaling_policies.json
      taskdefs/               # one file per active task family
    logs/log_groups.json               # CloudWatch log groups, primary region
    logs/log_groups_us_east_1.json     # CloudWatch log groups us-east-1 (WAF/CloudFront)
    lambda/functions.json
    eks/
    elasticache/
    cloudfront/
    vpc/
      vpc_endpoints.json      # existing VPC endpoints
```

`./data/` is gitignored — never commit snapshots (they contain account IDs, ARNs, billing amounts).

## Configuration

`config.json` (gitignored) stores per-profile settings used by `analyze.py`:

```json
{
  "profiles": {
    "production": {
      "account_label": "My Company Production",
      "tracked_rds_instances": ["my-primary-db", "my-replica-db"],
      "datadog": true
    },
    "staging": {
      "account_label": "My Company Staging",
      "tracked_rds_instances": ["staging-primary-db"],
      "datadog": true
    }
  }
}
```

The profile name is extracted automatically from the snapshot path (`data/YYYY-MM-DD/<profile>`). `tracked_rds_instances` enables the appendix day-by-day section. Top-level keys are a fallback for old flat snapshots.

`datadog` declares whether Datadog is confirmed to collect container metrics for the account: `true` → Container Insights advice says "Datadog already covers this"; `false` → advice says "verify monitoring first"; absent → advice stays neutral. The analyzer cannot detect this from AWS data alone (it only sees containers *named* `datadog` in task definitions), so it must be declared. Edit via `python3 cli.py config` or the "Analyzer settings" menu item.

## Analysis Sections

`analyze.py` computes sections in dependency order internally, then a reorder pass at the end of the script re-emits them in executive reading order (summary → conclusions → evidence → appendix). Report order:

- **Executive Summary** — total monthly cost, MoM change, top 3 cost drivers, potential savings, top 3 recommendations (generated last, moved to top)
- **1** Monthly cost trend + daily spike detection
- **2** Top services by cost with MoM change
- **3** Savings opportunities summary — dollar-quantified items ranked by impact, then tagged additional items (`[PRIORITY|CATEGORY|SAVING]` format, rendered as priority cards in HTML)
- **4** CloudWatch cost anomaly + Container Insights confirmed status
- **5** Month-start billing spikes (dynamic — detects whichever 1st-of-month spike is in the daily window)
- **6** ECS cost growth driver
- **7** RDS instances with RI coverage
- **7b** RDS per-instance cost estimate + RI ROI (ap-southeast-1 approximate prices)
- **7c** RI plan — active RIs with expiry, NU-based coverage per instance, purchase/renewal recommendations
- **7d** RDS utilization — CPU avg/max, free memory, connections, Multi-AZ
- **7e** RDS memory deep dive — peak RAM used vs total, right-sizing recommendations
- **7f** RDS daily cost tracker — cross-snapshot change detection, per-instance daily cost with RI normalization, actual daily RDS billing (last 90 days)
- **8** ECS task definitions — CPU/memory allocation, sidecar containers
- **8b** ECS running services — actual task counts, total allocated vCPU/RAM, autoscaling bounds, Fargate cost estimate
- **9** CloudWatch log groups — sizes, retention, groups without expiry
- **9b** CloudWatch cost breakdown — logs vs metrics gap
- **10** EC2 waste — stopped instances, unattached/gp2 volumes
- **11** Savings Plans coverage
- **12** Data transfer + VPC endpoint gap
- **13** S3 lifecycle policies
- **14** ElastiCache — Redis/Valkey caches (AWS API calls these "replication groups" even for single-node caches; falls back to `clusters.json` when `replication_groups.json` is empty)
- **15** EKS clusters
- **A1** Appendix: tracked DB cluster day-by-day — instances from `config.json tracked_rds_instances`, config timeline + per-instance daily cost vs actual billing

Internal `hr()` titles in the code still use the old numbering (1, 2, 3, 3b, 3c, 3d, 3e, 4–18); the `_SECTION_ORDER` table at the bottom of `analyze.py` maps old → new numbers. Add new sections to that table or they'll be appended at the end of the report.

Section 7f (internally 3c) detects RDS instance class changes automatically across all snapshots in `./data/`. Each new pull adds a data point — the more snapshots, the richer the change history. Frequent class changes on a primary DB (e.g. scaling up for peak-load events) are expected and tracked, not flagged as errors.

## IAM

The pull script requires read-only permissions. Create a dedicated `cost-data-reader` IAM user with:

```
sts:GetCallerIdentity, iam:ListAccountAliases,
ce:Get*, ce:List*,
ec2:Describe*, rds:Describe*,
ecs:List*, ecs:Describe*, eks:List*, eks:Describe*,
elasticache:Describe*, cloudfront:ListDistributions,
cloudwatch:GetMetricStatistics, logs:DescribeLogGroups,
s3:ListAllMyBuckets, s3:GetLifecycleConfiguration,
lambda:ListFunctions, elasticloadbalancing:Describe*,
application-autoscaling:Describe*
```

Note: `ce:List*` is required for `ListCostAllocationTags` (not covered by `ce:Get*`).

## Conventions

- **Region:** pricing tables in `analyze.py` and `make_ri_plan.py` assume `ap-southeast-1` (Singapore). Update the constants at the top of each file for other regions.
- **Credentials:** `.env` for local secrets (git-ignored), `.env.example` committed with placeholders. AWS credentials live in `~/.aws/` via `cli.py profiles --add`.
- **Dependencies:** Python only — `boto3`, `questionary`, `openpyxl`, `python-dateutil`.
