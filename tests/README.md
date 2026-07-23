# Test QueryX

## Suite completa

Dalla radice del repository:

```bash
make test
```

Il comando esegue:

```bash
docker compose exec queryx pytest -q
```

## Test di riproduzione

Per eseguire soltanto i test relativi a seed e benchmark:

```bash
make test-reproduction
```

## Esecuzione locale

```bash
pytest -q
```

Per un singolo file:

```bash
pytest -q tests/<nome_file>.py
```

Per un singolo test:

```bash
pytest -q tests/<nome_file>.py::<nome_test>
```
