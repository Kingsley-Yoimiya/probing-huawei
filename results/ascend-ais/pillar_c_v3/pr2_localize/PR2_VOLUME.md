# PR-2 数据量比（E3 头条）

| 轮次 | run_id | headline | dense | culprit | 备注 |
|------|--------|----------|-------|---------|------|
| v2 ref | 20260726_181423 | 72.6% | 1 | victim | grj baseline |
| B5d | (prev) | 115.05% | 1 | 7 | eager comm/step,no prune |
| B7 | 20260728_185909 | 47.67% raw | 0 | 5(mis) | B6 code lazy+prune, crash@146, mis-localize |
| B8-smoke | 20260728_203149 | n/a | 0 (early exit) | 7 | avg+window=100 修 B7 mis; grj-w0 pod |
| **B8 长跑** | 20260728_204936 | **88.28%** W* | 16 | **7** GT ✅ | grj-w0, 1042 步 done, dense=16 与 v2 采样架构冲突, PARTIAL |
