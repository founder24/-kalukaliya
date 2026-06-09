#!/bin/bash
# Run this once in the Replit Shell to install the post-commit auto-sync hook.
# After installation, every git commit (including Replit checkpoints) will
# automatically push to GitHub in the background.

set -e

HOOK_PATH="$(git rev-parse --show-toplevel)/.git/hooks/post-commit"
SCRIPT_PATH="$(git rev-parse --show-toplevel)/scripts/sync-to-github.sh"

# Make sync script executable
chmod +x "$SCRIPT_PATH"

# Write the hook
cat > "$HOOK_PATH" << 'EOF'
#!/bin/bash
# Auto-installed by scripts/install-hooks.sh
# Pushes to GitHub after every Replit checkpoint.
exec "$(git rev-parse --show-toplevel)/scripts/sync-to-github.sh"
EOF

chmod +x "$HOOK_PATH"

echo "✓ post-commit hook installed at $HOOK_PATH"
echo "✓ Every Replit checkpoint will now auto-push to GitHub."
echo ""
echo "To test it manually:"
echo "  bash scripts/sync-to-github.sh"
