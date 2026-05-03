//! Models module — re-exports from `db::models` so consumers can write
//! `crate::models::Subject` without reaching into the `db` namespace.
//!
//! These re-exports are part of the public surface of the crate; the
//! `dead_code`/`unused_imports` lints fire because no in-tree code uses
//! them yet, but downstream consumers (and future handlers) rely on
//! them being available, so we suppress the lints rather than delete
//! the re-exports.

#[allow(unused_imports)]
pub use crate::db::models::*;
#[allow(unused_imports)]
pub use crate::db::repository::Repository;
