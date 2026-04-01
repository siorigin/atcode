export CUDA_VISIBLE_DEVICES=1
# local model example:
vllm serve /xx/model/Qwen/Qwen3-Embedding-0.6B
            --host 0.0.0.0 --port 8080  --block-size 16                      \
            --api-key 123456 --dtype auto                                    \
            --trust-remote-code                                              \
            --served-model-name qwen_embedding                                        \
            --enable-prefix-caching                                          \
            --gpu-memory-utilization 0.3                                    \
            --max-model-len   4096                                           \
            --task embed --disable-log-requests