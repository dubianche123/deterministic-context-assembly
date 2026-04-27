#!/bin/bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
# The Norn Machine — Backend IaC Deployment
# Creates: IAM Role + Lambda + API Gateway + API Key + Guardrails
# ═══════════════════════════════════════════════════════════

REGION="ap-northeast-1"
ACCOUNT_ID="726725835094"
FUNCTION_NAME="norn-machine-analyze"
ROLE_NAME="norn-machine-lambda-role"
API_NAME="NornMachineAPI"
STAGE_NAME="prod"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="${PROJECT_DIR}/backend"
FRONTEND_DIR="${PROJECT_DIR}/frontend"
FRONTEND_BUILD_DIR=$(mktemp -d "${PROJECT_DIR}/frontend_build_XXXXXX")
FRONTEND_BUCKET="norn-machine-frontend-726725835094-ap-northeast-1"
FRONTEND_CLOUDFRONT_DOMAIN="d3gncg0hircdt9.cloudfront.net"
FRONTEND_DISTRIBUTION_ID="E7VCP8VRJBJOB"

cleanup() {
  rm -rf "${FRONTEND_BUILD_DIR}"
  rm -f "${PROJECT_DIR}/lambda_package.zip"
}
trap cleanup EXIT

echo ""
echo "  ✦ The Norn Machine — Backend Deployment"
echo "  ═══════════════════════════════════════════"
echo ""

# ─── 1. Create IAM Role ─────────────────────────────────
echo "🔑 [1/7] Creating IAM Role..."

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

ROLE_ARN=""
if aws iam get-role --role-name "${ROLE_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  ROLE_ARN=$(aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.Arn' --output text)
  echo "   → Role already exists: ${ROLE_ARN}"
else
  ROLE_ARN=$(aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --query 'Role.Arn' --output text)
  echo "   → Role created: ${ROLE_ARN}"

  # Attach policies
  aws iam attach-role-policy --role-name "${ROLE_NAME}" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

  # Inline policy for Bedrock
  BEDROCK_POLICY='{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ApplyGuardrail"
      ],
      "Resource": "*"
    }]
  }'
  aws iam put-role-policy --role-name "${ROLE_NAME}" \
    --policy-name "BedrockInvokeAccess" \
    --policy-document "${BEDROCK_POLICY}"

  echo "   → Policies attached. Waiting 10s for IAM propagation..."
  sleep 10
fi

# ─── 2. Package Lambda ──────────────────────────────────
echo "📦 [2/7] Packaging Lambda function..."

PACKAGE_DIR=$(mktemp -d "${PROJECT_DIR}/lambda_pkg_XXXXXX")
cp "${BACKEND_DIR}/lambda_function.py" "${PACKAGE_DIR}/"
cp "${BACKEND_DIR}/fast_thinker.py" "${PACKAGE_DIR}/"
cp "${BACKEND_DIR}/template_router.py" "${PACKAGE_DIR}/"
cp "${BACKEND_DIR}/slow_thinker.py" "${PACKAGE_DIR}/"
cp "${BACKEND_DIR}/dialogue_thinker.py" "${PACKAGE_DIR}/"
cp -r "${BACKEND_DIR}/config" "${PACKAGE_DIR}/"

cd "${PACKAGE_DIR}"
zip -r9 "${PROJECT_DIR}/lambda_package.zip" . -x "*.pyc" "__pycache__/*" > /dev/null
cd "${PROJECT_DIR}"
rm -rf "${PACKAGE_DIR}"
echo "   → Package created: lambda_package.zip"

# ─── 3. Deploy Lambda ───────────────────────────────────
echo "⚡ [3/7] Deploying Lambda function..."

if aws lambda get-function --function-name "${FUNCTION_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "${FUNCTION_NAME}" \
    --zip-file "fileb://${PROJECT_DIR}/lambda_package.zip" \
    --region "${REGION}" > /dev/null
  echo "   → Lambda updated."
else
  aws lambda create-function \
    --function-name "${FUNCTION_NAME}" \
    --runtime "python3.12" \
    --role "${ROLE_ARN}" \
    --handler "lambda_function.handler" \
    --zip-file "fileb://${PROJECT_DIR}/lambda_package.zip" \
    --timeout 30 \
    --memory-size 256 \
    --region "${REGION}" > /dev/null
  echo "   → Lambda created."
fi

LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

# ─── 4. Create API Gateway ──────────────────────────────
echo "🌐 [4/7] Creating API Gateway..."

# Check if API already exists
API_ID=$(aws apigateway get-rest-apis --region "${REGION}" \
  --query "items[?name=='${API_NAME}'].id" --output text 2>/dev/null || echo "")

if [ -z "${API_ID}" ] || [ "${API_ID}" = "None" ]; then
  API_ID=$(aws apigateway create-rest-api \
    --name "${API_NAME}" \
    --description "The Norn Machine Backend API" \
    --endpoint-configuration types=REGIONAL \
    --region "${REGION}" \
    --query 'id' --output text)
  echo "   → API created: ${API_ID}"
else
  echo "   → API already exists: ${API_ID}"
fi

# Get root resource id
ROOT_ID=$(aws apigateway get-resources --rest-api-id "${API_ID}" --region "${REGION}" \
  --query 'items[?path==`/`].id' --output text)

# Create /analyze resource (if not exists)
ANALYZE_ID=$(aws apigateway get-resources --rest-api-id "${API_ID}" --region "${REGION}" \
  --query 'items[?path==`/analyze`].id' --output text 2>/dev/null || echo "")

if [ -z "${ANALYZE_ID}" ] || [ "${ANALYZE_ID}" = "None" ]; then
  ANALYZE_ID=$(aws apigateway create-resource \
    --rest-api-id "${API_ID}" \
    --parent-id "${ROOT_ID}" \
    --path-part "analyze" \
    --region "${REGION}" \
    --query 'id' --output text)
fi

# POST method
aws apigateway put-method \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method POST \
  --authorization-type NONE \
  --api-key-required \
  --region "${REGION}" > /dev/null 2>&1 || true

# POST integration → Lambda
aws apigateway put-integration \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${LAMBDA_ARN}/invocations" \
  --region "${REGION}" > /dev/null

# OPTIONS method for CORS
aws apigateway put-method \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method OPTIONS \
  --authorization-type NONE \
  --region "${REGION}" > /dev/null 2>&1 || true

aws apigateway put-integration \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method OPTIONS \
  --type MOCK \
  --request-templates '{"application/json": "{\"statusCode\": 200}"}' \
  --region "${REGION}" > /dev/null

aws apigateway put-method-response \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method OPTIONS \
  --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":false,"method.response.header.Access-Control-Allow-Methods":false,"method.response.header.Access-Control-Allow-Origin":false}' \
  --region "${REGION}" > /dev/null 2>&1 || true

aws apigateway put-integration-response \
  --rest-api-id "${API_ID}" \
  --resource-id "${ANALYZE_ID}" \
  --http-method OPTIONS \
  --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":"'"'"'Content-Type,X-Api-Key'"'"'","method.response.header.Access-Control-Allow-Methods":"'"'"'POST,OPTIONS'"'"'","method.response.header.Access-Control-Allow-Origin":"'"'"'*'"'"'"}' \
  --region "${REGION}" > /dev/null 2>&1 || true

# Create /dialogue resource (if not exists)
DIALOGUE_ID=$(aws apigateway get-resources --rest-api-id "${API_ID}" --region "${REGION}" \
  --query 'items[?path==`/dialogue`].id' --output text 2>/dev/null || echo "")

if [ -z "${DIALOGUE_ID}" ] || [ "${DIALOGUE_ID}" = "None" ]; then
  DIALOGUE_ID=$(aws apigateway create-resource \
    --rest-api-id "${API_ID}" \
    --parent-id "${ROOT_ID}" \
    --path-part "dialogue" \
    --region "${REGION}" \
    --query 'id' --output text)
fi

aws apigateway put-method \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method POST \
  --authorization-type NONE \
  --api-key-required \
  --region "${REGION}" > /dev/null 2>&1 || true

aws apigateway put-integration \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${LAMBDA_ARN}/invocations" \
  --region "${REGION}" > /dev/null

aws apigateway put-method \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method OPTIONS \
  --authorization-type NONE \
  --region "${REGION}" > /dev/null 2>&1 || true

aws apigateway put-integration \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method OPTIONS \
  --type MOCK \
  --request-templates '{"application/json": "{\"statusCode\": 200}"}' \
  --region "${REGION}" > /dev/null

aws apigateway put-method-response \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method OPTIONS \
  --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":false,"method.response.header.Access-Control-Allow-Methods":false,"method.response.header.Access-Control-Allow-Origin":false}' \
  --region "${REGION}" > /dev/null 2>&1 || true

aws apigateway put-integration-response \
  --rest-api-id "${API_ID}" \
  --resource-id "${DIALOGUE_ID}" \
  --http-method OPTIONS \
  --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":"'"'"'Content-Type,X-Api-Key'"'"'","method.response.header.Access-Control-Allow-Methods":"'"'"'POST,OPTIONS'"'"'","method.response.header.Access-Control-Allow-Origin":"'"'"'*'"'"'"}' \
  --region "${REGION}" > /dev/null 2>&1 || true

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name "${FUNCTION_NAME}" \
  --statement-id "apigateway-invoke-$(date +%s)" \
  --action "lambda:InvokeFunction" \
  --principal "apigateway.amazonaws.com" \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*" \
  --region "${REGION}" > /dev/null 2>&1 || true

echo "   → API Gateway configured."

# ─── 5. Create API Key + Usage Plan ─────────────────────
echo "🔐 [5/7] Creating API Key + Usage Plan..."

# Create usage plan
PLAN_ID=$(aws apigateway get-usage-plans --region "${REGION}" \
  --query "items[?name=='NornMachinePlan'].id" --output text 2>/dev/null || echo "")

if [ -z "${PLAN_ID}" ] || [ "${PLAN_ID}" = "None" ]; then
  # Deploy API first (needed for usage plan)
  aws apigateway create-deployment \
    --rest-api-id "${API_ID}" \
    --stage-name "${STAGE_NAME}" \
    --region "${REGION}" > /dev/null

  PLAN_ID=$(aws apigateway create-usage-plan \
    --name "NornMachinePlan" \
    --throttle burstLimit=10,rateLimit=5 \
    --quota limit=500,period=DAY \
    --api-stages apiId="${API_ID}",stage="${STAGE_NAME}" \
    --region "${REGION}" \
    --query 'id' --output text)
  echo "   → Usage plan created: ${PLAN_ID}"
else
  # Just redeploy
  aws apigateway create-deployment \
    --rest-api-id "${API_ID}" \
    --stage-name "${STAGE_NAME}" \
    --region "${REGION}" > /dev/null
  echo "   → Usage plan exists: ${PLAN_ID}"
fi

# Create API Key
KEY_ID=$(aws apigateway get-api-keys --region "${REGION}" \
  --query "items[?name=='NornMachineKey'].id" --output text 2>/dev/null || echo "")

API_KEY_VALUE=""
if [ -z "${KEY_ID}" ] || [ "${KEY_ID}" = "None" ]; then
  KEY_RESULT=$(aws apigateway create-api-key \
    --name "NornMachineKey" \
    --enabled \
    --region "${REGION}" \
    --output json)
  KEY_ID=$(echo "${KEY_RESULT}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  API_KEY_VALUE=$(echo "${KEY_RESULT}" | python3 -c "import sys,json; print(json.load(sys.stdin)['value'])")

  aws apigateway create-usage-plan-key \
    --usage-plan-id "${PLAN_ID}" \
    --key-id "${KEY_ID}" \
    --key-type "API_KEY" \
    --region "${REGION}" > /dev/null
  echo "   → API Key created."
else
  API_KEY_VALUE=$(aws apigateway get-api-key --api-key "${KEY_ID}" --include-value \
    --region "${REGION}" --query 'value' --output text)
  echo "   → API Key exists."
fi

API_ENDPOINT="https://${API_ID}.execute-api.${REGION}.amazonaws.com/${STAGE_NAME}"

# ─── 6. Build Frontend Runtime Config ───────────────
echo "📝 [6/7] Preparing frontend runtime config..."

rsync -a \
  --exclude "deploy_to_s3.sh" \
  --exclude ".DS_Store" \
  --exclude "*.sh" \
  "${FRONTEND_DIR}/" "${FRONTEND_BUILD_DIR}/"

# Keep the live endpoint and API key out of the checked-in frontend source.
export API_ENDPOINT API_KEY_VALUE FRONTEND_BUILD_DIR
python3 - <<'PY'
import json
import os
from pathlib import Path

build_dir = Path(os.environ["FRONTEND_BUILD_DIR"])
payload = {
    "apiEndpoint": os.environ["API_ENDPOINT"],
    "apiKey": os.environ["API_KEY_VALUE"],
}
build_dir.joinpath("runtime-config.js").write_text(
    "window.__NORN_CONFIG__ = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
    encoding="utf-8",
)
print("   → runtime-config.js generated.")
PY

# Redeploy frontend to S3
aws s3 sync "${FRONTEND_BUILD_DIR}/" "s3://${FRONTEND_BUCKET}/" \
  --region "${REGION}" \
  --exclude "deploy_to_s3.sh" --exclude ".DS_Store" --exclude "*.sh" > /dev/null
echo "   → Frontend redeployed to S3."

aws cloudfront create-invalidation \
  --distribution-id "${FRONTEND_DISTRIBUTION_ID}" \
  --paths "/index.html" "/app.js" "/runtime-config.js" "/style.css" "/data/*" "/Drifting_Near_the_Core.mp3" > /dev/null
echo "   → CloudFront invalidation submitted."

# ─── 7. Summary ─────────────────────────────────────────
echo ""
echo "  ═══════════════════════════════════════════"
echo "  ✦ Backend Deployment Complete!"
echo ""
echo "  🔗 API Endpoint : ${API_ENDPOINT}/analyze"
echo "  🔑 API Key      : [written to runtime-config.js]"
echo "  ⚡ Lambda       : ${FUNCTION_NAME}"
echo "  🌐 Frontend     : https://${FRONTEND_CLOUDFRONT_DOMAIN}"
echo ""
echo "  ═══════════════════════════════════════════"
echo ""

# Clean up
rm -f "${PROJECT_DIR}/lambda_package.zip"
rm -rf "${FRONTEND_BUILD_DIR}"
