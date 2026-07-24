"""
Shared RDS pricing tables & helpers — ap-southeast-1 (Singapore), Single-AZ,
MySQL/PostgreSQL. Used by both analyze.py (cost analysis/report) and
make_ri_plan.py (RI planning Excel export) so the two never drift apart.

Source: aws.amazon.com/rds/pricing — verify before purchasing RIs.
"""

# Instance class → total RAM in GB (current-gen)
RDS_RAM_GB = {
    "db.t3.small": 2,   "db.t3.medium": 4,   "db.t3.large": 8,
    "db.t4g.medium": 4, "db.t4g.large": 8,
    "db.m7i.large": 8,  "db.m7i.xlarge": 16,  "db.m7i.2xlarge": 32,
    "db.m7i.4xlarge": 64, "db.m7i.8xlarge": 128, "db.m7i.12xlarge": 192, "db.m7i.16xlarge": 256,

    "db.m7g.large": 8,  "db.m7g.xlarge": 16,  "db.m7g.2xlarge": 32,  "db.m7g.4xlarge": 64,
    "db.r7i.large": 16, "db.r7i.xlarge": 32,  "db.r7i.2xlarge": 64,
}

# On-demand hourly prices, Single-AZ
RDS_ONDEMAND_HOURLY = {
    "db.t3.small": 0.034,   "db.t3.medium": 0.068,
    "db.t4g.medium": 0.065,
    "db.m7i.xlarge": 0.494, "db.m7i.2xlarge": 0.988, "db.m7i.4xlarge": 1.976,
    "db.m7i.8xlarge": 3.952, "db.m7i.12xlarge": 5.928, "db.m7i.16xlarge": 7.904,
    "db.m7g.large": 0.165,  "db.m7g.xlarge": 0.331,
    "db.m7g.2xlarge": 0.662, "db.m7g.4xlarge": 1.323,
}

# 1-year no-upfront RI effective hourly rate (~33% discount vs on-demand for
# current-gen m7i/m7g, ~40% for t3/t4g burstable).
#
# NOTE: the m7g rates below were originally transcribed higher than on-demand
# (a data-entry error — RI is never more expensive than on-demand) and have
# been corrected to match the same OD × 0.67 ratio already used consistently
# across every m7i entry in this table. Verify against aws.amazon.com/rds/pricing
# before purchasing — this is an estimate, not a live quote.
RDS_RI_1YR_HOURLY = {
    "db.t3.small": 0.020,   "db.t3.medium": 0.041,
    "db.t4g.medium": 0.039,
    "db.m7i.xlarge": 0.3310,  "db.m7i.2xlarge": 0.6620,
    "db.m7i.4xlarge": 1.3240, "db.m7i.8xlarge": 2.6480, "db.m7i.12xlarge": 3.9720, "db.m7i.16xlarge": 5.2960,
    "db.m7g.large": 0.1106,   "db.m7g.xlarge": 0.2218,
    "db.m7g.2xlarge": 0.4435, "db.m7g.4xlarge": 0.8864,
}

# AWS standard normalization units — used for cross-class RI coverage
RDS_RI_NU = {
    "db.t3.small": 1,   "db.t3.medium": 2,
    "db.t4g.medium": 2,
    "db.m7i.xlarge": 8, "db.m7i.2xlarge": 16,
    "db.m7i.4xlarge": 32, "db.m7i.8xlarge": 64,
    "db.m7i.12xlarge": 96, "db.m7i.16xlarge": 128,
    "db.m7g.large": 4,  "db.m7g.xlarge": 8,
    "db.m7g.2xlarge": 16, "db.m7g.4xlarge": 32,
}

# gp2/gp3 storage — $/GB-month base rate, gp3 IOPS/throughput above free tier
RDS_STORAGE_MONTHLY_PER_GB = 0.138  # gp2 and gp3 base $/GB-month (confirmed AWS pricing API)
GP3_IOPS_FREE        = 3000   # first 3,000 IOPS included
GP3_IOPS_RATE        = 0.024  # $/IOPS-month above free tier
GP3_THROUGHPUT_FREE  = 125    # first 125 MiBps included
GP3_THROUGHPUT_RATE  = 0.096  # $/MiBps-month above free tier


def rds_storage_monthly(gb, storage_type="gp2", iops=0, throughput=0):
    cost = gb * RDS_STORAGE_MONTHLY_PER_GB
    if storage_type == "gp3":
        if iops > GP3_IOPS_FREE:
            cost += (iops - GP3_IOPS_FREE) * GP3_IOPS_RATE
        if throughput > GP3_THROUGHPUT_FREE:
            cost += (throughput - GP3_THROUGHPUT_FREE) * GP3_THROUGHPUT_RATE
    return cost
