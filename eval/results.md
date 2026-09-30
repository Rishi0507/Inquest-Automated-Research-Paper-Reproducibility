# Evaluation results

Generated 2026-09-21 23:53:49. Values are measured on this machine; missing rows mean the experiment has not run.

| Experiment | Result |
|---|---|
| E1 extraction, kipf2016-vgae | not extracted |
| E1 extraction, kipf2017-gcn | not extracted |
| E1 extraction, wu2019-sgc | not extracted |
| E2 Cartographer | first-attempt validation not measured over 0 paper(s) |
| E3 planted faults | top-1 accuracy 50.0%; clean-control false positives 0.0% |
| E3 historical fault, kipf2016-vgae~hist-3a98122 | C-01 no gap, C-02 no gap; attribution: C-01: unexplained; C-02: unexplained |
| E3 share error, wu2019-sgc~blind-a C-01 | Shapley +0.97 against single-fault effect +0.97 (error 0.00) |
| E3 share error, wu2019-sgc~blind-a C-03 | Shapley +0.63 against single-fault effect +0.36 (error 0.28) |
| E3 share error, wu2019-sgc~metric-swap C-01 | Shapley -0.22 against single-fault effect -0.22 (error 0.00) |
| E3 share error, wu2019-sgc~metric-swap C-02 | Shapley -0.35 against single-fault effect -0.35 (error 0.01) |
| E4 selection signal, k=1 | lower-bound coverage 98.2%, silent 100.0% |
| E4 selection signal, k=5 | lower-bound coverage 98.2%, silent 100.0% |
| E4 selection signal, k=20 | lower-bound coverage 99.9%, silent 100.0% |
| E5 single-run fragility, against the paper value (81.50) | 15.4% of pairs of single runs disagree at +/-0.5; a single run passes 8.0% of the time; the band verdict is reproduced |
| E5 single-run fragility, against the code's own mean (83.05) | 51.5% of pairs of single runs disagree at +/-0.5; a single run passes 48.7% of the time; the band verdict is reproduced |
| E6 cost | 3172 runs requested, 1681 executed, 1486 cache hits |
| E7 Witness fidelity | re-scoring agreement 100.0% over 23 checks |
