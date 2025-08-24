#!/bin/bash

# Tailscale ACL Import Script
# This script imports existing Tailscale ACL configuration into Terraform

set -e

echo "Tailscale ACL Import Script"
echo "=========================="

# Check if required tools are installed
echo "1. Checking prerequisites..."
echo "============================"

if [ -z "$TAILSCALE_API_KEY" ]; then
    echo "❌ TAILSCALE_API_KEY is not set"
    echo "   Please set it with: export TAILSCALE_API_KEY='your-api-key'"
    exit 1
else
    echo "✅ TAILSCALE_API_KEY is set"
fi

if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    echo "❌ AWS credentials are not set (required for S3 backend)"
    echo "   Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
    exit 1
else
    echo "✅ AWS credentials are set"
fi

if ! command -v terraform &> /dev/null; then
    echo "❌ Terraform is not installed"
    exit 1
else
    TERRAFORM_VERSION=$(terraform version -json | jq -r '.terraform_version')
    echo "✅ Terraform is installed (version: $TERRAFORM_VERSION)"
fi

if ! command -v curl &> /dev/null; then
    echo "❌ curl is not installed"
    exit 1
else
    echo "✅ curl is installed"
fi

echo ""

# Test Tailscale API connectivity
echo "2. Testing Tailscale API connectivity..."
echo "========================================"

echo "Testing API connection..."
API_RESPONSE=$(curl -s -w "%{http_code}" -H "Authorization: Bearer $TAILSCALE_API_KEY" \
    "https://api.tailscale.com/api/v2/tailnet/-/acl" -o /tmp/tailscale_acl_test.json)

if [ "$API_RESPONSE" = "200" ]; then
    echo "✅ API connection successful"
    echo "   Current ACL retrieved"
else
    echo "❌ API connection failed (HTTP $API_RESPONSE)"
    echo "   Check your API key and network connectivity"
    rm -f /tmp/tailscale_acl_test.json
    exit 1
fi

rm -f /tmp/tailscale_acl_test.json
echo ""

# Initialize Terraform
echo "3. Initializing Terraform..."
echo "============================"

if [ ! -d ".terraform" ]; then
    echo "Initializing Terraform..."
    terraform init
    if [ $? -ne 0 ]; then
        echo "❌ Terraform initialization failed"
        exit 1
    fi
    echo "✅ Terraform initialized successfully"
else
    echo "✅ Terraform already initialized"
fi

echo ""

# Import ACL
echo "4. Importing ACL configuration..."
echo "================================="

echo "Importing Tailscale ACL..."
terraform import tailscale_acl.main acl

if [ $? -eq 0 ]; then
    echo "✅ ACL imported successfully"
else
    echo "❌ ACL import failed"
    echo "   This might be normal if the ACL is already imported"
    echo "   Check with 'terraform plan' to see the current state"
fi

echo ""

# Validate configuration
echo "5. Validating configuration..."
echo "=============================="

echo "Running terraform plan to check configuration..."
terraform plan

if [ $? -eq 0 ]; then
    echo "✅ Configuration validation completed"
    echo ""
    echo "Import process completed successfully!"
    echo ""
    echo "Next steps:"
    echo "==========="
    echo "1. Review the output above to ensure no unwanted changes"
    echo "2. If everything looks good, run: terraform apply"
    echo "3. If there are differences, adjust the ACL configuration in main.tf"
else
    echo "❌ Configuration validation failed"
    echo "   Please review the errors and adjust main.tf accordingly"
    exit 1
fi
