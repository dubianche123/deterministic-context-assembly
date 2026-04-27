#!/usr/bin/env python3
import json
import subprocess
import uuid
import os
import sys

BUCKET_NAME = "leo-norn-machine-test-picture-726725835094-ap-northeast-1-an"
REGION = "ap-northeast-1"
ACCOUNT_ID = "726725835094"
ALTERNATE_DOMAIN = os.environ.get("CLOUDFRONT_ALIAS", "").strip().lower()
ACM_CERT_ARN = os.environ.get("CLOUDFRONT_CERT_ARN", "").strip()

if ACM_CERT_ARN and not ALTERNATE_DOMAIN:
    raise ValueError("CLOUDFRONT_CERT_ARN 需要同时配置 CLOUDFRONT_ALIAS 才能生效。")

def run_cmd(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)

def main():
    print(f"🚀 [1/4] 开始为图片存储桶 {BUCKET_NAME} 配置 CloudFront + OAC...")
    origin_domain = f"{BUCKET_NAME}.s3.{REGION}.amazonaws.com"
    try:
        res = run_cmd(["aws", "cloudfront", "list-distributions", "--output", "json"])
        distributions = json.loads(res.stdout).get("DistributionList", {}).get("Items", [])
        for dist in distributions:
            origins = dist.get("Origins", {}).get("Items", [])
            if any(origin.get("DomainName") == origin_domain for origin in origins):
                print(f"   → 图片 CDN 已存在: https://{dist['DomainName']}")
                print(f"   → Distribution ID: {dist['Id']}")
                sys.exit(0)
    except subprocess.CalledProcessError:
        pass

    # 1. 创建或获取 Origin Access Control (OAC)
    print("📦 [2/4] 正在创建 Origin Access Control (OAC)...")
    oac_name = f"OAC-{BUCKET_NAME}"
    oac_id = None

    try:
        res = run_cmd(["aws", "cloudfront", "list-origin-access-controls", "--output", "json"])
        if res.stdout.strip():
            oacs = json.loads(res.stdout).get("OriginAccessControlList", {}).get("Items", [])
            for oac in oacs:
                if oac["Name"] == oac_name:
                    oac_id = oac["Id"]
                    break
    except subprocess.CalledProcessError:
        pass

    if not oac_id:
        oac_config = {
            "Name": oac_name,
            "Description": "OAC for Norn Machine Images",
            "OriginAccessControlOriginType": "s3",
            "SigningBehavior": "always",
            "SigningProtocol": "sigv4"
        }
        with open("oac_config.json", "w") as f:
            json.dump(oac_config, f)
        
        res = run_cmd(["aws", "cloudfront", "create-origin-access-control", "--origin-access-control-config", "file://oac_config.json", "--output", "json"])
        oac_id = json.loads(res.stdout)["OriginAccessControl"]["Id"]
        os.remove("oac_config.json")

    print(f"   → OAC ID: {oac_id}")

    # 2. 创建 CloudFront Distribution
    print("🌐 [3/4] 正在创建 CloudFront 分发节点 (AWS 可能会花一分钟处理)...")
    dist_config = {
        "CallerReference": str(uuid.uuid4()),
        "Comment": "Distribution for Norn Machine Images",
        "Enabled": True,
        "Aliases": {
            "Quantity": 1 if ALTERNATE_DOMAIN else 0,
            "Items": [ALTERNATE_DOMAIN] if ALTERNATE_DOMAIN else [],
        },
        "Origins": {
            "Quantity": 1,
            "Items": [
                {
                    "Id": f"S3-{BUCKET_NAME}",
                    "DomainName": f"{BUCKET_NAME}.s3.{REGION}.amazonaws.com",
                    "OriginPath": "",
                    "CustomHeaders": {"Quantity": 0},
                    "S3OriginConfig": {"OriginAccessIdentity": ""},
                    "OriginAccessControlId": oac_id
                }
            ]
        },
        "DefaultCacheBehavior": {
            "TargetOriginId": f"S3-{BUCKET_NAME}",
            "TrustedSigners": {"Enabled": False, "Quantity": 0},
            "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
            "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {
                "Quantity": 2,
                "Items": ["HEAD", "GET"],
                "CachedMethods": {"Quantity": 2, "Items": ["HEAD", "GET"]}
            },
            "SmoothStreaming": False,
            "Compress": True,
            "ForwardedValues": {
                "QueryString": False,
                "Cookies": {"Forward": "none"},
                "Headers": {"Quantity": 3, "Items": ["Origin", "Access-Control-Request-Headers", "Access-Control-Request-Method"]}
            },
            "MinTTL": 0,
            "DefaultTTL": 86400,
            "MaxTTL": 31536000
        },
        "PriceClass": "PriceClass_All",
        "IsIPV6Enabled": True,
        "ViewerCertificate": {
            "CloudFrontDefaultCertificate": True,
        } if not ACM_CERT_ARN else {
            "ACMCertificateArn": ACM_CERT_ARN,
            "SSLSupportMethod": "sni-only",
            "MinimumProtocolVersion": "TLSv1.2_2021",
            "CloudFrontDefaultCertificate": False,
        }
    }

    with open("dist_config.json", "w") as f:
        json.dump(dist_config, f)

    res = run_cmd(["aws", "cloudfront", "create-distribution", "--distribution-config", "file://dist_config.json", "--output", "json"])
    dist_data = json.loads(res.stdout)["Distribution"]
    dist_id = dist_data["Id"]
    dist_domain = dist_data["DomainName"]
    os.remove("dist_config.json")

    print(f"   → Distribution ID: {dist_id}")
    print(f"   → 你的专属 CDN 域名: {dist_domain}")

    # 3. 更新 S3 Bucket Policy (只允许 CloudFront 访问)
    print("🔒 [4/4] 正在配置 S3 Bucket Policy，应用极致的 Least Privilege...")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontServicePrincipalReadOnly",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET_NAME}/*",
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceArn": f"arn:aws:cloudfront::{ACCOUNT_ID}:distribution/{dist_id}"
                    }
                }
            }
        ]
    }

    with open("bucket_policy.json", "w") as f:
        json.dump(policy, f)

    run_cmd(["aws", "s3api", "put-bucket-policy", "--bucket", BUCKET_NAME, "--region", REGION, "--policy", "file://bucket_policy.json"])
    os.remove("bucket_policy.json")
    print("   → S3 Policy 配置成功，现在除了 CDN，任何人都无法直接读取你的 Bucket。")

    # 4. 自动修改前端代码的 JSON 数据
    print("\n📝 正在更新前端代码中的图片链接...")
    cards_path = "/Users/dubianche/Documents/New project/the-norn-machine/frontend/data/cards.json"
    if os.path.exists(cards_path):
        with open(cards_path, "r") as f:
            cards = json.load(f)

        for card in cards:
            old_url = card["url"]
            filename = old_url.split("/")[-1]
            card["url"] = f"https://{dist_domain}/images/{filename}"

        with open(cards_path, "w") as f:
            json.dump(cards, f, ensure_ascii=False, indent=2)
        print(f"   → 成功将 {len(cards)} 张图片的链接替换为了最新的 CDN 域名。")
    else:
        print(f"   → 警告: 未找到 {cards_path}，请手动更新链接。")

    print("\n🎉 AWS CloudFront + OAC 部署大功告成！")
    print("---------------------------------------------------------")
    print("💡 接下来请进入 frontend/ 文件夹，重新运行一次 bash deploy_to_s3.sh，")
    print("   将替换了全新 CDN 链接的前端代码部署上线。")
    print("   (注: 新建的 CDN 节点可能需要 1~3 分钟全网生效，期间图片加载如果稍慢是正常的)")

if __name__ == "__main__":
    main()
