#!/bin/bash
    
# python ./scripts/train_ml_guard.py \
#         --dataset ./output/ml/dataset_dense_nifty_50_20260516.csv \
#         --label-mode quantile \
#         --top-quantile 0.75 \
#         --min-recall 0.05 \
#         --min-precision 0.70 \
#         --n-iter 100 \
#         --use-gpu

# python ./scripts/train_ml_guard_regressor.py \
#      --dataset ./output/ml/dataset_dense_nifty_50_20260518.csv \
#      --n-iter 100 \
#      --min-precision 0.70 \
#      --min-recall 0.05 \
#      --use-gpu


python ./scripts/train_ml_guard_multitarget.py \
     --dataset ./output/ml/dataset_dense_nifty_50_20260519.csv \
     --target mfe \
     --threshold 8.0 \
     --n-iter 100 \
     --min-precision 0.70 \
     --min-recall 0.05 \
     --use-gpu