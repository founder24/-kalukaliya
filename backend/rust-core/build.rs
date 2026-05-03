// Build script for generating Rust code from Protocol Buffers definitions

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Tell Cargo to re-run this build script if proto files change
    println!("cargo:rerun-if-changed=proto/schema.proto");

    // Compile the proto file. tonic-build 0.12 renamed `.compile()` to
    // `.compile_protos()`. We deliberately do NOT set `.out_dir(...)` —
    // the generated `syrabit.rs` lives in `OUT_DIR` and is pulled in
    // via `include!` from `src/generated/mod.rs`.
    //
    // We also write a binary FileDescriptorSet (`syrabit_descriptor.bin`)
    // alongside the generated Rust so `tonic-reflection` can serve gRPC
    // server reflection on port 50051 (used by grpcurl smoke probes and
    // the Cloudflare gRPC-Web proxy for service discovery).
    let out_dir = std::path::PathBuf::from(std::env::var("OUT_DIR")?);
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .file_descriptor_set_path(out_dir.join("syrabit_descriptor.bin"))
        .compile_protos(&["proto/schema.proto"], &["proto"])?;

    println!("Proto compilation completed successfully");

    Ok(())
}
