'use strict';

/**
 * PulseDB JavaScript ORM — complete usage example.
 *
 * Run:
 *   # Start server first
 *   # .\target\release\pulsedb-server.exe --no-auth
 *
 *   node example_orm.js
 */

const { PulseDB } = require('./index');
const { defineModel, DataTypes, withTransaction } = require('./orm');

async function main() {

  // ── 1. Connect ──────────────────────────────────────────────────────────────

  const db = await PulseDB.connect({ host: '127.0.0.1', port: 7878 });
  // await db.auth(process.env.PULSEDB_USER, process.env.PULSEDB_PASSWORD);


  // ── 2. Define models ────────────────────────────────────────────────────────

  const User = defineModel('users', {
    id:     { type: DataTypes.INT,   primaryKey: true },
    name:   { type: DataTypes.TEXT },
    age:    { type: DataTypes.INT },
    active: { type: DataTypes.BOOL,  defaultValue: true },
    score:  { type: DataTypes.FLOAT, defaultValue: 0.0 },
  }, { db });

  const Product = defineModel('products', {
    id:        { type: DataTypes.INT,    primaryKey: true },
    name:      { type: DataTypes.TEXT },
    price:     { type: DataTypes.FLOAT },
    tags:      { type: DataTypes.JSON,   defaultValue: null },
    embedding: { type: DataTypes.VECTOR, defaultValue: null },
  }, { db });


  // ── 3. Schema ────────────────────────────────────────────────────────────────

  await User.createTable();
  await Product.createTable();
  await User.createIndex('age');
  await Product.createIndex('price');


  // ── 4. Write ─────────────────────────────────────────────────────────────────

  const alice = await User.create({ id: 1, name: 'Alice', age: 30, active: true, score: 0.95 });
  await User.create({ id: 2, name: 'Bob',   age: 17, active: true,  score: 0.72 });
  await User.create({ id: 3, name: 'Carol', age: 25, active: false, score: 0.88 });

  console.log('Created:', String(alice));

  // Bulk insert in a transaction
  await withTransaction(db, async () => {
    await User.bulkCreate(
      Array.from({ length: 6 }, (_, i) => ({
        id: i + 4, name: `User${i + 4}`, age: 20 + i, active: i % 2 === 0, score: 0.5,
      })),
      { transaction: false }  // already inside withTransaction
    );
  });

  // Products with vectors
  await Product.create({ id: 1, name: 'Widget',    price: 9.99,  embedding: [0.9, 0.1, 0.2, 0.0] });
  await Product.create({ id: 2, name: 'Gadget',    price: 24.99, embedding: [0.1, 0.8, 0.3, 0.5] });
  await Product.create({ id: 3, name: 'Doohickey', price: 4.99,  embedding: [0.5, 0.5, 0.5, 0.5] });

  console.log(`Inserted ${await Product.count()} products`);


  // ── 5. Read ──────────────────────────────────────────────────────────────────

  const all = await User.findAll({ orderBy: 'age' });
  console.log(`\nAll users (${all.length}):`);
  all.forEach(u => console.log(`  ${String(u.id).padStart(2)}  ${u.name.padEnd(10)}  age=${u.age}`));

  // Filter
  const adults = await User.findAll({
    where:   { age: { gte: 18 }, active: true },
    orderBy: 'age',
    limit:   5,
  });
  console.log(`\nActive adults (top 5): ${adults.map(u => u.name).join(', ')}`);

  // Find by PK
  const foundAlice = await User.findByPk(1);
  console.log(`\nfindByPk(1): ${String(foundAlice)}`);

  // findOne
  const youngest = await User.findOne({ where: { active: true }, orderBy: 'age' });
  console.log(`Youngest active: ${youngest.name} (age ${youngest.age})`);

  // in operator
  const specific = await User.findAll({ where: { id: { in: [1, 2, 3] } } });
  console.log(`id in [1,2,3]: ${specific.map(u => u.name).join(', ')}`);


  // ── 6. Update ────────────────────────────────────────────────────────────────

  // Static update
  await User.update({ active: true, score: 0.85 }, { where: { id: 2 } });

  // Instance update
  const bob = await User.findByPk(2);
  await bob.update({ age: 18 });
  console.log(`\nBob updated: age=${bob.age}, active=${bob.active}`);


  // ── 7. Delete ────────────────────────────────────────────────────────────────

  await User.create({ id: 99, name: 'Temp', age: 0 });
  await User.destroy({ where: { id: 99 } });
  console.log(`\nAfter destroy id=99: ${await User.count()} users`);

  // Instance delete
  await User.create({ id: 100, name: 'Temp2', age: 0 });
  const temp = await User.findByPk(100);
  await temp.delete();
  console.log(`After instance delete: ${await User.count()} users`);


  // ── 8. Vector similarity search ──────────────────────────────────────────────

  const nearest = await Product.similar('embedding', [0.88, 0.12, 0.25, 0.05], { limit: 3 });
  console.log('\nVector search nearest:');
  nearest.forEach(p => console.log(`  ${p.name.padEnd(12)} price=${p.price}  score=${p._score}`));


  // ── 9. Fuzzy text search ──────────────────────────────────────────────────────

  const hits = await User.fuzzy('name', 'alic', { limit: 5 });
  console.log(`\nFuzzy 'alic': ${hits.map(u => u.name).join(', ')}`);


  // ── 10. QuerySet chaining ─────────────────────────────────────────────────────

  const topUsers = await User
    .where({ age: { gte: 20 }, active: true })
    .orderBy('-score')
    .limit(3)
    .all();
  console.log('\nTop 3 active users by score (age>=20):');
  topUsers.forEach(u => console.log(`  ${u.name.padEnd(10)} score=${u.score}`));


  // ── 11. withTransaction rollback ──────────────────────────────────────────────

  try {
    await withTransaction(db, async () => {
      await User.create({ id: 200, name: 'TxUser', age: 25 });
      throw new Error('rollback test');
    });
  } catch (_) {}

  const txUser = await User.findByPk(200);
  console.log(`\nTransaction rollback test: id=200 exists = ${txUser !== null}`); // false


  db.close();
  console.log('\nDone.');
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
