import boto3
import time
import os
import sys

cloudfront = boto3.client('cloudfront')
s3 = boto3.client('s3')

BUCKET_NAME = "norn-machine-frontend-726725835094-ap-northeast-1"
REGION = "ap-northeast-1"
ALTERNATE_DOMAIN = os.environ.get("CLOUDFRONT_ALIAS", "").strip().lower()
ACM_CERT_ARN = os.environ.get("CLOUDFRONT_CERT_ARN", "").strip()

if ACM_CERT_ARN and not ALTERNATE_DOMAIN:
    raise ValueError("CLOUDFRONT_CERT_ARN 需要同时配置 CLOUDFRONT_ALIAS 才能生效。")

origin_domain = f'{BUCKET_NAME}.s3.{REGION}.amazonaws.com'
dist_list = cloudfront.list_distributions().get('DistributionList', {}).get('Items', [])
for dist in dist_list:
    origins = dist.get('Origins', {}).get('Items', [])
    if any(origin.get('DomainName') == origin_domain for origin in origins):
        print(f"Frontend distribution already exists: https://{dist['DomainName']}")
        print(f"Distribution ID: {dist['Id']}")
        sys.exit(0)

# Create OAC
try:
    oac_resp = cloudfront.create_origin_access_control(
        OriginAccessControlConfig={
            'Name': f'{BUCKET_NAME}-oac',
            'Description': 'OAC for frontend',
            'SigningProtocol': 'sigv4',
            'SigningBehavior': 'always',
            'OriginAccessControlOriginType': 's3'
        }
    )
    oac_id = oac_resp['OriginAccessControl']['Id']
except Exception as e:
    if 'OriginAccessControlAlreadyExists' in str(e):
        resp = cloudfront.list_origin_access_controls()
        oac_id = next(item['Id'] for item in resp['OriginAccessControlList']['Items'] if item['Name'] == f'{BUCKET_NAME}-oac')
    else:
        raise

# Create Distribution
print("Creating distribution...")
aliases = {'Quantity': 1, 'Items': [ALTERNATE_DOMAIN]} if ALTERNATE_DOMAIN else {'Quantity': 0}
viewer_certificate = {'CloudFrontDefaultCertificate': True}
if ACM_CERT_ARN:
    viewer_certificate = {
        'ACMCertificateArn': ACM_CERT_ARN,
        'SSLSupportMethod': 'sni-only',
        'MinimumProtocolVersion': 'TLSv1.2_2021',
        'CloudFrontDefaultCertificate': False,
    }

dist_resp = cloudfront.create_distribution(
    DistributionConfig={
        'CallerReference': str(time.time()),
        'Aliases': aliases,
        'DefaultRootObject': 'index.html',
        'Origins': {
            'Quantity': 1,
            'Items': [{
                'Id': 'frontend-s3',
                'DomainName': f'{BUCKET_NAME}.s3.{REGION}.amazonaws.com',
                'OriginAccessControlId': oac_id,
                'S3OriginConfig': {'OriginAccessIdentity': ''}
            }]
        },
        'DefaultCacheBehavior': {
            'TargetOriginId': 'frontend-s3',
            'ViewerProtocolPolicy': 'redirect-to-https',
            'TrustedSigners': {'Enabled': False, 'Quantity': 0},
            'TrustedKeyGroups': {'Enabled': False, 'Quantity': 0},
            'AllowedMethods': {'Quantity': 2, 'Items': ['HEAD', 'GET'], 'CachedMethods': {'Quantity': 2, 'Items': ['HEAD', 'GET']}},
            'ForwardedValues': {
                'QueryString': False,
                'Cookies': {'Forward': 'none'}
            },
            'MinTTL': 0,
            'DefaultTTL': 3600,
            'MaxTTL': 86400
        },
        'Comment': 'Norn Machine Frontend',
        'Enabled': True,
        'HttpVersion': 'http2',
        'IsIPV6Enabled': True,
        'ViewerCertificate': viewer_certificate,
    }
)

domain = dist_resp['Distribution']['DomainName']
dist_id = dist_resp['Distribution']['Id']
arn = dist_resp['Distribution']['ARN']
print(f"Distribution created: {domain}")
if ALTERNATE_DOMAIN:
    print(f"Custom domain configured: {ALTERNATE_DOMAIN}")

# Update S3 Policy
policy = f'''{{
    "Version": "2012-10-17",
    "Statement": [
        {{
            "Effect": "Allow",
            "Principal": {{"Service": "cloudfront.amazonaws.com"}},
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::{BUCKET_NAME}/*",
            "Condition": {{
                "StringEquals": {{"AWS:SourceArn": "{arn}"}}
            }}
        }}
    ]
}}'''
s3.put_bucket_policy(Bucket=BUCKET_NAME, Policy=policy)
print("S3 Policy updated for OAC.")
