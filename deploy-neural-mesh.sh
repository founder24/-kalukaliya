#!/bin/bash
set -e

# Syrabit Neural Mesh Deployment Script
# Orchestrates deployment of Frontend → Edge → Core
# Phase 1 Complete: gRPC-Web support, D1 sync handlers, env files

echo "🚀 Deploying Syrabit Neural Mesh..."
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
RUST_CORE_DIR="backend/rust-core"
EDGE_DIR="edge"
FRONTEND_DIR="frontend"

# Parse command line arguments
DEPLOY_TARGET="${1:-all}" # all, core, edge, frontend
SKIP_BUILD="${SKIP_BUILD:-false}"
DRY_RUN="${DRY_RUN:-false}"

# Validate environment variables
validate_env() {
    echo -e "${BLUE}=== Validating Environment ===${NC}"
    
    local errors=0
    
    # Check Rust Core env
    if [ ! -f "$RUST_CORE_DIR/.env" ] && [ -z "$DATABASE_URL" ]; then
        echo -e "${RED}❌ Missing DATABASE_URL for Rust Core${NC}"
        echo "   Set DATABASE_URL env var or create $RUST_CORE_DIR/.env"
        errors=$((errors + 1))
    fi
    
    if [ -z "$JWT_SECRET" ]; then
        echo -e "${RED}❌ Missing JWT_SECRET${NC}"
        echo "   Generate one: openssl rand -hex 32"
        errors=$((errors + 1))
    fi
    
    # Check Cloudflare auth
    if [ "$DEPLOY_TARGET" = "edge" ] || [ "$DEPLOY_TARGET" = "all" ]; then
        if [ -z "$CLOUDFLARE_API_TOKEN" ] && [ ! -f "$HOME/.wrangler/config/default.toml" ]; then
            echo -e "${YELLOW}⚠️  Cloudflare API token not found${NC}"
            echo "   Run: wrangler login"
        fi
    fi
    
    if [ $errors -gt 0 ]; then
        echo -e "${RED}Found $errors configuration error(s)${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ Environment validation passed${NC}"
    echo ""
}

deploy_rust_core() {
    echo -e "${BLUE}=== Deploying Rust Core ===${NC}"
    
    if [ ! -d "$RUST_CORE_DIR" ]; then
        echo -e "${RED}Error: Rust Core directory not found${NC}"
        return 1
    fi
    
    cd "$RUST_CORE_DIR"
    
    # Build (skip if requested)
    if [ "$SKIP_BUILD" = "false" ]; then
        echo -e "${YELLOW}Building Rust Core (release mode)...${NC}"
        cargo build --release
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}❌ Build failed${NC}"
            cd - > /dev/null
            return 1
        fi
    fi
    
    # Run database migrations
    echo -e "${YELLOW}Running database migrations...${NC}"
    if [ -n "$DATABASE_URL" ]; then
        cargo install sqlx-cli --no-default-features --features postgres
        sqlx migrate run --database-url "$DATABASE_URL"
        echo -e "${GREEN}✅ Migrations applied${NC}"
    else
        echo -e "${YELLOW}⚠️  DATABASE_URL not set, skipping migrations${NC}"
    fi
    
    # Docker build (optional)
    if command -v docker &> /dev/null && [ -f "Dockerfile" ]; then
        echo -e "${YELLOW}Building Docker image...${NC}"
        docker build -t syrabit-rust-core:latest .
        
        # Tag for registry
        if [ -n "$DOCKER_REGISTRY" ]; then
            docker tag syrabit-rust-core:latest "$DOCKER_REGISTRY/syrabit-rust-core:latest"
            docker push "$DOCKER_REGISTRY/syrabit-rust-core:latest"
        fi
    fi
    
    # Digital Ocean App Platform deployment (Task #336 — replaces Railway)
    if command -v doctl &> /dev/null; then
        echo -e "${YELLOW}Deploying to Digital Ocean App Platform...${NC}"
        bash "$(git rev-parse --show-toplevel)/scripts/digitalocean.sh" deploy rust-core
    else
        echo -e "${YELLOW}doctl CLI not found. Manual deployment required.${NC}"
        echo "   1. Install doctl: brew install doctl  (or see docs/DIGITALOCEAN-DEPLOYMENT.md)"
        echo "   2. Authenticate: doctl auth init -t \"\$DIGITALOCEAN_ACCESS_TOKEN\""
        echo "   3. Deploy: bash scripts/digitalocean.sh deploy rust-core"
    fi
    
    cd - > /dev/null
    echo -e "${GREEN}✅ Rust Core deployment complete${NC}"
    echo ""
}

deploy_edge_worker() {
    echo -e "${BLUE}=== Deploying Edge Worker ===${NC}"
    
    if [ ! -f "$EDGE_DIR/worker.js" ]; then
        echo -e "${RED}Error: Edge worker file not found${NC}"
        return 1
    fi
    
    cd "$EDGE_DIR"
    
    # Check for Wrangler CLI
    if ! command -v wrangler &> /dev/null; then
        echo -e "${YELLOW}Installing Wrangler CLI...${NC}"
        npm install -g wrangler
    fi
    
    # Validate wrangler.toml exists
    if [ ! -f "wrangler.toml" ]; then
        echo -e "${RED}❌ wrangler.toml not found in $EDGE_DIR${NC}"
        echo "   Create wrangler.toml or use workers/edge-proxy/wrangler.toml"
        cd - > /dev/null
        return 1
    fi
    
    # Dry run (schema validation only)
    if [ "$DRY_RUN" = "true" ]; then
        echo -e "${YELLOW}Running dry-run validation...${NC}"
        wrangler deploy --dry-run
        cd - > /dev/null
        return 0
    fi
    
    # Deploy to Cloudflare Workers
    echo -e "${YELLOW}Deploying to Cloudflare Workers...${NC}"
    wrangler deploy worker.js --env production
    
    cd - > /dev/null
    echo -e "${GREEN}✅ Edge Worker deployment complete${NC}"
    echo ""
}

deploy_frontend() {
    echo -e "${BLUE}=== Deploying Frontend ===${NC}"
    
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo -e "${RED}Error: Frontend directory not found${NC}"
        return 1
    fi
    
    cd "$FRONTEND_DIR"
    
    # Install dependencies
    echo -e "${YELLOW}Installing dependencies...${NC}"
    if [ -f "pnpm-lock.yaml" ]; then
        pnpm install --frozen-lockfile
    elif [ -f "yarn.lock" ]; then
        yarn install --frozen-lockfile
    else
        npm ci
    fi
    
    # Build
    echo -e "${YELLOW}Building frontend...${NC}"
    npm run build
    
    # Deploy
    if command -v vercel &> /dev/null; then
        vercel --prod
    elif command -v netlify &> /dev/null; then
        netlify deploy --prod --dir=dist
    elif command -v wrangler &> /dev/null; then
        wrangler pages deploy dist --project-name=syrabit-frontend
    else
        echo -e "${YELLOW}No deployment tool found. Build artifacts in dist/${NC}"
    fi
    
    cd - > /dev/null
    echo -e "${GREEN}✅ Frontend deployment complete${NC}"
    echo ""
}

run_health_checks() {
    echo -e "${BLUE}=== Running Health Checks ===${NC}"
    
    local RUST_CORE_URL="${RUST_CORE_URL:-http://localhost:3000}"
    local EDGE_URL="${EDGE_URL:-https://syrabit.ai}"
    local passed=0
    local failed=0
    
    # Check Rust Core health
    echo -e "${YELLOW}Checking Rust Core health...${NC}"
    if curl -f -s --max-time 10 "$RUST_CORE_URL/health" > /dev/null; then
        echo -e "${GREEN}✅ Rust Core is healthy ($RUST_CORE_URL/health)${NC}"
        passed=$((passed + 1))
    else
        echo -e "${RED}❌ Rust Core health check failed${NC}"
        failed=$((failed + 1))
    fi
    
    # Check D1 sync endpoint
    echo -e "${YELLOW}Checking D1 sync endpoint...${NC}"
    if curl -f -s --max-time 10 "$RUST_CORE_URL/api/edge/d1-status" > /dev/null; then
        echo -e "${GREEN}✅ D1 sync endpoint responding${NC}"
        passed=$((passed + 1))
    else
        echo -e "${YELLOW}⚠️  D1 sync endpoint not available (may need DB setup)${NC}"
    fi
    
    # Check Edge Worker
    echo -e "${YELLOW}Checking Edge Worker...${NC}"
    if curl -f -s --max-time 10 "$EDGE_URL" > /dev/null; then
        echo -e "${GREEN}✅ Edge Worker is responding${NC}"
        passed=$((passed + 1))
    else
        echo -e "${RED}❌ Edge Worker check failed${NC}"
        failed=$((failed + 1))
    fi
    
    echo ""
    echo -e "${BLUE}Health Check Summary: ${GREEN}$passed passed${NC}, ${RED}$failed failed${NC}"
    echo ""
    
    if [ $failed -gt 0 ]; then
        return 1
    fi
}

show_next_steps() {
    echo -e "${GREEN}🎉 Deployment completed successfully!${NC}"
    echo ""
    echo "Summary:"
    echo "  - Rust Core: $RUST_CORE_DIR"
    echo "  - Edge Worker: $EDGE_DIR"
    echo "  - Frontend: $FRONTEND_DIR"
    echo ""
    echo "Next steps:"
    echo "  1. Verify services: curl $RUST_CORE_URL/health"
    echo "  2. Test D1 sync: curl $RUST_CORE_URL/api/edge/d1-status"
    echo "  3. Check logs: bash scripts/digitalocean.sh logs rust-core"
    echo "  4. Test JARVIS HUD: https://syrabit.ai/jarvis"
    echo "  5. Test staff login: https://syrabit.ai/staff/login"
    echo ""
    echo "Rollback instructions:"
    echo "  - Digital Ocean: doctl apps create-deployment \"\$DO_APP_ID_RUST_CORE\" --rollback-deployment <prev-uuid>"
    echo "  - Cloudflare: wrangler rollback"
    echo ""
}

# Main deployment logic
main() {
    validate_env || exit 1
    
    case "$DEPLOY_TARGET" in
        core)
            deploy_rust_core
            ;;
        edge)
            deploy_edge_worker
            ;;
        frontend)
            deploy_frontend
            ;;
        all|*)
            deploy_rust_core
            deploy_edge_worker
            deploy_frontend
            run_health_checks
            ;;
    esac
    
    show_next_steps
}

main
