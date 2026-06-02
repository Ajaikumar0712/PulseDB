'use strict';

/**
 * PulseDB JavaScript ORM.
 *
 * Provides a Mongoose/Sequelize-inspired declarative model layer on top of
 * the raw PulseDB TCP client (index.js).
 *
 * @example
 * ```js
 * const { PulseDB } = require('./index');
 * const { defineModel, DataTypes } = require('./orm');
 *
 * const db = await PulseDB.connect({ host: '127.0.0.1', port: 7878 });
 *
 * const User = defineModel('users', {
 *   id:     { type: DataTypes.INT,   primaryKey: true },
 *   name:   { type: DataTypes.TEXT },
 *   age:    { type: DataTypes.INT },
 *   active: { type: DataTypes.BOOL, defaultValue: true },
 * }, { db });
 *
 * await User.createTable();
 *
 * const alice = await User.create({ id: 1, name: 'Alice', age: 30 });
 * const users = await User.findAll({ where: { age: { gte: 18 } }, orderBy: 'age', limit: 10 });
 * await User.update({ active: false }, { where: { age: { lt: 18 } } });
 * await User.destroy({ where: { id: 99 } });
 * ```
 */

const { PulseDB } = require('./index');

// ── Data types ────────────────────────────────────────────────────────────────

const DataTypes = Object.freeze({
  INT:    'int',
  FLOAT:  'float',
  TEXT:   'text',
  BOOL:   'bool',
  JSON:   'json',
  BLOB:   'blob',
  VECTOR: 'vector',
});

// ── Serialisation helpers ─────────────────────────────────────────────────────

function toLiteral(type, value) {
  if (value === null || value === undefined) return 'null';
  switch (type) {
    case DataTypes.INT:    return String(Math.trunc(Number(value)));
    case DataTypes.FLOAT:  return String(Number(value));
    case DataTypes.BOOL:   return value ? 'true' : 'false';
    case DataTypes.TEXT: {
      const escaped = String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      return `"${escaped}"`;
    }
    case DataTypes.VECTOR: {
      const nums = Array.isArray(value) ? value : [];
      return `[${nums.map(Number).join(', ')}]`;
    }
    case DataTypes.JSON:
      return JSON.stringify(value);
    default:
      return JSON.stringify(value);
  }
}

// ── WHERE clause builder ──────────────────────────────────────────────────────

/**
 * Build a PulseQL WHERE clause fragment from a filter object.
 *
 * Supports:
 *   { field: value }              → field = value
 *   { field: { gt: v } }         → field > v
 *   { field: { gte: v } }        → field >= v
 *   { field: { lt: v } }         → field < v
 *   { field: { lte: v } }        → field <= v
 *   { field: { ne: v } }         → field != v
 *   { field: { in: [a, b] } }    → (field = a OR field = b)
 */
function buildWhere(where, schema) {
  if (!where || Object.keys(where).length === 0) return '';

  const parts = [];
  for (const [col, condition] of Object.entries(where)) {
    const fieldDef = schema[col] || { type: DataTypes.TEXT };
    const type = fieldDef.type;

    if (condition !== null && typeof condition === 'object' && !Array.isArray(condition)) {
      const ops = { gt: '>', gte: '>=', lt: '<', lte: '<=', ne: '!=' };
      for (const [op, val] of Object.entries(condition)) {
        if (op === 'in') {
          if (!Array.isArray(val) || val.length === 0) {
            parts.push('1 = 0'); // always false
          } else {
            const clauses = val.map(v => `${col} = ${toLiteral(type, v)}`);
            parts.push(`(${clauses.join(' OR ')})`);
          }
        } else if (ops[op]) {
          parts.push(`${col} ${ops[op]} ${toLiteral(type, val)}`);
        }
      }
    } else {
      parts.push(`${col} = ${toLiteral(type, condition)}`);
    }
  }
  return parts.length ? ` WHERE ${parts.join(' AND ')}` : '';
}

function buildOrderBy(orderBy) {
  if (!orderBy) return '';
  if (typeof orderBy === 'string') {
    if (orderBy.startsWith('-')) return ` ORDER BY ${orderBy.slice(1)} DESC`;
    return ` ORDER BY ${orderBy} ASC`;
  }
  if (Array.isArray(orderBy)) {
    const cols = orderBy.map(c =>
      c.startsWith('-') ? `${c.slice(1)} DESC` : `${c} ASC`
    );
    return ` ORDER BY ${cols.join(', ')}`;
  }
  return '';
}

function buildLimit(limit) {
  return limit != null ? ` LIMIT ${limit}` : '';
}

// ── QuerySet ──────────────────────────────────────────────────────────────────

class QuerySet {
  constructor(model) {
    this._model  = model;
    this._wheres = [];
    this._order  = null;
    this._lim    = null;
  }

  _clone() {
    const q = new QuerySet(this._model);
    q._wheres = [...this._wheres];
    q._order  = this._order;
    q._lim    = this._lim;
    return q;
  }

  where(conditions) {
    const q = this._clone();
    q._wheres.push(buildWhere(conditions, this._model._schema).slice(7)); // strip ' WHERE '
    return q;
  }

  orderBy(...cols) {
    const q = this._clone();
    q._order = cols;
    return q;
  }

  limit(n) {
    const q = this._clone();
    q._lim = n;
    return q;
  }

  _buildWhereClause() {
    const parts = this._wheres.filter(Boolean);
    return parts.length ? ` WHERE ${parts.join(' AND ')}` : '';
  }

  async all() {
    const { table, db, _schema } = this._model;
    const stmt =
      `GET ${table}` +
      this._buildWhereClause() +
      buildOrderBy(this._order) +
      buildLimit(this._lim);
    const result = await db.query(stmt);
    return result.rows.map(row => this._model._fromRow(result.columns, row));
  }

  async first() {
    const rows = await this.limit(1).all();
    return rows[0] || null;
  }

  async count() {
    return (await this.all()).length;
  }

  async update(values) {
    const { table, db, _schema } = this._model;
    const parts = Object.entries(values).map(([k, v]) => {
      const type = (_schema[k] || {}).type || DataTypes.TEXT;
      return `${k}: ${toLiteral(type, v)}`;
    });
    const stmt = `SET ${table} { ${parts.join(', ')} }${this._buildWhereClause()}`;
    await db.query(stmt);
  }

  async delete() {
    const { table, db } = this._model;
    await db.query(`DEL ${table}${this._buildWhereClause()}`);
  }

  [Symbol.asyncIterator]() {
    let results = null;
    let index = 0;
    return {
      next: async () => {
        if (!results) results = await this.all();
        if (index < results.length) return { value: results[index++], done: false };
        return { value: undefined, done: true };
      },
    };
  }
}

// ── Model class ───────────────────────────────────────────────────────────────

class Model {
  constructor(data = {}) {
    Object.assign(this, data);
  }

  /** Persist this instance (PUT/upsert). */
  async save() {
    const { table, db, _schema } = this.constructor;
    const parts = Object.entries(_schema).map(([col, def]) => {
      const val = this[col] !== undefined ? this[col] : def.defaultValue;
      return `${col}: ${toLiteral(def.type, val)}`;
    });
    await db.query(`PUT ${table} { ${parts.join(', ')} }`);
  }

  /** Delete this instance by primary key. */
  async delete() {
    const { table, db, _schema } = this.constructor;
    const pkCol = Object.entries(_schema).find(([, d]) => d.primaryKey)?.[0];
    if (!pkCol) throw new Error('No primaryKey defined on this model');
    const pkDef = _schema[pkCol];
    await db.query(
      `DEL ${table} WHERE ${pkCol} = ${toLiteral(pkDef.type, this[pkCol])}`
    );
  }

  /** Update specific fields and persist. */
  async update(values) {
    Object.assign(this, values);
    const { table, db, _schema } = this.constructor;
    const pkCol = Object.entries(_schema).find(([, d]) => d.primaryKey)?.[0];
    const pkDef = _schema[pkCol];
    const parts = Object.entries(values).map(([k, v]) => {
      const type = (_schema[k] || {}).type || DataTypes.TEXT;
      return `${k}: ${toLiteral(type, v)}`;
    });
    await db.query(
      `SET ${table} { ${parts.join(', ')} } WHERE ${pkCol} = ${toLiteral(pkDef.type, this[pkCol])}`
    );
  }

  toJSON() {
    const out = {};
    for (const col of Object.keys(this.constructor._schema)) {
      out[col] = this[col];
    }
    return out;
  }

  toString() {
    const pkCol = Object.entries(this.constructor._schema)
      .find(([, d]) => d.primaryKey)?.[0];
    const pk = pkCol ? `${pkCol}=${this[pkCol]}` : '';
    return `[${this.constructor.name} ${pk}]`;
  }
}

// ── Model factory ─────────────────────────────────────────────────────────────

/**
 * Define a PulseDB ORM model.
 *
 * @param {string} tableName - PulseDB table name
 * @param {object} schema    - Field definitions keyed by column name
 * @param {object} options
 * @param {PulseDB} options.db - Connected PulseDB client
 * @returns {typeof Model}   - Model class with static query methods
 */
function defineModel(tableName, schema, { db }) {
  class DerivedModel extends Model {}

  DerivedModel.table   = tableName;
  DerivedModel.db      = db;
  DerivedModel._schema = schema;
  DerivedModel.name    = tableName.replace(/^\w/, c => c.toUpperCase());

  // ── Schema management ─────────────────────────────────────────────────────

  DerivedModel.createTable = async function() {
    const cols = Object.entries(schema).map(([col, def]) => {
      const pk = def.primaryKey ? ' PRIMARY KEY' : '';
      return `${col} ${def.type}${pk}`;
    });
    try {
      await db.query(`MAKE TABLE ${tableName} (${cols.join(', ')})`);
    } catch (e) {
      if (!e.message.includes('already exists')) throw e;
    }
  };

  DerivedModel.dropTable = async function() {
    await db.query(`DROP TABLE ${tableName}`);
  };

  DerivedModel.createIndex = async function(...columns) {
    for (const col of columns) {
      await db.query(`MAKE INDEX ON ${tableName} (${col})`);
    }
  };

  // ── Write ─────────────────────────────────────────────────────────────────

  DerivedModel.create = async function(data) {
    const instance = new DerivedModel(data);
    await instance.save();
    return instance;
  };

  DerivedModel.bulkCreate = async function(records, { transaction = true } = {}) {
    if (transaction) await db.query('BEGIN');
    try {
      const instances = [];
      for (const data of records) {
        const inst = new DerivedModel(data);
        await inst.save();
        instances.push(inst);
      }
      if (transaction) await db.query('COMMIT');
      return instances;
    } catch (e) {
      if (transaction) await db.query('ROLLBACK');
      throw e;
    }
  };

  // ── Read ──────────────────────────────────────────────────────────────────

  DerivedModel.findAll = async function({ where, orderBy, limit } = {}) {
    const stmt =
      `GET ${tableName}` +
      buildWhere(where, schema) +
      buildOrderBy(orderBy) +
      buildLimit(limit);
    const result = await db.query(stmt);
    return result.rows.map(row => DerivedModel._fromRow(result.columns, row));
  };

  DerivedModel.findOne = async function({ where, orderBy } = {}) {
    const rows = await DerivedModel.findAll({ where, orderBy, limit: 1 });
    return rows[0] || null;
  };

  DerivedModel.findByPk = async function(pk) {
    const pkCol = Object.entries(schema).find(([, d]) => d.primaryKey)?.[0];
    if (!pkCol) throw new Error('No primaryKey defined');
    const pkDef = schema[pkCol];
    const stmt = `GET ${tableName} WHERE ${pkCol} = ${toLiteral(pkDef.type, pk)}`;
    const result = await db.query(stmt);
    const row = result.rows[0];
    return row ? DerivedModel._fromRow(result.columns, row) : null;
  };

  DerivedModel.count = async function({ where } = {}) {
    const rows = await DerivedModel.findAll({ where });
    return rows.length;
  };

  // ── Update / Delete ───────────────────────────────────────────────────────

  DerivedModel.update = async function(values, { where } = {}) {
    const parts = Object.entries(values).map(([k, v]) => {
      const type = (schema[k] || {}).type || DataTypes.TEXT;
      return `${k}: ${toLiteral(type, v)}`;
    });
    const stmt = `SET ${tableName} { ${parts.join(', ')} }${buildWhere(where, schema)}`;
    await db.query(stmt);
  };

  DerivedModel.destroy = async function({ where } = {}) {
    await db.query(`DEL ${tableName}${buildWhere(where, schema)}`);
  };

  // ── Vector + fuzzy search ─────────────────────────────────────────────────

  DerivedModel.similar = async function(column, vector, { limit = 10 } = {}) {
    const vec = `[${vector.map(Number).join(', ')}]`;
    const stmt = `SIMILAR ${tableName} ON ${column} TO ${vec} LIMIT ${limit}`;
    const result = await db.query(stmt);
    return result.rows.map(row => DerivedModel._fromRow(result.columns, row));
  };

  DerivedModel.fuzzy = async function(column, pattern, { limit = 20 } = {}) {
    const escaped = pattern.replace(/"/g, '\\"');
    const stmt = `FIND ${tableName} WHERE ${column} ~ "${escaped}" LIMIT ${limit}`;
    const result = await db.query(stmt);
    return result.rows.map(row => DerivedModel._fromRow(result.columns, row));
  };

  // ── QuerySet ──────────────────────────────────────────────────────────────

  DerivedModel.where = function(conditions) {
    return new QuerySet(DerivedModel).where(conditions);
  };

  DerivedModel.orderBy = function(...cols) {
    return new QuerySet(DerivedModel).orderBy(...cols);
  };

  DerivedModel.limit = function(n) {
    return new QuerySet(DerivedModel).limit(n);
  };

  // ── Internal helpers ──────────────────────────────────────────────────────

  DerivedModel._fromRow = function(columns, values) {
    const data = {};
    columns.forEach((col, i) => { data[col] = values[i]; });
    return new DerivedModel(data);
  };

  return DerivedModel;
}

// ── Transaction helper ────────────────────────────────────────────────────────

async function withTransaction(db, fn) {
  await db.query('BEGIN');
  try {
    const result = await fn();
    await db.query('COMMIT');
    return result;
  } catch (e) {
    await db.query('ROLLBACK');
    throw e;
  }
}

// ── Exports ───────────────────────────────────────────────────────────────────

module.exports = { defineModel, DataTypes, withTransaction, QuerySet, Model };
