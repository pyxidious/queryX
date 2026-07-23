# Benchmark QueryX

Questa directory contiene casi, runner e risultati del benchmark.

## Riproduzione rapida

Dalla radice del repository:

```bash
MODEL_LABEL=qwen3.5-9b-100k make reproduce
```

Il comando:

1. verifica Ollama e il modello configurato;
2. avvia i servizi;
3. attende che l'API sia disponibile;
4. genera i dati dimostrativi;
5. esegue discovery e profiling;
6. rigenera la ground truth;
7. esegue il benchmark.

## Esecuzione manuale

```bash
make up
make wait
make seed
make scan
make ground-truth
MODEL_LABEL=qwen3.5-9b-100k make benchmark
```

I risultati vengono salvati in:

```text
benchmark/results/
```

Per confrontare modelli differenti, mantenere costanti dati, catalogo, casi, configurazione e hardware, cambiando soltanto il modello e `MODEL_LABEL`.
