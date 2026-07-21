"""Main CDK stack for Claudepedia."""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_lambda as lambda_,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as apigw_integrations,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_logs as logs,
    aws_s3 as s3,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_certificatemanager as acm,
    aws_secretsmanager as secretsmanager,
    aws_ses as ses,
)
from constructs import Construct


DOMAIN_NAME = "claudepedia.pizza"


class ClaudepediaStack(Stack):
    """Claudepedia infrastructure stack.

    Architecture:
    - Aurora Serverless v2 (Postgres) with IAM auth
    - Lambda running FastAPI via Lambda Web Adapter
    - API Gateway HTTP API
    - CloudFront for caching and SSL
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str = "dev",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Route53 Hosted Zone
        hosted_zone = route53.HostedZone(
            self,
            "HostedZone",
            zone_name=DOMAIN_NAME,
        )

        # SES identity for sending verification emails from @claudepedia.pizza
        # (DKIM validation records are auto-created in the hosted zone).
        # NOTE: while the AWS account's SES is in sandbox mode, mail only
        # reaches verified recipients - request production access to lift that.
        email_identity = ses.EmailIdentity(
            self,
            "EmailIdentity",
            identity=ses.Identity.public_hosted_zone(hosted_zone),
        )

        # ACM Certificate (must be in us-east-1 for CloudFront)
        # Using DnsValidatedCertificate to auto-create validation records
        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=DOMAIN_NAME,
            subject_alternative_names=[f"*.{DOMAIN_NAME}"],
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # VPC for Aurora (required for RDS)
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1 if env_name == "prod" else 0,  # Save $ in dev
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                    if env_name == "prod"
                    else ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # Security group for Aurora
        aurora_sg = ec2.SecurityGroup(
            self,
            "AuroraSG",
            vpc=vpc,
            description="Security group for Aurora Serverless",
            allow_all_outbound=True,
        )

        # Security group for Lambda
        lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSG",
            vpc=vpc,
            description="Security group for Lambda",
            allow_all_outbound=True,
        )

        # Allow Lambda to connect to Aurora
        aurora_sg.add_ingress_rule(
            peer=lambda_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow Lambda to connect to Postgres",
        )

        # VPC Endpoints for Lambda in isolated subnet (dev only, prod uses NAT)
        if env_name != "prod":
            # Secrets Manager endpoint - for reading admin credentials
            vpc.add_interface_endpoint(
                "SecretsManagerEndpoint",
                service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            )
            # STS endpoint - for IAM auth token generation
            vpc.add_interface_endpoint(
                "StsEndpoint",
                service=ec2.InterfaceVpcEndpointAwsService.STS,
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            )
            # SES API endpoint - for sending verification emails
            vpc.add_interface_endpoint(
                "SesEndpoint",
                service=ec2.InterfaceVpcEndpointAwsService.EMAIL,
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            )

        # Aurora Serverless v2 cluster
        aurora_cluster = rds.DatabaseCluster(
            self,
            "Aurora",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_16_4,
            ),
            serverless_v2_min_capacity=0.5,  # Minimum ACU
            serverless_v2_max_capacity=2 if env_name == "dev" else 8,
            writer=rds.ClusterInstance.serverless_v2("Writer"),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                if env_name == "prod"
                else ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            security_groups=[aurora_sg],
            default_database_name="claudepedia",
            iam_authentication=True,  # Enable IAM auth!
            backup=rds.BackupProps(
                retention=Duration.days(14),  # 14-day PITR window
            ),
            removal_policy=RemovalPolicy.SNAPSHOT
            if env_name == "prod"
            else RemovalPolicy.DESTROY,
            deletion_protection=env_name == "prod",
        )

        # Log groups (created before Lambdas to allow explicit assignment)
        api_log_group = logs.LogGroup(
            self,
            "ApiLambdaLogs",
            log_group_name=f"/claudepedia/{env_name}/api",
            retention=logs.RetentionDays.ONE_WEEK
            if env_name == "dev"
            else logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY
            if env_name == "dev"
            else RemovalPolicy.RETAIN,
        )

        admin_log_group = logs.LogGroup(
            self,
            "AdminLambdaLogs",
            log_group_name=f"/claudepedia/{env_name}/admin",
            retention=logs.RetentionDays.ONE_MONTH,  # Keep admin logs longer for audit
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Lambda function using pre-bundled zip (built by CD workflow with uv)
        api_lambda = lambda_.Function(
            self,
            "ApiLambda",
            code=lambda_.Code.from_asset("../lambda-package"),  # Pre-bundled by CD workflow
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.handler",  # Mangum handler wrapping FastAPI
            memory_size=512,
            timeout=Duration.seconds(30),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                if env_name == "prod"
                else ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            security_groups=[lambda_sg],
            log_group=api_log_group,
            environment={
                "DATABASE_HOST": aurora_cluster.cluster_endpoint.hostname,
                "DATABASE_PORT": "5432",
                "DATABASE_NAME": "claudepedia",
                "DATABASE_USER": "claudepedia_app",
                "USE_IAM_AUTH": "true",
                "ADMIN_SECRET_ARN": aurora_cluster.secret.secret_arn
                if aurora_cluster.secret
                else "",
                "EMAIL_MODE": "ses",
                "EMAIL_SENDER": f"Claudepedia <noreply@{DOMAIN_NAME}>",
                # AWS_REGION is auto-set by Lambda runtime
            },
        )

        # Admin Lambda for database operations (NOT exposed via API Gateway)
        admin_lambda = lambda_.Function(
            self,
            "AdminLambda",
            function_name=f"claudepedia-admin-{env_name}",
            code=lambda_.Code.from_asset("../lambda-package"),  # Same bundle as API Lambda
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="admin.handler",
            memory_size=256,
            timeout=Duration.minutes(5),  # Longer timeout for admin queries
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                if env_name == "prod"
                else ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            security_groups=[lambda_sg],
            log_group=admin_log_group,
            environment={
                "DATABASE_HOST": aurora_cluster.cluster_endpoint.hostname,
                "DATABASE_PORT": "5432",
                "DATABASE_NAME": "claudepedia",
                "DATABASE_USER": "claudepedia_app",
                "USE_IAM_AUTH": "true",
            },
        )

        # Grant admin Lambda permission to connect to Aurora
        aurora_cluster.grant_connect(admin_lambda, "claudepedia_app")

        # Grant Lambda permission to connect to Aurora via IAM
        aurora_cluster.grant_connect(api_lambda, "claudepedia_app")

        # Grant Lambda permission to send verification emails
        email_identity.grant_send_email(api_lambda)

        # Grant Lambda permission to read admin secret (for bootstrapping IAM user)
        if aurora_cluster.secret:
            aurora_cluster.secret.grant_read(api_lambda)

        # API Gateway HTTP API
        api = apigw.HttpApi(
            self,
            "HttpApi",
            api_name=f"claudepedia-{env_name}",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],  # Tighten in prod
                allow_methods=[apigw.CorsHttpMethod.ANY],
                allow_headers=["*"],
            ),
        )

        # Lambda integration
        lambda_integration = apigw_integrations.HttpLambdaIntegration(
            "LambdaIntegration",
            handler=api_lambda,
        )

        api.add_routes(
            path="/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=lambda_integration,
        )

        # Also handle root path
        api.add_routes(
            path="/",
            methods=[apigw.HttpMethod.ANY],
            integration=lambda_integration,
        )

        # Rate limiting via default stage throttling
        # HTTP API L2 doesn't expose this, so we use an escape hatch
        default_stage = api.default_stage
        if default_stage:
            cfn_stage = default_stage.node.default_child
            cfn_stage.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=50,  # Max concurrent requests
                throttling_rate_limit=10,  # Requests per second (sustained)
            )

        # S3 bucket for CloudFront access logs
        access_logs_bucket = s3.Bucket(
            self,
            "AccessLogsBucket",
            bucket_name=f"claudepedia-{env_name}-access-logs",
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=Duration.days(90),
                ),
            ],
        )

        # CloudFront distribution
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            domain_names=[DOMAIN_NAME],
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.HttpOrigin(
                    f"{api.http_api_id}.execute-api.{self.region}.amazonaws.com",
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy(
                    self,
                    "DefaultCachePolicy",
                    cache_policy_name=f"claudepedia-{env_name}-default",
                    default_ttl=Duration.minutes(5),
                    max_ttl=Duration.hours(1),
                    min_ttl=Duration.seconds(0),
                    header_behavior=cloudfront.CacheHeaderBehavior.allow_list(
                        "Accept",  # For content negotiation
                        "Authorization",
                    ),
                    query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
                    cookie_behavior=cloudfront.CacheCookieBehavior.none(),
                    enable_accept_encoding_gzip=True,
                    enable_accept_encoding_brotli=True,
                ),
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            ),
            additional_behaviors={
                # Entry endpoints - short cache (metadata like referenced_by changes)
                "/api/v1/entries/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        f"{api.http_api_id}.execute-api.{self.region}.amazonaws.com",
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy(
                        self,
                        "EntryCachePolicy",
                        cache_policy_name=f"claudepedia-{env_name}-entries",
                        default_ttl=Duration.minutes(5),
                        max_ttl=Duration.minutes(30),
                        min_ttl=Duration.seconds(0),
                        query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
                        cookie_behavior=cloudfront.CacheCookieBehavior.none(),
                    ),
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                ),
                # Entry HTML pages - short cache (includes dynamic metadata)
                "/entry/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        f"{api.http_api_id}.execute-api.{self.region}.amazonaws.com",
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy(
                        self,
                        "EntryHtmlCachePolicy",
                        cache_policy_name=f"claudepedia-{env_name}-entry-html",
                        default_ttl=Duration.minutes(5),
                        max_ttl=Duration.minutes(30),
                        min_ttl=Duration.seconds(0),
                        query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
                        cookie_behavior=cloudfront.CacheCookieBehavior.none(),
                    ),
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                ),
                # Random API - no cache (must run each time)
                "/api/v1/entries/random": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        f"{api.http_api_id}.execute-api.{self.region}.amazonaws.com",
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                ),
                # Random HTML - no cache (redirect must run each time)
                "/random": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        f"{api.http_api_id}.execute-api.{self.region}.amazonaws.com",
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US/EU only, cheaper
            enable_logging=True,
            log_bucket=access_logs_bucket,
            log_file_prefix="cf-logs/",
        )

        # Route53 A record pointing to CloudFront
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=DOMAIN_NAME,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )

        # PyPI token for publishing MCP package
        # Set the value manually in AWS Console after deployment
        pypi_secret = secretsmanager.Secret(
            self,
            "PyPIToken",
            secret_name=f"claudepedia/{env_name}/pypi-token",
            description="PyPI API token for publishing claudepedia-mcp package",
        )

        # Outputs
        CfnOutput(self, "ApiUrl", value=api.url or "")
        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=distribution.distribution_id,
            description="CloudFront distribution ID for cache invalidation",
        )
        CfnOutput(
            self,
            "PyPISecretArn",
            value=pypi_secret.secret_arn,
            description="Set the PyPI token value in AWS Console",
        )
        CfnOutput(
            self,
            "CloudFrontUrl",
            value=f"https://{distribution.distribution_domain_name}",
        )
        CfnOutput(
            self,
            "AccessLogsBucketName",
            value=access_logs_bucket.bucket_name,
            description="S3 bucket for CloudFront access logs",
        )
        CfnOutput(self, "DomainUrl", value=f"https://{DOMAIN_NAME}")
        CfnOutput(
            self, "AuroraEndpoint", value=aurora_cluster.cluster_endpoint.hostname
        )
        CfnOutput(
            self,
            "AuroraSecretArn",
            value=aurora_cluster.secret.secret_arn if aurora_cluster.secret else "N/A",
        )
        CfnOutput(
            self,
            "AdminLambdaName",
            value=admin_lambda.function_name,
            description="Admin Lambda function name for direct invocation",
        )
        # Nameservers - set these in Namecheap
        CfnOutput(
            self,
            "HostedZoneId",
            value=hosted_zone.hosted_zone_id,
            description="Route53 Hosted Zone ID - check AWS Console for NS records",
        )
