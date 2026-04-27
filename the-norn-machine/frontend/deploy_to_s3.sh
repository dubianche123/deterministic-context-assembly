#!/bin/bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
# The Norn Machine — S3 Static Website Deployment
# ═══════════════════════════════════════════════════════════

BUCKET_NAME="norn-machine-frontend-726725835094-ap-northeast-1"
REGION="ap-northeast-1"
ACCOUNT_ID="726725835094"
FRONTEND_DISTRIBUTION_ID="E7VCP8VRJBJOB"
FRONTEND_CLOUDFRONT_DOMAIN="d3gncg0hircdt9.cloudfront.net"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  ✦ The Norn Machine — Deploying to AWS S3"
echo "  ═══════════════════════════════════════════"
echo "  Bucket : ${BUCKET_NAME}"
echo "  HTTPS  : https://${FRONTEND_CLOUDFRONT_DOMAIN}"
echo "  Region : ${REGION}"
echo ""

# ─── 1. Ensure Bucket Exists ─────────────────────────────
echo "📦 [1/6] Checking S3 Bucket..."
if aws s3api head-bucket --bucket "${BUCKET_NAME}" --region "${REGION}" 2>/dev/null; then
  echo "   → Bucket exists."
else
  aws s3 mb "s3://${BUCKET_NAME}" --region "${REGION}"
  echo "   → Bucket created."
fi

# ─── 2. Lock Bucket To CloudFront ────────────────────────
echo "🔒 [2/6] Locking direct S3 access behind CloudFront..."
aws s3api put-public-access-block \
  --bucket "${BUCKET_NAME}" \
  --region "${REGION}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipalReadOnly",
      "Effect": "Allow",
      "Principal": {"Service": "cloudfront.amazonaws.com"},
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${FRONTEND_DISTRIBUTION_ID}"
        }
      }
    }
  ]
}
EOF
)
aws s3api put-bucket-policy \
  --bucket "${BUCKET_NAME}" \
  --region "${REGION}" \
  --policy "${POLICY}"
aws s3api delete-bucket-website \
  --bucket "${BUCKET_NAME}" \
  --region "${REGION}" >/dev/null 2>&1 || true
echo "   → Direct S3 website endpoint disabled; CloudFront remains the public entry."

# ─── 3. Sync Files ───────────────────────────────────────
echo "🚀 [3/6] Uploading frontend files..."
# runtime-config.js is owned by backend deployment and should be preserved here.
aws s3 sync "${SCRIPT_DIR}/" "s3://${BUCKET_NAME}/" \
  --region "${REGION}" \
  --exclude "deploy_to_s3.sh" \
  --exclude ".DS_Store" \
  --exclude "runtime-config.js" \
  --exclude "*.sh" \
  --cache-control "max-age=300" \
  --content-type "text/html" \
  --exclude "*" --include "*.html"

# runtime-config.js is owned by backend deployment and should be preserved here.
aws s3 sync "${SCRIPT_DIR}/" "s3://${BUCKET_NAME}/" \
  --region "${REGION}" \
  --exclude "deploy_to_s3.sh" \
  --exclude ".DS_Store" \
  --exclude "runtime-config.js" \
  --exclude "*.sh" \
  --exclude "*.html" \
  --cache-control "max-age=3600"

echo "   → Files uploaded."

# ─── 4. Configure CORS on Image Bucket ──────────────────
echo "🔗 [4/6] Configuring CORS on image bucket..."
IMAGE_BUCKET="leo-norn-machine-test-picture-726725835094-ap-northeast-1-an"
WEBSITE_URL="https://${FRONTEND_CLOUDFRONT_DOMAIN}"

CORS_CONFIG=$(cat <<EOF
{
  "CORSRules": [
    {
      "AllowedHeaders": ["*"],
      "AllowedMethods": ["GET"],
      "AllowedOrigins": ["${WEBSITE_URL}", "*"],
      "ExposeHeaders": [],
      "MaxAgeSeconds": 86400
    }
  ]
}
EOF
)
aws s3api put-bucket-cors \
  --bucket "${IMAGE_BUCKET}" \
  --region "${REGION}" \
  --cors-configuration "${CORS_CONFIG}"
echo "   → CORS configured."

# ─── 5. Refresh CloudFront ───────────────────────────────
echo "♻ [5/6] Refreshing CloudFront cache..."
aws cloudfront create-invalidation \
  --distribution-id "${FRONTEND_DISTRIBUTION_ID}" \
  --paths "/index.html" "/app.js" "/style.css" "/data/*" "/Drifting_Near_the_Core.mp3" > /dev/null
echo "   → Invalidation submitted."

# ─── Done ────────────────────────────────────────────────
echo ""
echo "  ═══════════════════════════════════════════"
echo "  ✦ Deployment Complete!"
echo ""
echo "  🌐 HTTPS Website URL:"
echo "  https://${FRONTEND_CLOUDFRONT_DOMAIN}"
echo ""
echo "  ═══════════════════════════════════════════"
echo ""
