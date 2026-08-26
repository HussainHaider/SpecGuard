# Deployment

One VM, one compose file, Caddy in front. This document is the reasoning, not just the
runbook — the alternatives are at the end, with why each was rejected.

## What runs where

```
                    :443
                     │
              ┌──────▼──────┐   TLS, basic auth, the only public listener
              │    caddy    │
              └──┬───────┬──┘
        /checks  │       │  everything else
        /clauses │       │
                 │       │
          ┌──────▼──┐ ┌──▼──────┐
          │   api   │ │   web   │   static bundle, nginx
          └──┬───┬──┘ └─────────┘
             │   │
     ┌───────▼─┐ └──────┬─────────┬──────────┐
     │   db    │        │         │          │
     │postgres │   ┌────▼───┐ ┌───▼───┐ ┌────▼────┐
     └─────────┘   │ qdrant │ │ redis │ │ worker  │
                   └────────┘ └───────┘ └─────────┘

                   ┌─────────┐
                   │   n8n   │   internal only, reached over SSH
                   └─────────┘
```

Nothing except Caddy binds a public port. Postgres, Qdrant, Redis, the API, the UI and
n8n are all on the compose network and reachable only from inside it, or through a
tunnel.

## Bring it up

**Minimum 4 GB of memory available to Docker**, and 6 GB is comfortable. The embedding
model is resident in both the API and the worker, and Qdrant holds the whole collection in
memory. This is not theoretical: the clean-clone test was killed at 3.8 GB while another
stack was running, which is what exit code 137 means and how it presents — no traceback,
no error, a command that simply stops.

On a fresh Debian or Ubuntu VM with Docker installed:

```bash
git clone https://github.com/HussainHaider/SpecGuard.git && cd SpecGuard
cp .env.example .env
```

Then edit `.env`. Four values are not optional:

| variable | why |
|---|---|
| `N8N_ENCRYPTION_KEY` | `openssl rand -hex 32`. Set it before the first start. n8n encrypts saved credentials with this key, and anything saved under a key you later lose is unrecoverable. compose refuses to start without it. |
| `SPECGUARD_DOMAIN` | The hostname Caddy requests a certificate for. |
| `BASIC_AUTH_HASH` | `docker run --rm caddy caddy hash-password --plaintext '…'`. The plaintext never goes in `.env`. |
| `DEMO_MODE` | `true` for a public instance. See below. |

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml \
  --profile app up -d
docker compose exec api python -m specguard.corpus.seed
```

The corpus text is committed, so the seed indexes from disk and downloads nothing except
the embedding model on first run.

## Reaching the things that are not public

```bash
# n8n editor
ssh -N -L 5678:localhost:5678 deploy@$SPECGUARD_DOMAIN     # then http://localhost:5678

# Prometheus metrics
ssh -N -L 8000:localhost:8000 deploy@$SPECGUARD_DOMAIN     # then http://localhost:8000/metrics

# Postgres, for a one-off query
ssh -N -L 5432:localhost:5432 deploy@$SPECGUARD_DOMAIN
```

n8n is the one that matters. It stores credentials and can run shell commands, so it is
the highest-value target in the deployment and it has no business on a public interface
behind nothing but basic auth.

## Demo mode

The public instance runs `DEMO_MODE=true`. It replays reports computed in advance from
the synthetic specifications in this repository, matched by content hash, and labels every
one of them as replayed in the UI and in the API response.

This is not a shortcut, it is the only responsible setting for an instance anyone can
reach. A public upload endpoint wired to a paid model API is a bill a stranger can run up,
and a queue anyone can fill is a queue that will be filled. In demo mode the API builds no
model client and no vector store at all, so there is no misconfiguration that quietly
turns spending back on.

Turning it off is one variable, and then the worker, Qdrant and a provider key all matter
again.

## Backups

Two volumes hold anything that cannot be rebuilt:

- `db_data` — jobs, results, feedback. The audit trail. This is the one that matters.
- `n8n_data` — workflow state and credentials, encrypted with `N8N_ENCRYPTION_KEY`.
  Worthless without that key, which is why the key belongs in your password manager and
  not only in `.env` on the box.

`qdrant_data` is deliberately not backed up: it is derived from the committed corpus and
`corpus.seed` rebuilds it byte-identically, because chunk ids are deterministic. That is
the same property that lets the weekly re-index run without invalidating stored citations.

```bash
docker compose exec db pg_dump -U specguard specguard | gzip > specguard-$(date +%F).sql.gz
```

## Alternatives considered

**Kubernetes.** Rejected, and it is explicitly out of scope in `CLAUDE.md`. The workload
is one API, one worker and three datastores, with a peak concurrency of one reviewer. k8s
would add a control plane with more moving parts than the application, and every operation
in this document would become a manifest to keep current.

**Serverless — Lambda or Cloud Run.** Rejected on three specifics rather than taste. The
embedding model is a 200 MB ONNX download that has to be resident, so every cold start
pays for it. A check takes tens of seconds and several model calls, which fits a queue
worker and fights a request-scoped runtime. And Qdrant would have to become a managed
service, which reintroduces the per-month cost that a single small VM was chosen to avoid.
The honest version: this system's shape is a long-running worker with a warm model in
memory, and serverless is the wrong shape for that.

**Managed Postgres and managed Qdrant.** Rejected for a portfolio deployment on cost
alone; both are the right answer the moment this holds data anyone would miss. Nothing in
the code assumes otherwise — `DATABASE_URL` and `QDRANT_URL` are both configuration, so
the migration is an environment change.

**A separate migration service.** Rejected. The API container runs `alembic upgrade head`
before it serves. With several API replicas that is a race and a one-shot job is correct;
with one replica it is one fewer thing in the compose file. Revisit at the same time as
the second replica.

**Publishing n8n behind basic auth.** Rejected. Caddy's basic auth is a real boundary for
a read-only UI; n8n executes shell commands and holds credentials, and one leaked password
would be arbitrary code execution on the box. A tunnel costs one command and removes the
category.

**Authentication in the application.** Rejected, and out of scope in `CLAUDE.md`. There is
no user model and building one to protect a demo would be a week of the wrong work. Basic
auth at the edge is honest about what it is: a door, not an identity system.
