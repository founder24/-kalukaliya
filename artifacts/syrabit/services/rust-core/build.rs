// Phase 5 — Observability rewire (Task #333).
//
// `tonic-health` ships a prebuilt Reflection of `grpc.health.v1.Health`
// so we do not need to compile a `proto/health.proto` ourselves to
// register the standard health server. This stub `build.rs` exists
// only to keep the Dockerfile's `protoc` install meaningful for any
// future user-defined .proto files dropped in `proto/`.

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto_dir = std::path::Path::new("proto");
    if proto_dir.exists() {
        let entries: Vec<_> = std::fs::read_dir(proto_dir)?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("proto"))
            .collect();

        if !entries.is_empty() {
            tonic_build::configure()
                .build_server(true)
                .build_client(false)
                .compile(&entries, &[proto_dir])?;
            for p in &entries {
                println!("cargo:rerun-if-changed={}", p.display());
            }
        }
    }
    println!("cargo:rerun-if-changed=build.rs");
    Ok(())
}
