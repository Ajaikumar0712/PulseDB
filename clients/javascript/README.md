# @pulsedb/client — JavaScript/Node.js client

```bash
npm install @pulsedb/client
```

## Quick start

```js
const { PulseDB } = require('@pulsedb/client');

async function main() {
  const db = await PulseDB.connect({ host: '127.0.0.1', port: 7878 });

  await db.query('MAKE TABLE users (id int, name text, score float)');
  await db.query('PUT users (1, "Alice", 9.5)');
  await db.query('PUT users (2, "Bob",   7.2)');

  const result = await db.query('GET users ORDER BY score DESC');
  for (const row of result) {
    console.log(row.id, row.name, row.score);
  }

  db.close();
}

main().catch(console.error);
```

## With auto-close

```js
await PulseDB.withConnection(async (db) => {
  const res = await db.query('GET users WHERE id = 1');
  console.log(res.rows[0].name);
});
```

## Transactions

```js
await db.query('BEGIN');
await db.query('PUT orders (101, "shipped")');
await db.query('COMMIT');
```
