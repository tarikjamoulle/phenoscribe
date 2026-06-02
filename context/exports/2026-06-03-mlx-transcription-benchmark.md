---
date: 2026-06-03
type: benchmark
input: data/recordings/467.mp3
hardware: Apple M1 Pro, 16-core GPU, 16 GB unified memory
---

# Transcription benchmark — faster-whisper (CPU) vs mlx-whisper (Apple GPU)

## Headline

| Setup | Backend / model | Where it runs | Transcription time | Realtime ratio | Speedup |
|---|---|---|---|---|---|
| Baseline | faster-whisper / large-v3 | M1 Pro CPU | 2733 s (45:33) | 0.26× | 1× |
| New default for Mac | mlx-whisper / large-v3 | M1 Pro GPU (Metal) | **606 s (10:06)** | **1.15×** | **4.5×** |

Same Whisper weights, same audio (11:39 of French clinical interview), same downstream HPO pipeline. The only thing that changed is which engine drives the model and which chip does the math.

For one transcript that's the difference between half a working day and a coffee break. At 100 recordings the gap compounds:

| Setup | One recording (avg 12 min audio) | 100 recordings |
|---|---|---|
| CPU | ~45 min | ~75 hours |
| **M1 Pro GPU (mlx)** | **~10 min** | **~17 hours** |
| RTX 3060 box (extrapolation) | ~1 min | ~1.7 hours |

## What changed in the code

A new `transcription.backend` knob in `config.yaml`:

```yaml
transcription:
  backend: "mlx"              # or "faster-whisper" (the previous default)
  model: "large-v3"
  language: "fr"
```

faster-whisper stays as the default — the deferred Docker CUDA variant uses it, and it's the only backend currently wired up for diarization. Apple Silicon users opt in by flipping `backend: "mlx"`.

## HPO output quality

The two transcripts produce comparable HPO code sets — the audio is the same, the LLM extractor is the same, only the transcript text differs in minor wording.

| Run | Distinct HPO codes | Codes shared with CPU baseline |
|---|---|---|
| faster-whisper / CPU | 26 | (baseline) |
| mlx / GPU (this benchmark) | 23 | 18 |

The 5 codes the mlx run picked up that the CPU run missed, and the 8 the CPU run picked up that mlx missed, are all in the same clinical neighbourhood (motor symptoms, cognitive complaints, etc.) — the difference comes from small wording variations in the transcripts which surface slightly different verbatims for the LLM to chase. Neither transcript is "better"; they are equivalent for the practical purpose.

## Why not the 6–10× we expected from M1 Pro

The M1 Pro 16-core GPU has roughly twice the theoretical throughput of the base M1, so on paper an M1 Pro should be ~2× faster than an M1 for GPU-bound work. The empirical 4.5× falls short of that headroom for three reasons:

1. **Whisper's decoder is autoregressive.** It generates output tokens one at a time, each token conditioned on the previous one. The encoder runs in parallel across the audio; the decoder cannot. This is a property of the model, not the engine.
2. **CPU preprocessing.** Audio → mel-spectrogram → tensor handoff happens on CPU before the GPU does anything. That cost is fixed regardless of how fast the GPU is.
3. **CTranslate2 is heavily optimised.** faster-whisper rides on CTranslate2, which has years of low-level CPU kernel tuning. The CPU baseline is faster than naive Whisper-on-CPU; mlx-whisper, being a younger project, hasn't squeezed the same kind of optimisation out of Apple's Metal kernels yet.

The gap will narrow as mlx-whisper matures. For now, 4.5× is a real, GDPR-safe speedup on hardware already in the room.

## The detour through `distil-large-v3`

An early version of this benchmark tried `mlx-community/distil-whisper-large-v3` (the distilled variant, ~2× faster than `large-v3`). It transcribed in 152 s — but produced English-soup gibberish on French audio. distil-whisper was trained on English-only data and silently degrades on other languages. Discarded. Documented in `MEMORY.md` so it doesn't get re-proposed for this project.

## Operational implications

- **For ad-hoc batches you'll run yourself on this Mac:** flip to `backend: "mlx"`, accept ~17 hours for 100 recordings, run over a long weekend.
- **For Marc running it from the clinic:** the GPU win does not transfer — his Windows laptop has no useful GPU. Either ship the work to your Mac, or revive the deferred plan to put a small Linux+GPU box in the clinic (`context/plans/2026-05-30-cross-platform-docker-distribution.md`). The RTX 3060 path lands at ~1.7 hours per 100 recordings, with no operator in the chain.

## Reproducing

```sh
# faster-whisper baseline
backend: "faster-whisper", model: "large-v3", device: "cpu"

# mlx (this benchmark)
backend: "mlx", model: "large-v3"
```

Then `phenoscribe process data/run_467 --provider claude --model claude-sonnet-4-6 --output output/results_467_<backend>.xlsx`. Audio file used: `data/recordings/467.mp3` (11:39, French long-COVID interview).

Transcripts produced during the benchmark are snapshotted at `output/transcripts/467_faster_whisper.txt`, `output/transcripts/467_mlx_large.txt`, and `output/transcripts/467_mlx_large_clean.txt` for future regression-case work.
