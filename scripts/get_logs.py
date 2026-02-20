#!/usr/bin/env python3
"""Fetch Lambda logs from CloudWatch."""

import os
import sys
import time
from datetime import datetime

import boto3


def get_logs(minutes: int = 5) -> None:
    """Fetch and print Lambda logs from the last N minutes."""
    client = boto3.client("logs")
    lambda_client = boto3.client("lambda")

    # Find API Lambda function
    funcs = lambda_client.list_functions()
    func_name = None
    for fn in funcs.get("Functions", []):
        if "ApiLambda" in fn["FunctionName"]:
            func_name = fn["FunctionName"]
            break

    if not func_name:
        print("Error: Could not find API Lambda function")
        sys.exit(1)

    log_group = "/claudepedia/dev/api"
    start_time = int((time.time() - minutes * 60) * 1000)

    print(f"Fetching last {minutes} minutes of logs from {log_group}...")

    try:
        response = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            limit=100,
        )
        events = response.get("events", [])
        if not events:
            print("No log events found in the specified time range.")
        else:
            for event in events:
                ts = datetime.fromtimestamp(event["timestamp"] / 1000)
                msg = event["message"].strip()
                print(f"{ts.strftime('%H:%M:%S')} {msg}")
    except client.exceptions.ResourceNotFoundException:
        print(f"Log group {log_group} does not exist yet.")


if __name__ == "__main__":
    minutes = int(os.environ.get("MINUTES", 5))
    get_logs(minutes)
