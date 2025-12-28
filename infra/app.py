#!/usr/bin/env python3
"""CDK app entry point for Claudepedia."""

import os

import aws_cdk as cdk

from stack import ClaudepediaStack


app = cdk.App()

# Get environment from context or default
env_name = app.node.try_get_context("env") or "dev"

ClaudepediaStack(
    app,
    f"Claudepedia-{env_name.capitalize()}",
    env_name=env_name,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
