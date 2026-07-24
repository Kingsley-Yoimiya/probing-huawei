use std::collections::HashMap;

use async_trait::async_trait;
use probing_core::core::EngineError;
use probing_core::core::Maybe;
use probing_core::core::ProbeExtension;
use probing_core::core::ProbeExtensionCall;
use probing_core::core::ProbeExtensionOption;
use pyo3::prelude::*;

#[derive(Debug, Default, ProbeExtension)]
pub struct TorchProbeExtension {
    /// Combined PyTorch profiling specification string (see TorchProbeConfig).
    #[option(aliases=["profiling_mode"])]
    profiling: Maybe<String>,
}

#[async_trait]
impl ProbeExtensionCall for TorchProbeExtension {
    async fn call(
        &self,
        path: &str,
        params: &HashMap<String, String>,
        _body: &[u8],
    ) -> Result<Vec<u8>, EngineError> {
        match path.trim_start_matches('/') {
            "flamegraph" => Ok(crate::features::torch::flamegraph().into_bytes()),
            "flamegraph/json" => {
                let metric = params.get("metric").map(|s| s.as_str());
                Ok(crate::features::torch::flamegraph_json(metric).into_bytes())
            }
            _ => Err(EngineError::UnsupportedCall),
        }
    }
}

impl TorchProbeExtension {
    fn set_profiling(&mut self, profiling: Maybe<String>) -> Result<(), EngineError> {
        // IMPORTANT (Ascend/NPU): never call torch_probe.configure() from this
        // Tokio/env-sync path. configure() may `import torch` and register hooks;
        // doing that off the training main thread SIGABRTs on torch_npu images.
        // Only persist the spec into probing.config; main-thread activation
        // (import hook / ext.torch.init / first optimizer.step) picks it up.
        let py_result = Python::attach(|py| -> pyo3::PyResult<()> {
            let probing = py.import("probing")?;
            let config = probing.getattr("config")?;
            const KEY: &str = "probing.torch.profiling";
            match &profiling {
                Maybe::Just(spec) if !spec.trim().is_empty() => {
                    config.call_method1("set", (KEY, spec.as_str()))?;
                }
                _ => {
                    let _ = config.call_method1("remove", (KEY,));
                }
            }
            Ok(())
        });

        match py_result {
            Ok(()) => {
                self.profiling = profiling;
                Ok(())
            }
            Err(err) => {
                let value: String = profiling.clone().into();
                log::error!(
                    "Failed to store torch profiling spec '{}': {}",
                    value,
                    err
                );
                Err(EngineError::InvalidOptionValue(
                    Self::OPTION_PROFILING.to_string(),
                    value,
                ))
            }
        }
    }
}
