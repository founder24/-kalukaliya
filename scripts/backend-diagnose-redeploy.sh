#!/bin/bash
#===============================================================================
# SYRABIT BACKEND DIAGNOSTIC & REDEPLOYMENT SCRIPT
#===============================================================================
# Purpose: Diagnose backend issues and trigger Cloud Run redeployment
# Usage:   ./scripts/backend-diagnose-redeploy.sh [--dry-run] [--force]
#===============================================================================

set -euo pipefail

# Configuration
PROJECT_ID="blissful-acumen-495019-t6"
REGION="asia-south1"
SERVICE_NAME="syrabit-backend"
BACKEND_DIR="apps/backend"
DOCKERFILE="$BACKEND_DIR/Dockerfile"
CLOUDBUILD_FILE="cloudbuild.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Flags
DRY_RUN=false
FORCE=false
SKIP_TESTS=false

#-------------------------------------------------------------------------------
# Parse arguments
#-------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--force] [--skip-tests]"
            echo "  --dry-run     Show what would be done without executing"
            echo "  --force       Force redeployment even if checks pass"
            echo "  --skip-tests  Skip local tests before deployment"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

#-------------------------------------------------------------------------------
# Helper functions
#-------------------------------------------------------------------------------
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_section() {
    echo -e "\n${BLUE}===============================================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===============================================================================${NC}\n"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed"
        return 1
    fi
    return 0
}

#-------------------------------------------------------------------------------
# Main diagnostic checks
#-------------------------------------------------------------------------------
log_section "PHASE 1: ENVIRONMENT CHECKS"

# Check required commands
log_info "Checking required commands..."
REQUIRED_CMDS=("curl" "jq" "openssl" "git")
MISSING_CMDS=()

for cmd in "${REQUIRED_CMDS[@]}"; do
    if ! check_command "$cmd"; then
        MISSING_CMDS+=("$cmd")
    fi
done

if [ ${#MISSING_CMDS[@]} -ne 0 ]; then
    log_warn "Missing commands: ${MISSING_CMDS[*]}"
    log_info "Some diagnostics may be skipped"
fi

# Check gcloud availability
if check_command "gcloud"; then
    log_success "gcloud CLI available"
    GCLOUD_AVAILABLE=true
else
    log_warn "gcloud CLI not available - will use API-based checks"
    GCLOUD_AVAILABLE=false
fi

#-------------------------------------------------------------------------------
log_section "PHASE 2: BACKEND HEALTH CHECK"

# Check current backend status
log_info "Checking backend health at https://api.syrabit.ai/health..."

HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" "https://api.syrabit.ai/health" 2>/dev/null || echo "000")
HEALTH_CODE=$(echo "$HEALTH_RESPONSE" | tail -n1)
HEALTH_BODY=$(echo "$HEALTH_RESPONSE" | sed '$d')

if [ "$HEALTH_CODE" = "200" ]; then
    log_success "Backend health endpoint responding (HTTP $HEALTH_CODE)"
    if echo "$HEALTH_BODY" | jq -e '.status == "healthy"' > /dev/null 2>&1; then
        log_success "Backend reports healthy status"
        STATUS=$(echo "$HEALTH_BODY" | jq -r '.status // "unknown"')
        SERVICE=$(echo "$HEALTH_BODY" | jq -r '.service // "unknown"')
        TIMESTAMP=$(echo "$HEALTH_BODY" | jq -r '.timestamp // "unknown"')
        log_info "Status: $STATUS | Service: $SERVICE | Timestamp: $TIMESTAMP"
        
        if [ "$FORCE" = false ]; then
            log_info "Backend appears healthy. Use --force to redeploy anyway."
            exit 0
        else
            log_warn "Force redeploy requested despite healthy status"
        fi
    else
        log_warn "Backend responding but status not healthy"
        log_info "Response: $HEALTH_BODY"
    fi
elif [ "$HEALTH_CODE" = "000" ]; then
    log_error "Backend unreachable - connection failed"
    NEEDS_REDEPLOY=true
else
    log_error "Backend returning HTTP $HEALTH_CODE (expected 200)"
    NEEDS_REDEPLOY=true
fi

# Check deep health endpoint
log_info "Checking deep health endpoint..."
DEEP_HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://api.syrabit.ai/health/deep" 2>/dev/null || echo "000")

if [ "$DEEP_HEALTH_CODE" = "200" ]; then
    log_success "Deep health endpoint responding"
else
    log_warn "Deep health endpoint returned HTTP $DEEP_HEALTH_CODE (may not exist)"
fi

#-------------------------------------------------------------------------------
log_section "PHASE 3: DOCKERFILE VALIDATION"

log_info "Validating Dockerfile..."

if [ ! -f "$DOCKERFILE" ]; then
    log_error "Dockerfile not found at $DOCKERFILE"
    exit 1
fi

log_success "Dockerfile exists"

# Check for common issues
if grep -q "EXPOSE 8000" "$DOCKERFILE"; then
    log_success "Dockerfile exposes port 8000"
else
    log_error "Dockerfile does not expose port 8000"
fi

if grep -q "CMD.*gunicorn" "$DOCKERFILE"; then
    log_success "Dockerfile has gunicorn CMD"
else
    log_error "Dockerfile missing gunicorn CMD"
fi

if grep -q "WORKDIR /app" "$DOCKERFILE"; then
    log_success "Dockerfile sets WORKDIR to /app"
else
    log_warn "Dockerfile WORKDIR may not be /app"
fi

# Check entrypoint consistency
if grep -q "app.main:app" "$DOCKERFILE"; then
    log_success "Dockerfile references correct app module"
else
    log_error "Dockerfile may reference incorrect app module"
fi

#-------------------------------------------------------------------------------
log_section "PHASE 4: CLOUDBUILD CONFIGURATION"

log_info "Validating cloudbuild.yaml..."

if [ ! -f "$CLOUDBUILD_FILE" ]; then
    log_error "cloudbuild.yaml not found"
    exit 1
fi

log_success "cloudbuild.yaml exists"

# Validate key configuration
if grep -q "asia-south1-docker.pkg.dev/blissful-acumen-495019-t6/syrabit/backend" "$CLOUDBUILD_FILE"; then
    log_success "Artifact Registry path configured correctly"
else
    log_error "Artifact Registry path misconfigured"
fi

if grep -q "syrabit-backend" "$CLOUDBUILD_FILE"; then
    log_success "Cloud Run service name configured"
else
    log_error "Cloud Run service name not found in config"
fi

if grep -q "region=asia-south1" "$CLOUDBUILD_FILE" || grep -q "\-\-region=asia-south1" "$CLOUDBUILD_FILE"; then
    log_success "Region configured as asia-south1"
else
    log_warn "Region may not be explicitly set to asia-south1"
fi

#-------------------------------------------------------------------------------
log_section "PHASE 5: APPLICATION CODE VALIDATION"

log_info "Checking application entry point..."

MAIN_PY="$BACKEND_DIR/app/main.py"
if [ ! -f "$MAIN_PY" ]; then
    log_error "main.py not found at $MAIN_PY"
    exit 1
fi

log_success "main.py exists"

# Check for lifespan events
if grep -q "asynccontextmanager" "$MAIN_PY" && grep -q "lifespan" "$MAIN_PY"; then
    log_success "FastAPI lifespan context manager present"
else
    log_warn "Lifespan context manager may be missing"
fi

# Check for health router registration
if grep -q "health.router" "$MAIN_PY"; then
    log_success "Health router registered"
else
    log_error "Health router not registered in main.py"
fi

# Check for critical route registrations
CRITICAL_ROUTES=("chat" "auth" "subscription" "admin")
for route in "${CRITICAL_ROUTES[@]}"; do
    if grep -q "${route}.router" "$MAIN_PY"; then
        log_success "$route router registered"
    else
        log_warn "$route router may not be registered"
    fi
done

#-------------------------------------------------------------------------------
log_section "PHASE 6: DEPENDENCY CHECK"

log_info "Checking Python dependencies..."

REQ_FILE="$BACKEND_DIR/requirements.txt"
if [ ! -f "$REQ_FILE" ]; then
    log_error "requirements.txt not found"
    exit 1
fi

log_success "requirements.txt exists"

# Check for critical packages
CRITICAL_PKGS=("fastapi" "uvicorn" "gunicorn" "beanie" "pymongo")
for pkg in "${CRITICAL_PKGS[@]}"; do
    if grep -qi "^${pkg}" "$REQ_FILE" || grep -qi "${pkg}==" "$REQ_FILE"; then
        log_success "$pkg dependency present"
    else
        log_warn "$pkg dependency may be missing or unpinned"
    fi
done

#-------------------------------------------------------------------------------
if [ "$SKIP_TESTS" = false ]; then
    log_section "PHASE 7: LOCAL TESTS (OPTIONAL)"
    
    log_info "Running basic syntax check on main.py..."
    if python3 -m py_compile "$MAIN_PY" 2>/dev/null; then
        log_success "Python syntax valid"
    else
        log_error "Python syntax errors detected"
        if [ "$FORCE" = false ]; then
            exit 1
        fi
    fi
fi

#-------------------------------------------------------------------------------
log_section "PHASE 8: CLOUD RUN STATUS"

if [ "$GCLOUD_AVAILABLE" = true ]; then
    log_info "Fetching Cloud Run service details..."
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run: gcloud run services describe $SERVICE_NAME --region $REGION --project $PROJECT_ID"
    else
        SERVICE_INFO=$(gcloud run services describe "$SERVICE_NAME" \
            --region "$REGION" \
            --project "$PROJECT_ID" \
            --format="json" 2>/dev/null || echo "{}")
        
        if [ "$SERVICE_INFO" = "{}" ]; then
            log_error "Service $SERVICE_NAME not found in $REGION"
            NEEDS_DEPLOY=true
        else
            log_success "Service found"
            
            # Get current image
            CURRENT_IMAGE=$(echo "$SERVICE_INFO" | jq -r '.spec.template.spec.containers[0].image // "unknown"')
            log_info "Current image: $CURRENT_IMAGE"
            
            # Get service URL
            SERVICE_URL=$(echo "$SERVICE_INFO" | jq -r '.status.url // "unknown"')
            log_info "Service URL: $SERVICE_URL"
            
            # Get ready status
            READY_CONDITION=$(echo "$SERVICE_INFO" | jq -r '.status.conditions[] | select(.type=="Ready") | .status // "unknown"')
            if [ "$READY_CONDITION" = "True" ]; then
                log_success "Service is Ready"
            else
                log_error "Service NOT Ready (status: $READY_CONDITION)"
                NEEDS_REDEPLOY=true
            fi
        fi
    fi
else
    log_warn "Skipping Cloud Run status check (gcloud not available)"
    # Try curl-based check
    log_info "Attempting to infer from health check..."
    if [ "$HEALTH_CODE" != "200" ]; then
        NEEDS_REDEPLOY=true
    fi
fi

#-------------------------------------------------------------------------------
log_section "PHASE 9: REDEPLOYMENT DECISION"

if [ "$NEEDS_REDEPLOY" = true ] || [ "$FORCE" = true ]; then
    log_info "Redeployment recommended/initiated"
    
    if [ "$DRY_RUN" = true ]; then
        log_section "DRY RUN - REDEPLOYMENT COMMANDS"
        log_info "Would execute:"
        echo ""
        echo "  # Build and push image"
        echo "  gcloud builds submit --config $CLOUDBUILD_FILE --substitutions COMMIT_SHA=\$(git rev-parse HEAD)"
        echo ""
        echo "  # Or manual deploy:"
        echo "  IMAGE_TAG=\$(date +%Y%m%d-%H%M%S)-\$(git rev-parse --short HEAD)"
        echo "  docker build -t asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:\$IMAGE_TAG -f $DOCKERFILE $BACKEND_DIR"
        echo "  docker push asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:\$IMAGE_TAG"
        echo "  gcloud run deploy $SERVICE_NAME \\"
        echo "    --image=asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:\$IMAGE_TAG \\"
        echo "    --region=$REGION \\"
        echo "    --port=8000 \\"
        echo "    --no-allow-unauthenticated"
        echo ""
        exit 0
    fi
    
    log_section "EXECUTING REDEPLOYMENT"
    
    # Generate image tag
    GIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    GIT_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    IMAGE_TAG="${TIMESTAMP}-${GIT_SHORT}"
    
    log_info "Image tag will be: $IMAGE_TAG"
    log_info "Git SHA: $GIT_SHA"
    
    # Confirm before proceeding
    if [ "$FORCE" = false ]; then
        read -p "Proceed with redeployment? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Redeployment cancelled"
            exit 0
        fi
    fi
    
    # Check if gcloud is available
    if [ "$GCLOUD_AVAILABLE" = true ]; then
        log_info "Submitting build via Cloud Build..."
        
        gcloud builds submit \
            --config "$CLOUDBUILD_FILE" \
            --substitutions "COMMIT_SHA=$IMAGE_TAG" \
            --project "$PROJECT_ID" \
            --timeout "20m"
        
        BUILD_STATUS=$?
        
        if [ $BUILD_STATUS -eq 0 ]; then
            log_success "Build submitted successfully"
            log_info "Monitor build progress at:"
            log_info "https://console.cloud.google.com/cloud-build/builds?project=$PROJECT_ID"
        else
            log_error "Build submission failed"
            exit 1
        fi
        
        # Wait for deployment
        log_info "Waiting for deployment to complete..."
        sleep 30
        
        # Check deployment status
        for i in {1..20}; do
            log_info "Checking deployment status (attempt $i/20)..."
            
            READY=$(gcloud run services describe "$SERVICE_NAME" \
                --region "$REGION" \
                --project "$PROJECT_ID" \
                --format="value(status.conditions[0].status)" 2>/dev/null || echo "Unknown")
            
            if [ "$READY" = "True" ]; then
                log_success "Deployment completed successfully!"
                break
            fi
            
            if [ $i -eq 20 ]; then
                log_warn "Deployment may still be in progress. Check Cloud Console."
            else
                sleep 15
            fi
        done
        
    else
        log_error "gcloud CLI required for automated deployment"
        log_info "Manual deployment steps:"
        echo ""
        echo "  1. Build Docker image:"
        echo "     docker build -t asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:$IMAGE_TAG -f $DOCKERFILE $BACKEND_DIR"
        echo ""
        echo "  2. Push to Artifact Registry:"
        echo "     docker push asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:$IMAGE_TAG"
        echo ""
        echo "  3. Deploy to Cloud Run:"
        echo "     gcloud run deploy $SERVICE_NAME \\"
        echo "       --image=asia-south1-docker.pkg.dev/$PROJECT_ID/syrabit/backend:$IMAGE_TAG \\"
        echo "       --region=$REGION \\"
        echo "       --port=8000 \\"
        echo "       --no-allow-unauthenticated \\"
        echo "       --service-account=syrabit-backend-sa@$PROJECT_ID.iam.gserviceaccount.com"
        echo ""
        echo "  4. Grant Cloudflare edge invoker access:"
        echo "     gcloud run services add-iam-policy-binding $SERVICE_NAME \\"
        echo "       --region=$REGION \\"
        echo "       --member=serviceAccount:cloudflare-edge-invoker@$PROJECT_ID.iam.gserviceaccount.com \\"
        echo "       --role=roles/run.invoker"
        echo ""
        exit 1
    fi
else
    log_section "DIAGNOSIS COMPLETE"
    log_success "No critical issues detected"
    log_info "Backend appears to be functioning normally"
    log_info "Use --force to trigger redeployment if needed"
fi

#-------------------------------------------------------------------------------
log_section "POST-DEPLOYMENT VERIFICATION"

log_info "Waiting 30 seconds for deployment to stabilize..."
sleep 30

log_info "Running post-deployment health checks..."

# Health check
FINAL_HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://api.syrabit.ai/health" 2>/dev/null || echo "000")

if [ "$FINAL_HEALTH_CODE" = "200" ]; then
    log_success "Post-deployment health check passed"
    
    # Get health response
    HEALTH_JSON=$(curl -s "https://api.syrabit.ai/health" 2>/dev/null)
    if echo "$HEALTH_JSON" | jq -e '.status == "healthy"' > /dev/null 2>&1; then
        log_success "Backend reports healthy status"
        echo "$HEALTH_JSON" | jq '.'
    else
        log_warn "Backend not reporting healthy status"
        echo "$HEALTH_JSON"
    fi
else
    log_error "Post-deployment health check failed (HTTP $FINAL_HEALTH_CODE)"
    log_info "Check Cloud Run logs:"
    log_info "https://console.cloud.google.com/logs/query;query=resource.type%3D%22cloud_run_revision%22%20AND%20resource.labels.service_name%3D%22$SERVICE_NAME%22?project=$PROJECT_ID"
fi

log_section "SUMMARY"
log_info "Diagnostic and redeployment script completed"
log_info "For detailed logs, check:"
log_info "  - Cloud Build: https://console.cloud.google.com/cloud-build/builds?project=$PROJECT_ID"
log_info "  - Cloud Run Logs: https://console.cloud.google.com/logs/query;query=resource.type%3D%22cloud_run_revision%22?project=$PROJECT_ID"
log_info "  - Cloud Monitoring: https://console.cloud.google.com/monitoring/dashboards?project=$PROJECT_ID"

exit 0
