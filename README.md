# AWS Cost Analyzer

A lightweight AWS cost investigation pipeline — no SaaS, no server, no subscription.
Pulls billing and infrastructure data from AWS APIs into local JSON snapshots, then runs an offline analysis script to surface cost anomalies, waste, and right-sizing opportunities.

Built and used in production to find and eliminate real, recurring AWS waste.

---

## How it works

```
AWS APIs (Cost Explorer + EC2 + RDS + ECS + CloudWatch + S3 + Lambda)
  ↓
Local JSON snapshots  ./data/YYYY-MM-DD/<profile>/
  ↓
Offline analysis      compare snapshots, apply heuristics, flag anomalies
  ↓
Ranked recommendations — dollar-quantified, specific, actionable
```

The analysis script is fully offline. It reads from `./data/` and makes no AWS calls. Pull once, analyze as many times as you like.

---

## Requirements

```bash
pip install -r requirements.txt
```

- **boto3** — AWS API access (no AWS CLI required)
- **questionary** — interactive terminal menus
- **openpyxl** — RI plan Excel export
- **python-dateutil** — date arithmetic in analysis
- **Chrome or Chromium** — optional, for PDF export

---

## Quick start

```bash
# 1. Clone the repo
git clone <repo-url>
cd aws-cost-analyzer

# 2. Add your AWS credentials
python3 cli.py profiles --add

# 3. Copy and edit the config
cp config.json.example config.json

# 4. Pull data and run
python3 cli.py
```

Or skip the menu entirely:

```bash
python3 cli.py pull
python3 cli.py report
python3 cli.py report --pdf
```

---

## IAM permissions

Create a dedicated read-only IAM user (`cost-data-reader`) with this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "iam:ListAccountAliases",
        "ce:Get*",
        "ce:List*",
        "ec2:Describe*",
        "rds:Describe*",
        "ecs:List*",
        "ecs:Describe*",
        "eks:List*",
        "eks:Describe*",
        "elasticache:Describe*",
        "cloudwatch:GetMetricStatistics",
        "logs:DescribeLogGroups",
        "s3:ListAllMyBuckets",
        "s3:GetLifecycleConfiguration",
        "lambda:ListFunctions",
        "cloudfront:ListDistributions",
        "elasticloadbalancing:Describe*",
        "application-autoscaling:Describe*"
      ],
      "Resource": "*"
    }
  ]
}
```

No write permissions. The scripts only read, never modify anything.

---

## Configuration

Copy `config.json.example` to `config.json` (gitignored — never committed):

```json
{
  "profiles": {
    "production": {
      "account_label": "My Company Production",
      "tracked_rds_instances": [
        "my-primary-db",
        "my-replica-db"
      ]
    },
    "staging": {
      "account_label": "My Company Staging",
      "tracked_rds_instances": [
        "staging-primary-db"
      ]
    }
  }
}
```

`tracked_rds_instances` enables Section 3d — a day-by-day cost tracker for specific RDS instances across all snapshots. Each profile can have its own list. Leave empty to skip that section.

---

## CLI

```bash
python3 cli.py                                              # interactive menu
python3 cli.py pull                                         # pull from default profile
python3 cli.py pull --profile production                    # pull specific profile
python3 cli.py pull --profile production --profile staging  # pull multiple at once
python3 cli.py analyze                                      # analyze latest snapshot
python3 cli.py analyze data/2026-07-06/production           # specific snapshot
python3 cli.py report                                       # generate HTML report
python3 cli.py report --pdf                                 # HTML + PDF
python3 cli.py ri-plan                                      # RI planning Excel file
python3 cli.py profiles --list                              # list AWS profiles
python3 cli.py profiles --add                               # add a profile
python3 cli.py profiles --remove staging                    # remove a profile
```

### Interactive menu

Run `python3 cli.py` for a fully keyboard-driven menu (arrow keys + space + enter):

- **Pull** — checkbox multi-select across all configured profiles, prompted for months of history
- **Analyze / HTML report / PDF report / RI plan** — two-step date → profile picker, supports multiple profiles
- **Manage profiles** — add/remove AWS credentials stored in `~/.aws/`

### Multi-account

```bash
# Pull both accounts in one command
python3 cli.py pull --profile production --profile staging
# Snapshots land in:
#   data/2026-07-06/production/
#   data/2026-07-06/staging/

# Analyze a specific account
python3 cli.py analyze data/2026-07-06/staging
```

---

## What the analysis covers

| Section | What it checks |
|---|---|
| 1 | Monthly cost trend, daily spike detection |
| 2 | Top services by cost, month-over-month change |
| 3 | RDS instances with RI coverage |
| 3b | RDS per-instance cost estimate + RI ROI |
| 3c | RDS daily cost tracker — cross-snapshot change detection |
| 3d | Tracked DB cluster day-by-day (configured per profile in `config.json`) |
| 4 | CloudWatch anomaly flag, Container Insights status |
| 5 | EC2 waste — stopped instances, unattached/gp2 volumes |
| 6 | CloudWatch log groups — sizes, retention, groups without expiry |
| 7 | RDS utilization — CPU, memory, connections, Multi-AZ |
| 8 | ECS task definitions — CPU/memory allocation, sidecars |
| 8b | ECS running services — task counts, vCPU/RAM, autoscaling |
| 9 | RDS memory deep dive — peak RAM vs total, right-sizing |
| 10 | Month-start billing spikes |
| 11 | CloudWatch cost breakdown |
| 12 | ECS cost growth driver |
| 13 | Savings Plans coverage |
| 14 | Data transfer + VPC endpoint gap |
| 15 | S3 lifecycle policies |
| 16 | ElastiCache replication groups — node type, engine, HA status, cost estimate |
| 17 | EKS clusters — k8s version, status, control-plane cost |
| 18 | Savings opportunities summary — dollar-quantified, ranked |

---

## Data layout

```
data/YYYY-MM-DD/<profile>/
  pull_metadata.json          # account, region, pull timestamp
  billing/
    monthly_total.json
    monthly_by_service.json
    monthly_by_region.json
    monthly_by_account.json
    mtd_by_service.json
    daily_total.json
    daily_by_service.json
    daily_rds_by_record_type.json
    savings_plans.json
    savings_plans_coverage.json
    ri_recommendations.json
    cost_allocation_tags.json
    data_transfer.json
  infra/
    ec2/                      # instances, volumes, snapshots, AMIs, EIPs, RIs, spot
    rds/
      instances.json
      reserved_instances.json
      clusters.json
      snapshots.json
      metrics/                # per-instance CloudWatch: CPU, memory, connections, IOPS
    s3/                       # buckets, storage_sizes, lifecycle_policies
    ecs/
      clusters_detail.json
      services_detail_<cluster>_<n>.json
      autoscaling_targets.json
      autoscaling_policies.json
      taskdefs/
    logs/
      log_groups.json
      log_groups_us_east_1.json
    lambda/functions.json
    eks/
    elasticache/
    cloudfront/
    vpc/
```

`./data/` is gitignored — snapshots contain account IDs, ARNs, and billing amounts. Never commit them.

---

## Files

| File | Purpose |
|---|---|
| `cli.py` | Main entry point — interactive menu + subcommands |
| `pull_aws_data.py` | Pulls data from AWS APIs via boto3, saves to `./data/` |
| `analyze.py` | Offline analysis engine — reads `./data/`, prints report |
| `to_html.py` | Converts plain-text report to styled HTML |
| `make_ri_plan.py` | Generates RI planning Excel file |
| `config.json` | Your account config — per-profile settings (gitignored) |
| `config.json.example` | Template to copy |

---

## Notes

- Pricing tables in `analyze.py` and `make_ri_plan.py` are for `ap-southeast-1` (Singapore). Update the constants at the top of each file for your region.
- The scripts were written with help from [Claude](https://claude.ai). They do not connect to any external service — all AWS access is via the pull script, which you control.
- Pull frequency: weekly is enough for most teams. Each pull adds a data point; more snapshots means richer cross-snapshot change history.

---

## License

[MIT](LICENSE)
