1. Starting the backend: uvicorn main:app --reload
2. Starting the frontend: npm run dev


## Running on the VM (Docker)

```bash
cd ~/Personal_agent
git pull && docker compose up -d --build     # update and restart
docker compose logs -f backend               # logs
docker compose --profile sync run --rm sync  # re-index the notes and photos
```

Never use `docker compose down -v`: it deletes the volume with the knowledge base and the counters.

### Usage limits and how to reset them by hand

The limits (`MAX_QUESTIONS_PER_USER`, `MAX_CHATS_PER_USER`, `MAX_QUESTIONS_PER_IP`) are lifetime totals
kept in SQLite (`/data/usage.db`, inside the `appdata` volume). Nothing resets them automatically.
The image has no `sqlite3` command, so run Python inside the backend container. It is safe while the
site is running.

Show the counters:

```bash
docker compose exec backend python -c "
import sqlite3
c = sqlite3.connect('/data/usage.db')
print('users:', c.execute('select client_id, questions from users').fetchall())
print('ips:  ', c.execute('select ip, questions from ips').fetchall())
"
```

Reset the per-IP counters (visitors keep their own question count):

```bash
docker compose exec backend python -c "
import sqlite3
c = sqlite3.connect('/data/usage.db')
c.execute('delete from ips')
c.commit()
print('ips cleared')
"
```

Reset everything, so every visitor gets a fresh quota:

```bash
docker compose exec backend python -c "
import sqlite3
c = sqlite3.connect('/data/usage.db')
for table in ('users', 'chats', 'ips'):
    c.execute(f'delete from {table}')
c.commit()
print('all counters cleared')
"
```

Reset one visitor (copy the id from the `users` list above):

```bash
docker compose exec backend python -c "
import sqlite3
c = sqlite3.connect('/data/usage.db')
cid = 'PASTE-CLIENT-ID-HERE'
c.execute('delete from users where client_id = ?', (cid,))
c.execute('delete from chats where client_id = ?', (cid,))
c.commit()
print('cleared', cid)
"
```

### Client IP behind the host nginx

The request passes the host nginx and then the nginx in the frontend container, so the backend sees
`X-Forwarded-For: <visitor>, <host nginx>` and `TRUSTED_PROXY_COUNT` must be **2**. With 1 every
visitor is counted under the same address and they share one `MAX_QUESTIONS_PER_IP` budget. After
changing it, clear the `ips` table (above) and check that it holds real visitor addresses, not
`172.x.x.x` or `127.0.0.1`.

### Streaming (agent steps, typing) through the host nginx

The host nginx location that proxies to `127.0.0.1:8080` needs `proxy_http_version 1.1;`,
`proxy_set_header Connection "";`, `proxy_buffering off;`, `proxy_cache off;`, `gzip off;` and
`proxy_read_timeout 300s;`. Without `proxy_buffering off` the answer arrives in one piece at the end.
