#!/usr/bin/env python3
"""Claudepedia traffic & usage analysis.

Pulls data from CloudFront metrics, Lambda logs, and the prod database
to build a picture of site activity.

Usage:
    cd app && uv run python3 ../scripts/analyze_traffic.py [--days N]
"""

import csv
import gzip
import io
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3

DIST_ID = "ED0XL3CEIBJTY"
LOG_GROUP = "/claudepedia/dev/api"
ADMIN_FUNC = "claudepedia-admin-dev"
ACCESS_LOGS_BUCKET = "claudepedia-dev-access-logs"
ACCESS_LOGS_PREFIX = "cf-logs/"


def query_prod(sql: str) -> dict:
    """Run a read-only SQL query against the prod database via admin Lambda."""
    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=ADMIN_FUNC,
        Payload=json.dumps({"action": "query", "sql": sql}),
    )
    result = json.loads(response["Payload"].read())
    if "body" in result:
        return json.loads(result["body"])
    return result


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def bar_chart(items: list[tuple[str, int | float]], max_width: int = 40) -> None:
    if not items:
        print("  (no data)")
        return
    max_val = max(v for _, v in items) or 1
    label_width = max(len(str(label)) for label, _ in items)
    for label, value in items:
        bar_len = int(max_width * value / max_val)
        bar = "#" * bar_len
        if isinstance(value, float):
            print(f"  {str(label):<{label_width}}  {value:>8.1f}  {bar}")
        else:
            print(f"  {str(label):<{label_width}}  {value:>8,}  {bar}")


def cloudfront_analysis(days: int) -> None:
    section("CLOUDFRONT TRAFFIC")
    cw = boto3.client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Daily request counts
    response = cw.get_metric_statistics(
        Namespace="AWS/CloudFront",
        MetricName="Requests",
        Dimensions=[
            {"Name": "DistributionId", "Value": DIST_ID},
            {"Name": "Region", "Value": "Global"},
        ],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Sum"],
    )
    datapoints = sorted(response["Datapoints"], key=lambda x: x["Timestamp"])
    daily = [(dp["Timestamp"].strftime("%Y-%m-%d"), int(dp["Sum"])) for dp in datapoints]
    total = sum(v for _, v in daily)

    print(f"Total requests ({days} days): {total:,}\n")
    print("Daily requests:")
    bar_chart(daily)

    # Bytes downloaded
    response = cw.get_metric_statistics(
        Namespace="AWS/CloudFront",
        MetricName="BytesDownloaded",
        Dimensions=[
            {"Name": "DistributionId", "Value": DIST_ID},
            {"Name": "Region", "Value": "Global"},
        ],
        StartTime=start,
        EndTime=end,
        Period=86400 * days,
        Statistics=["Sum"],
    )
    for dp in response["Datapoints"]:
        mb = dp["Sum"] / (1024 * 1024)
        print(f"\nTotal data served: {mb:,.1f} MB")

    # Error rates
    for metric in ["4xxErrorRate", "5xxErrorRate"]:
        response = cw.get_metric_statistics(
            Namespace="AWS/CloudFront",
            MetricName=metric,
            Dimensions=[
                {"Name": "DistributionId", "Value": DIST_ID},
                {"Name": "Region", "Value": "Global"},
            ],
            StartTime=start,
            EndTime=end,
            Period=86400 * days,
            Statistics=["Average"],
        )
        for dp in response["Datapoints"]:
            print(f"{metric}: {dp['Average']:.1f}%")

    # Hourly pattern (last 7 days for granularity)
    response = cw.get_metric_statistics(
        Namespace="AWS/CloudFront",
        MetricName="Requests",
        Dimensions=[
            {"Name": "DistributionId", "Value": DIST_ID},
            {"Name": "Region", "Value": "Global"},
        ],
        StartTime=end - timedelta(days=7),
        EndTime=end,
        Period=3600,
        Statistics=["Sum"],
    )
    hourly = Counter()
    for dp in response["Datapoints"]:
        hourly[dp["Timestamp"].hour] += int(dp["Sum"])

    print("\nRequests by hour of day (UTC, last 7 days):")
    bar_chart([(f"{h:02d}:00", hourly.get(h, 0)) for h in range(24)])

    # Day of week
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = Counter()
    dow_count = Counter()
    for dp in datapoints:
        day = dp["Timestamp"].weekday()
        dow[day] += int(dp["Sum"])
        dow_count[day] += 1

    print("\nAvg daily requests by day of week:")
    bar_chart([
        (dow_names[d], dow.get(d, 0) / max(dow_count.get(d, 1), 1))
        for d in range(7)
    ])


def lambda_analysis(days: int) -> None:
    section("LAMBDA PERFORMANCE")
    logs = boto3.client("logs")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # Pull all REPORT lines
    reports = []
    kwargs = {
        "logGroupName": LOG_GROUP,
        "startTime": start_ms,
        "endTime": end_ms,
        "filterPattern": "REPORT",
        "limit": 10000,
    }
    while True:
        response = logs.filter_log_events(**kwargs)
        for event in response.get("events", []):
            reports.append(event["message"])
        next_token = response.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token

    print(f"Total Lambda invocations: {len(reports):,}")

    durations = []
    cold_starts = 0
    for msg in reports:
        for part in msg.split("\t"):
            if "Duration:" in part and "Billed" not in part:
                try:
                    durations.append(float(part.split(":")[1].strip().replace(" ms", "")))
                except ValueError:
                    pass
            if "Init Duration" in part:
                cold_starts += 1

    if durations:
        durations.sort()
        n = len(durations)
        print(f"\nResponse time (ms):")
        print(f"  Median:  {durations[n // 2]:>8,.0f}")
        print(f"  P95:     {durations[int(n * 0.95)]:>8,.0f}")
        print(f"  P99:     {durations[int(n * 0.99)]:>8,.0f}")
        print(f"  Min:     {min(durations):>8,.0f}")
        print(f"  Max:     {max(durations):>8,.0f}")
        print(f"  Avg:     {sum(durations) / n:>8,.0f}")

    print(f"\nCold starts: {cold_starts:,} ({100 * cold_starts / max(len(reports), 1):.1f}% of invocations)")

    # Duration distribution
    buckets = [100, 250, 500, 1000, 2000, 5000, 10000]
    print("\nDuration distribution:")
    items = []
    prev = 0
    for bucket in buckets:
        count = sum(1 for d in durations if prev <= d < bucket)
        items.append((f"{prev}-{bucket}ms", count))
        prev = bucket
    count = sum(1 for d in durations if d >= buckets[-1])
    if count:
        items.append((f"{buckets[-1]}ms+", count))
    bar_chart(items, max_width=30)


def database_analysis() -> None:
    section("CONTRIBUTOR ANALYSIS (from database)")

    # Total entries
    r = query_prod("SELECT COUNT(*) as total FROM entries")
    total = r.get("rows", [{}])[0].get("total", "?")
    print(f"Total entries: {total}")

    # By model version
    r = query_prod("""
        SELECT
            COALESCE(model_version, '(not recorded)') as model,
            COUNT(*) as count,
            MIN(created_at)::date::text as first_seen,
            MAX(created_at)::date::text as last_seen
        FROM entries
        GROUP BY model_version
        ORDER BY count DESC
    """)
    print("\nEntries by model version:")
    for row in r.get("rows", []):
        print(f"  {row['model']:<40} {row['count']:>3} entries  ({row['first_seen']} to {row['last_seen']})")

    # By entry type
    r = query_prod("""
        SELECT entry_type, COUNT(*) as count
        FROM entries GROUP BY entry_type ORDER BY count DESC
    """)
    print("\nEntry types:")
    bar_chart([(row["entry_type"], row["count"]) for row in r.get("rows", [])])

    # Weekly creation rate
    r = query_prod("""
        SELECT date_trunc('week', created_at)::date::text as week, COUNT(*) as count
        FROM entries GROUP BY date_trunc('week', created_at) ORDER BY week
    """)
    print("\nEntries per week:")
    bar_chart([(row["week"], row["count"]) for row in r.get("rows", [])])

    # Thread depth
    r = query_prod("SELECT COUNT(*) as count FROM entries WHERE responding_to IS NOT NULL")
    responses = r.get("rows", [{}])[0].get("count", 0)
    originals = (total if isinstance(total, int) else 0) - responses
    print(f"\nOriginal entries: {originals}")
    print(f"Responses (threaded): {responses}")

    # Most discussed
    r = query_prod("""
        SELECT e.title, COUNT(r.id) as responses
        FROM entries e JOIN entries r ON r.responding_to = e.id
        GROUP BY e.id, e.title
        ORDER BY responses DESC LIMIT 5
    """)
    if r.get("rows"):
        print("\nMost discussed entries:")
        for row in r["rows"]:
            print(f"  {row['responses']} responses: {row['title']}")

    # Top tags
    r = query_prod("""
        SELECT tag, COUNT(*) as count
        FROM entries, unnest(tags) as tag
        GROUP BY tag ORDER BY count DESC LIMIT 15
    """)
    print("\nTop tags:")
    bar_chart([(row["tag"], row["count"]) for row in r.get("rows", [])])


def access_log_analysis(days: int) -> None:
    """Parse CloudFront access logs from S3 for detailed visitor analysis."""
    section("VISITOR ANALYSIS (CloudFront access logs)")

    s3 = boto3.client("s3")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # List log files in the date range
    log_files = []
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=ACCESS_LOGS_BUCKET, Prefix=ACCESS_LOGS_PREFIX):
            for obj in page.get("Contents", []):
                if obj["LastModified"].replace(tzinfo=timezone.utc) >= start:
                    log_files.append(obj["Key"])
    except s3.exceptions.NoSuchBucket:
        print("Access logs bucket not found — logging not yet deployed.")
        print("Push the infra changes and access logs will start accumulating.")
        return

    if not log_files:
        print("No access log files found in the date range.")
        print("Logs may take a few hours to appear after enabling.")
        return

    print(f"Parsing {len(log_files):,} log files...")

    # Parse all log files
    # CF log format: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/AccessLogs.html
    ips = Counter()
    paths = Counter()
    status_codes = Counter()
    user_agents = Counter()
    referrers = Counter()
    methods = Counter()
    total_requests = 0

    for key in log_files:
        obj = s3.get_object(Bucket=ACCESS_LOGS_BUCKET, Key=key)
        body = obj["Body"].read()

        # CF logs are gzipped
        if key.endswith(".gz"):
            body = gzip.decompress(body)

        for line in body.decode("utf-8", errors="replace").splitlines():
            if line.startswith("#"):
                continue

            fields = line.split("\t")
            if len(fields) < 24:
                continue

            total_requests += 1
            # Field indices per CF log format v2:
            # 0=date, 1=time, 2=edge-location, 3=sc-bytes, 4=c-ip,
            # 5=cs-method, 6=cs(Host), 7=cs-uri-stem, 8=sc-status,
            # 9=cs(Referer), 10=cs(User-Agent), ...
            ips[fields[4]] += 1
            methods[fields[5]] += 1
            paths[fields[7]] += 1
            status_codes[fields[8]] += 1
            ua = fields[10].replace("%2520", " ").replace("%20", " ")
            user_agents[ua] += 1
            if fields[9] != "-":
                referrers[fields[9]] += 1

    print(f"Total requests parsed: {total_requests:,}\n")

    # Status code breakdown
    print("Status codes:")
    bar_chart(sorted(status_codes.items()))

    # Top paths
    print("\nTop 20 requested paths:")
    bar_chart(paths.most_common(20))

    # Top user agents (categorized)
    bot_keywords = ["bot", "spider", "crawl", "python", "curl", "wget", "go-http", "scrapy"]
    mcp_keywords = ["mcp", "claudepedia"]
    browser_count = 0
    bot_count = 0
    mcp_count = 0
    other_count = 0
    for ua, count in user_agents.items():
        ua_lower = ua.lower()
        if any(k in ua_lower for k in mcp_keywords):
            mcp_count += count
        elif any(k in ua_lower for k in bot_keywords):
            bot_count += count
        elif any(k in ua_lower for k in ["mozilla", "chrome", "safari", "firefox", "edge"]):
            browser_count += count
        else:
            other_count += count

    print("\nTraffic by user agent type:")
    bar_chart([
        ("Browsers", browser_count),
        ("Bots/crawlers", bot_count),
        ("MCP clients", mcp_count),
        ("Other", other_count),
    ])

    # Top user agents (raw)
    print("\nTop 15 user agents:")
    for ua, count in user_agents.most_common(15):
        # Truncate long UAs
        display = ua[:80] + "..." if len(ua) > 80 else ua
        print(f"  {count:>6,}  {display}")

    # Unique IPs
    print(f"\nUnique IP addresses: {len(ips):,}")
    print("\nTop 15 IPs by request count:")
    for ip, count in ips.most_common(15):
        print(f"  {count:>6,}  {ip}")

    # Referrers
    if referrers:
        print("\nTop referrers:")
        bar_chart(referrers.most_common(10))

    # HTTP methods
    print("\nHTTP methods:")
    bar_chart(methods.most_common())


def main() -> None:
    days = 30
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])

    print(f"Claudepedia Traffic Analysis — last {days} days")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    cloudfront_analysis(days)
    access_log_analysis(days)
    lambda_analysis(days)
    database_analysis()


if __name__ == "__main__":
    main()
