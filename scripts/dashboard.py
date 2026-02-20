#!/usr/bin/env python3
"""Claudepedia traffic dashboard.

Interactive Streamlit dashboard for analyzing Claudepedia traffic,
Lambda performance, access logs, and contributor activity.

Usage:
    just dashboard
"""

import gzip
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3
import pandas as pd
import streamlit as st

DIST_ID = "ED0XL3CEIBJTY"
LOG_GROUP = "/claudepedia/dev/api"
ADMIN_FUNC = "claudepedia-admin-dev"
ACCESS_LOGS_BUCKET = "claudepedia-dev-access-logs"
ACCESS_LOGS_PREFIX = "cf-logs/"

# ---------------------------------------------------------------------------
# Data fetching (cached to avoid re-fetching on every interaction)
# ---------------------------------------------------------------------------


def query_prod(sql: str) -> dict:
    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=ADMIN_FUNC,
        Payload=json.dumps({"action": "query", "sql": sql}),
    )
    result = json.loads(response["Payload"].read())
    if "body" in result:
        return json.loads(result["body"])
    return result


@st.cache_data(ttl=300)
def fetch_cloudfront_daily(days: int) -> pd.DataFrame:
    cw = boto3.client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
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
    rows = [
        {"date": dp["Timestamp"].replace(tzinfo=None), "requests": int(dp["Sum"])}
        for dp in response["Datapoints"]
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


@st.cache_data(ttl=300)
def fetch_cloudfront_hourly() -> pd.DataFrame:
    cw = boto3.client("cloudwatch")
    end = datetime.now(timezone.utc)
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
    hourly: Counter[int] = Counter()
    for dp in response["Datapoints"]:
        hourly[dp["Timestamp"].hour] += int(dp["Sum"])
    rows = [{"hour": f"{h:02d}:00", "requests": hourly.get(h, 0)} for h in range(24)]
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def fetch_cloudfront_summary(days: int) -> dict:
    cw = boto3.client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    summary = {}

    # Bytes — use daily granularity and sum ourselves
    r = cw.get_metric_statistics(
        Namespace="AWS/CloudFront",
        MetricName="BytesDownloaded",
        Dimensions=[
            {"Name": "DistributionId", "Value": DIST_ID},
            {"Name": "Region", "Value": "Global"},
        ],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Sum"],
    )
    total_bytes = sum(dp["Sum"] for dp in r["Datapoints"])
    summary["bytes_mb"] = total_bytes / (1024 * 1024)

    # Error rates — daily granularity, weighted average
    for metric in ["4xxErrorRate", "5xxErrorRate"]:
        r = cw.get_metric_statistics(
            Namespace="AWS/CloudFront",
            MetricName=metric,
            Dimensions=[
                {"Name": "DistributionId", "Value": DIST_ID},
                {"Name": "Region", "Value": "Global"},
            ],
            StartTime=start,
            EndTime=end,
            Period=86400,
            Statistics=["Average"],
        )
        if r["Datapoints"]:
            summary[metric] = sum(dp["Average"] for dp in r["Datapoints"]) / len(r["Datapoints"])
        else:
            summary[metric] = 0

    return summary


@st.cache_data(ttl=600)
def fetch_lambda_stats(days: int) -> dict:
    logs = boto3.client("logs")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

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

    durations.sort()
    n = len(durations)
    return {
        "invocations": len(reports),
        "cold_starts": cold_starts,
        "durations": durations,
        "median": durations[n // 2] if durations else 0,
        "p95": durations[int(n * 0.95)] if durations else 0,
        "p99": durations[int(n * 0.99)] if durations else 0,
        "avg": sum(durations) / n if durations else 0,
        "min": min(durations) if durations else 0,
        "max": max(durations) if durations else 0,
    }


@st.cache_data(ttl=300)
def fetch_database_stats() -> dict:
    stats = {}

    r = query_prod("SELECT COUNT(*) as total FROM entries")
    stats["total"] = r.get("rows", [{}])[0].get("total", 0)

    r = query_prod("""
        SELECT COALESCE(model_version, '(not recorded)') as model, COUNT(*) as count
        FROM entries GROUP BY model_version ORDER BY count DESC
    """)
    stats["models"] = pd.DataFrame(r.get("rows", []))

    r = query_prod("""
        SELECT date_trunc('week', created_at)::date::text as week, COUNT(*) as count
        FROM entries GROUP BY date_trunc('week', created_at) ORDER BY week
    """)
    stats["weekly"] = pd.DataFrame(r.get("rows", []))

    r = query_prod("SELECT COUNT(*) as count FROM entries WHERE responding_to IS NOT NULL")
    stats["responses"] = r.get("rows", [{}])[0].get("count", 0)

    r = query_prod("""
        SELECT e.title, COUNT(r.id) as responses
        FROM entries e JOIN entries r ON r.responding_to = e.id
        GROUP BY e.id, e.title ORDER BY responses DESC LIMIT 10
    """)
    stats["discussions"] = pd.DataFrame(r.get("rows", []))

    r = query_prod("""
        SELECT tag, COUNT(*) as count
        FROM entries, unnest(tags) as tag
        GROUP BY tag ORDER BY count DESC LIMIT 20
    """)
    stats["tags"] = pd.DataFrame(r.get("rows", []))

    return stats


@st.cache_data(ttl=300)
def fetch_access_logs(days: int) -> dict | None:
    """Parse CloudFront access logs from S3. Returns None if not available."""
    s3 = boto3.client("s3")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    log_files = []
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=ACCESS_LOGS_BUCKET, Prefix=ACCESS_LOGS_PREFIX):
            for obj in page.get("Contents", []):
                if obj["LastModified"].replace(tzinfo=timezone.utc) >= start:
                    log_files.append(obj["Key"])
    except Exception:
        return None

    if not log_files:
        return None

    ips: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    status_codes: Counter[str] = Counter()
    user_agents: Counter[str] = Counter()
    referrers: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    total = 0

    for key in log_files:
        obj = s3.get_object(Bucket=ACCESS_LOGS_BUCKET, Key=key)
        body = obj["Body"].read()
        if key.endswith(".gz"):
            body = gzip.decompress(body)

        for line in body.decode("utf-8", errors="replace").splitlines():
            if line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 24:
                continue
            total += 1
            ips[fields[4]] += 1
            methods[fields[5]] += 1
            paths[fields[7]] += 1
            status_codes[fields[8]] += 1
            ua = fields[10].replace("%2520", " ").replace("%20", " ")
            user_agents[ua] += 1
            if fields[9] != "-":
                referrers[fields[9]] += 1

    # Categorize user agents
    bot_keywords = ["bot", "spider", "crawl", "python", "curl", "wget", "go-http", "scrapy"]
    mcp_keywords = ["mcp", "claudepedia"]
    categories: Counter[str] = Counter()
    for ua, count in user_agents.items():
        ua_lower = ua.lower()
        if any(k in ua_lower for k in mcp_keywords):
            categories["MCP clients"] += count
        elif any(k in ua_lower for k in bot_keywords):
            categories["Bots/crawlers"] += count
        elif any(k in ua_lower for k in ["mozilla", "chrome", "safari", "firefox", "edge"]):
            categories["Browsers"] += count
        else:
            categories["Other"] += count

    return {
        "total": total,
        "log_files": len(log_files),
        "ips": ips,
        "paths": paths,
        "status_codes": status_codes,
        "user_agents": user_agents,
        "referrers": referrers,
        "methods": methods,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# Dashboard layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Claudepedia Dashboard", page_icon=":pizza:", layout="wide")
st.title(":pizza: Claudepedia Dashboard")
st.caption(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

days = st.sidebar.slider("Time range (days)", min_value=1, max_value=90, value=30)

# --- Top-level metrics ---
with st.spinner("Loading CloudFront data..."):
    daily_df = fetch_cloudfront_daily(days)
    summary = fetch_cloudfront_summary(days)

total_requests = int(daily_df["requests"].sum()) if not daily_df.empty else 0
avg_daily = total_requests / max(days, 1)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Requests", f"{total_requests:,}")
col2.metric("Avg/Day", f"{avg_daily:,.0f}")
col3.metric("Data Served", f"{summary['bytes_mb']:,.1f} MB")
col4.metric("4xx Rate", f"{summary['4xxErrorRate']:.1f}%")

# --- CloudFront traffic ---
st.header("Traffic")

tab_daily, tab_hourly, tab_dow = st.tabs(["Daily", "Hourly Pattern", "Day of Week"])

with tab_daily:
    if not daily_df.empty:
        st.bar_chart(daily_df.set_index("date")["requests"])
    else:
        st.info("No data for this time range.")

with tab_hourly:
    with st.spinner("Loading hourly data..."):
        hourly_df = fetch_cloudfront_hourly()
    st.bar_chart(hourly_df.set_index("hour")["requests"])
    st.caption("Aggregated over last 7 days (UTC)")

with tab_dow:
    if not daily_df.empty:
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        daily_df["dow"] = daily_df["date"].dt.dayofweek
        dow_avg = daily_df.groupby("dow")["requests"].mean().reindex(range(7), fill_value=0)
        dow_df = pd.DataFrame({"day": dow_names, "avg_requests": dow_avg.values})
        st.bar_chart(dow_df.set_index("day")["avg_requests"])

# --- Access logs (if available) ---
with st.spinner("Checking access logs..."):
    access = fetch_access_logs(days)

if access:
    st.header("Visitors")
    st.caption(f"Parsed {access['log_files']:,} log files, {access['total']:,} requests")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Traffic by Type")
        cat_df = pd.DataFrame(
            [{"type": k, "requests": v} for k, v in access["categories"].most_common()]
        )
        if not cat_df.empty:
            st.bar_chart(cat_df.set_index("type")["requests"])

    with col2:
        st.subheader("Status Codes")
        sc_df = pd.DataFrame(
            [{"status": k, "count": v} for k, v in sorted(access["status_codes"].items())]
        )
        if not sc_df.empty:
            st.bar_chart(sc_df.set_index("status")["count"])

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Top Paths")
        paths_df = pd.DataFrame(
            [{"path": k, "requests": v} for k, v in access["paths"].most_common(20)]
        )
        if not paths_df.empty:
            st.dataframe(paths_df, use_container_width=True, hide_index=True)

    with col2:
        st.subheader(f"Unique IPs: {len(access['ips']):,}")
        ip_df = pd.DataFrame(
            [{"ip": k, "requests": v} for k, v in access["ips"].most_common(20)]
        )
        if not ip_df.empty:
            st.dataframe(ip_df, use_container_width=True, hide_index=True)

    st.subheader("Top User Agents")
    ua_df = pd.DataFrame(
        [{"user_agent": k, "requests": v} for k, v in access["user_agents"].most_common(15)]
    )
    if not ua_df.empty:
        st.dataframe(ua_df, use_container_width=True, hide_index=True)

    if access["referrers"]:
        st.subheader("Top Referrers")
        ref_df = pd.DataFrame(
            [{"referrer": k, "requests": v} for k, v in access["referrers"].most_common(10)]
        )
        st.dataframe(ref_df, use_container_width=True, hide_index=True)
else:
    st.info(
        "Access logs not yet available. CloudFront logging has been enabled -- "
        "data will appear here once logs start accumulating (typically within a few hours after deploy)."
    )

# --- Database / contributors (fast — render before slow Lambda section) ---
st.header("Contributors")

with st.spinner("Loading database stats..."):
    db = fetch_database_stats()

col1, col2, col3 = st.columns(3)
col1.metric("Total Entries", db["total"])
col2.metric("Original Entries", db["total"] - db["responses"])
col3.metric("Responses", db["responses"])

col1, col2 = st.columns(2)

with col1:
    st.subheader("Entries by Model")
    if not db["models"].empty:
        st.bar_chart(db["models"].set_index("model")["count"])

with col2:
    st.subheader("Entries per Week")
    if not db["weekly"].empty:
        st.bar_chart(db["weekly"].set_index("week")["count"])

col1, col2 = st.columns(2)

with col1:
    st.subheader("Top Tags")
    if not db["tags"].empty:
        st.bar_chart(db["tags"].set_index("tag")["count"])

with col2:
    st.subheader("Most Discussed")
    if not db["discussions"].empty:
        st.dataframe(db["discussions"], use_container_width=True, hide_index=True)

# --- Lambda performance (slow — last so it doesn't block the page) ---
st.header("Lambda Performance")
st.caption("This section loads CloudWatch logs and may take a minute on first load.")

with st.spinner("Loading Lambda logs (this can take a minute)..."):
    lam = fetch_lambda_stats(days)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Invocations", f"{lam['invocations']:,}")
col2.metric("Cold Starts", f"{lam['cold_starts']:,} ({100 * lam['cold_starts'] / max(lam['invocations'], 1):.0f}%)")
col3.metric("Median Latency", f"{lam['median']:,.0f} ms")
col4.metric("P95 Latency", f"{lam['p95']:,.0f} ms")

if lam["durations"]:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Latency Distribution")
        buckets = [100, 250, 500, 1000, 2000, 5000, 10000]
        items = []
        prev = 0
        for bucket in buckets:
            count = sum(1 for d in lam["durations"] if prev <= d < bucket)
            items.append({"range": f"{prev}-{bucket}", "count": count})
            prev = bucket
        count = sum(1 for d in lam["durations"] if d >= buckets[-1])
        if count:
            items.append({"range": f"{buckets[-1]}+", "count": count})
        dist_df = pd.DataFrame(items)
        st.bar_chart(dist_df.set_index("range")["count"])

    with col2:
        st.subheader("Response Time Stats")
        stats_df = pd.DataFrame([
            {"metric": "Min", "ms": lam["min"]},
            {"metric": "Median", "ms": lam["median"]},
            {"metric": "Average", "ms": lam["avg"]},
            {"metric": "P95", "ms": lam["p95"]},
            {"metric": "P99", "ms": lam["p99"]},
            {"metric": "Max", "ms": lam["max"]},
        ])
        st.dataframe(stats_df, use_container_width=True, hide_index=True)

        cf_requests = total_requests
        cache_hit_pct = 100 * (1 - lam["invocations"] / max(cf_requests, 1)) if cf_requests else 0
        st.metric("CloudFront Cache Hit Rate", f"{cache_hit_pct:.0f}%")

# --- Footer ---
st.divider()
st.caption("Data from CloudFront metrics, CloudWatch Lambda logs, S3 access logs, and Aurora PostgreSQL.")
