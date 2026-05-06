# services/otel-collector/Dockerfile
#
# Task #333 — observability rewire.
#
# Thin wrapper over the upstream `otel/opentelemetry-collector-contrib`
# image whose only job is to bake `config.yaml` into the image at a
# known path. Azure Container Apps secrets are mounted differently, so
# the config has to ship inside the image — there is no
# init-container or volume mount for collector pipelines on DO.

# Pinned to a known-good contrib release (matches the tag in
# the otel-collector ACA bicep template — keep them in sync).
FROM otel/opentelemetry-collector-contrib:0.110.0

# The base image's default entrypoint is /otelcol-contrib; we only
# need to drop the config in place. The app spec's `run_command`
# passes `--config=/etc/otel/config.yaml` to the same entrypoint.
COPY config.yaml /etc/otel/config.yaml

# Healthcheck for local docker runs; Azure Container Apps uses its own
# probe declared in app-otel-collector.yaml.
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD ["/healthcheck", "--http=http://127.0.0.1:13133"] || exit 1

EXPOSE 4317 4318 13133
